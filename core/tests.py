from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import (
    AeoEntry, AttentionItem, Automation, AuditLog, EmailCampaign, Integration,
    IntegrationCheck, Lead, MessageDelivery, Setting, WhatsAppTemplate, WorkflowRun,
)
from .tasks import execute_workflow, process_due_automations, send_campaign


class ApplicationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("owner@example.ie", password="Strong-Test-Password-123")
        self.client.login(username="owner@example.ie", password="Strong-Test-Password-123")

    def test_core_pages(self):
        for path in ["/", "/leads/", "/campaigns/", "/email-marketing/", "/whatsapp/", "/automations/",
                     "/integrations/", "/aeo/", "/analytics/", "/integration-health/", "/ai-orchestrator/",
                     "/api/dashboard/", "/up"]:
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_email_metrics_not_user_editable(self):
        response = self.client.get("/email-marketing/")
        self.assertNotContains(response, 'name="opens"')
        self.assertNotContains(response, 'name="failures"')

    def test_operational_records_can_be_created(self):
        self.client.post("/automations/", {"action": "create", "name": "Test flow", "trigger": "Lead created",
                                           "conditions": "consent confirmed", "actions": "Send email"})
        self.assertEqual(Automation.objects.filter(name="Test flow").count(), 1)


class ConsentTests(TestCase):
    """GDPR: an unsubscribe request must actually unsubscribe the lead."""

    def test_unsubscribe_exact_case(self):
        Lead.objects.create(first_name="Test", last_name="Lead", email="lead@example.ie", consent_at=timezone.now())
        response = self.client.post("/api/consent/unsubscribe", data='{"email":"lead@example.ie"}',
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        lead = Lead.objects.get(email="lead@example.ie")
        self.assertEqual(lead.status, "unsubscribed")
        self.assertIsNone(lead.consent_at)

    def test_unsubscribe_is_case_insensitive(self):
        Lead.objects.create(first_name="A", last_name="B", email="Mixed.Case@Example.ie", consent_at=timezone.now())
        response = self.client.post("/api/consent/unsubscribe", data='{"email":"mixed.case@example.ie"}',
                                    content_type="application/json")
        self.assertEqual(response.json()["leads_updated"], 1)
        lead = Lead.objects.get(email="Mixed.Case@Example.ie")
        self.assertEqual(lead.status, "unsubscribed")
        self.assertIsNone(lead.consent_at)

    def test_unsubscribe_reports_when_nothing_matched(self):
        response = self.client.post("/api/consent/unsubscribe", data='{"email":"nobody@example.ie"}',
                                    content_type="application/json")
        self.assertEqual(response.json()["leads_updated"], 0)


class PermissionTests(TestCase):
    """Only the owner may reach accounts, settings and the audit trail."""

    def setUp(self):
        self.staff = User.objects.create_user("staff@example.ie", password="Strong-Test-Password-123")
        self.owner = User.objects.create_superuser("owner@example.ie", password="Strong-Test-Password-123")

    def test_non_superuser_blocked_from_sensitive_modules(self):
        self.client.login(username="staff@example.ie", password="Strong-Test-Password-123")
        for slug in ["users", "audit", "settings"]:
            self.assertEqual(self.client.get(f"/{slug}/").status_code, 403, slug)

    def test_non_superuser_cannot_create_superuser(self):
        self.client.login(username="staff@example.ie", password="Strong-Test-Password-123")
        self.client.post("/users/", {"username": "attacker", "password": "PlainTextPassword123",
                                     "is_superuser": "True", "is_staff": "True"})
        self.assertFalse(User.objects.filter(username="attacker").exists())

    def test_owner_created_user_gets_no_rights_and_a_hashed_password(self):
        self.client.login(username="owner@example.ie", password="Strong-Test-Password-123")
        self.client.post("/users/", {"username": "colleague", "password": "PlainTextPassword123",
                                     "is_superuser": "True", "is_staff": "True", "email": "c@example.ie"})
        created = User.objects.get(username="colleague")
        self.assertFalse(created.is_superuser)
        self.assertFalse(created.is_staff)
        self.assertNotEqual(created.password, "PlainTextPassword123")
        self.assertTrue(created.check_password("PlainTextPassword123"))

    def test_last_owner_cannot_be_deleted(self):
        self.client.login(username="owner@example.ie", password="Strong-Test-Password-123")
        self.client.post("/users/", {"action": "delete", "id": self.owner.pk})
        self.assertTrue(User.objects.filter(pk=self.owner.pk).exists())

    def test_audit_log_is_read_only(self):
        self.client.login(username="owner@example.ie", password="Strong-Test-Password-123")
        entry = AuditLog.objects.create(event="system.important", new_values={"x": 1})
        self.client.post("/audit/", {"action": "delete", "id": entry.pk})
        self.assertTrue(AuditLog.objects.filter(pk=entry.pk).exists())

    def test_secret_settings_are_masked(self):
        self.client.login(username="owner@example.ie", password="Strong-Test-Password-123")
        Setting.objects.create(group="smtp", key="api_key", value="SUPER-SECRET-VALUE", is_secret=True)
        response = self.client.get("/settings/")
        self.assertNotContains(response, "SUPER-SECRET-VALUE")


class LoginRateLimitTests(TestCase):
    def test_repeated_failures_are_throttled(self):
        User.objects.create_user("owner@example.ie", password="Strong-Test-Password-123")
        statuses = [self.client.post("/login/", {"email": "owner@example.ie", "password": "wrong"}).status_code
                    for _ in range(8)]
        self.assertIn(429, statuses, "login should be rate limited after repeated failures")


class AeoTests(TestCase):
    def setUp(self):
        User.objects.create_superuser("owner@example.ie", password="Strong-Test-Password-123")
        self.client.login(username="owner@example.ie", password="Strong-Test-Password-123")

    def test_publish_sets_published_at(self):
        entry = AeoEntry.objects.create(question="q", answer="a", topic="t", status="review")
        response = self.client.post("/aeo/", {"action": "publish", "id": entry.pk})
        self.assertEqual(response.status_code, 302)
        entry.refresh_from_db()
        self.assertEqual(entry.status, "published")
        self.assertIsNotNone(entry.published_at)


class IntegrationTests(TestCase):
    def setUp(self):
        User.objects.create_superuser("owner@example.ie", password="Strong-Test-Password-123")
        self.client.login(username="owner@example.ie", password="Strong-Test-Password-123")
        for name, provider, category in [("PostgreSQL Database", "PostgreSQL", "Database"),
                                         ("Transactional Email", "SMTP", "Email"),
                                         ("WhatsApp Business", "Meta Cloud API", "WhatsApp"),
                                         ("Live AI", "OpenAI Responses API", "AI")]:
            Integration.objects.create(name=name, provider=provider, category=category)

    def test_health_check_updates_the_matching_integration_card(self):
        self.client.post("/integrations/", {"service": "database"})
        card = Integration.objects.get(category="Database")
        self.assertEqual(card.status, "connected")
        self.assertEqual(IntegrationCheck.objects.filter(service="database").first().integration, card)

    def test_smtp_and_openai_checks_link_to_their_cards(self):
        with patch("core.views.test_integration", return_value="ok"):
            self.client.post("/integrations/", {"service": "smtp"})
            self.client.post("/integrations/", {"service": "openai"})
        self.assertEqual(Integration.objects.get(category="Email").status, "connected")
        self.assertEqual(Integration.objects.get(category="AI").status, "connected")


class WorkflowEngineTests(TestCase):
    """The engine must actually run actions, not copy the list."""

    def setUp(self):
        self.lead = Lead.objects.create(first_name="Aoife", last_name="Murphy", email="aoife@example.ie",
                                        company="Celtic Retail", phone="0871234567", consent_at=timezone.now())

    def test_actions_are_executed_and_recorded_individually(self):
        automation = Automation.objects.create(name="Welcome", trigger="Lead created", conditions=["consent confirmed"],
                                               actions=["Send welcome email", "Assign lead score",
                                                        "Wait 20 hours", "Fly to the moon"], status="active")
        execute_workflow(automation.pk, {"lead_id": self.lead.pk})
        run = WorkflowRun.objects.get(automation=automation)
        outcomes = {r["action"]: r["status"] for r in run.actions_completed}
        self.assertEqual(outcomes["Send welcome email"], "completed")
        self.assertEqual(outcomes["Assign lead score"], "completed")
        self.assertEqual(outcomes["Wait 20 hours"], "skipped")
        self.assertEqual(outcomes["Fly to the moon"], "skipped")
        self.assertEqual(run.status, "completed")

    def test_email_action_actually_sends(self):
        automation = Automation.objects.create(name="Welcome", trigger="t", conditions=[],
                                               actions=["Send welcome email"], status="active")
        execute_workflow(automation.pk, {"lead_id": self.lead.pk})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["aoife@example.ie"])
        self.assertTrue(MessageDelivery.objects.filter(recipient="aoife@example.ie", status="sent").exists())

    def test_scoring_action_updates_the_lead(self):
        automation = Automation.objects.create(name="Score", trigger="t", conditions=[],
                                               actions=["Assign lead score"], status="active")
        execute_workflow(automation.pk, {"lead_id": self.lead.pk})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.score, 100)

    def test_conditions_block_execution(self):
        no_consent = Lead.objects.create(first_name="B", last_name="C", email="b@example.ie")
        automation = Automation.objects.create(name="Gated", trigger="t", conditions=["consent confirmed"],
                                               actions=["Send welcome email"], status="active")
        execute_workflow(automation.pk, {"lead_id": no_consent.pk})
        run = WorkflowRun.objects.get(automation=automation)
        self.assertEqual(run.status, "skipped")
        self.assertEqual(len(mail.outbox), 0)

    def test_failures_are_counted_as_failures(self):
        automation = Automation.objects.create(name="Broken", trigger="t", conditions=[],
                                               actions=["Send welcome email"], status="active")
        execute_workflow(automation.pk, {})  # no lead in payload
        automation.refresh_from_db()
        run = WorkflowRun.objects.get(automation=automation)
        self.assertEqual(run.status, "failed")
        self.assertEqual(automation.failures, 1)
        self.assertEqual(automation.successes, 0)


class SchedulerTests(TestCase):
    def test_only_due_automations_are_queued(self):
        due = Automation.objects.create(name="Due", trigger="t", conditions=[], actions=[], status="active",
                                        run_every_minutes=60, last_run_at=timezone.now() - timezone.timedelta(hours=2))
        Automation.objects.create(name="Not due", trigger="t", conditions=[], actions=[], status="active",
                                  run_every_minutes=60, last_run_at=timezone.now())
        Automation.objects.create(name="Paused", trigger="t", conditions=[], actions=[], status="paused")
        with patch("core.tasks.execute_workflow.delay") as queue:
            result = process_due_automations()
        self.assertEqual(result["queued"], 1)
        queue.assert_called_once_with(due.pk, {"source": "scheduler"})

    def test_scheduler_does_not_fabricate_success_counters(self):
        automation = Automation.objects.create(name="Idle", trigger="t", conditions=[], actions=[], status="active",
                                               last_run_at=timezone.now())
        with patch("core.tasks.execute_workflow.delay"):
            process_due_automations()
        automation.refresh_from_db()
        self.assertEqual(automation.runs, 0)
        self.assertEqual(automation.successes, 0)


class CampaignTests(TestCase):
    def test_retry_does_not_send_twice(self):
        Lead.objects.create(first_name="A", last_name="B", email="a@example.ie", consent_at=timezone.now())
        campaign = EmailCampaign.objects.create(name="C", subject="S", content="<p>hi</p>", segment="all")
        send_campaign(campaign.pk)
        send_campaign(campaign.pk)  # simulate a Celery retry
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(MessageDelivery.objects.filter(recipient="a@example.ie", status="sent").count(), 1)


@override_settings(WHATSAPP_APP_SECRET="test-secret", WHATSAPP_VERIFY_TOKEN="verify-me")
class WhatsAppWebhookTests(TestCase):
    def _signed(self, body):
        import hashlib, hmac
        return "sha256=" + hmac.new(b"test-secret", body.encode(), hashlib.sha256).hexdigest()

    def test_verification_requires_the_right_token(self):
        self.assertEqual(self.client.get("/api/webhooks/whatsapp?hub.verify_token=wrong").status_code, 403)
        ok = self.client.get("/api/webhooks/whatsapp?hub.verify_token=verify-me&hub.challenge=abc")
        self.assertEqual(ok.content, b"abc")

    def test_unsigned_post_is_rejected(self):
        response = self.client.post("/api/webhooks/whatsapp", data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 403)

    def test_signed_status_updates_the_delivery_and_template(self):
        template = WhatsAppTemplate.objects.create(name="order_update", category="utility", body="b",
                                                   status="approved", delivered=0, read_count=0)
        delivery = MessageDelivery.objects.create(channel="whatsapp", recipient="353871234567",
                                                  template=template.name, external_id="wamid.123", status="sent")
        body = ('{"entry":[{"changes":[{"value":{"statuses":['
                '{"id":"wamid.123","status":"delivered"}]}}]}]}')
        response = self.client.post("/api/webhooks/whatsapp", data=body, content_type="application/json",
                                    HTTP_X_HUB_SIGNATURE_256=self._signed(body))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 1)
        delivery.refresh_from_db(); template.refresh_from_db()
        self.assertEqual(delivery.status, "delivered")
        self.assertEqual(template.delivered, 1)


class ModuleListingTests(TestCase):
    """Search and pagination on the generic record tables."""

    def setUp(self):
        User.objects.create_superuser("owner@example.ie", password="Strong-Test-Password-123")
        self.client.login(username="owner@example.ie", password="Strong-Test-Password-123")
        for i in range(30):
            Lead.objects.create(first_name=f"Name{i:02d}", last_name="Bulk",
                                email=f"bulk{i:02d}@example.ie",
                                company="Findable Ltd" if i < 7 else "Other Ltd")

    def test_first_page_is_capped(self):
        response = self.client.get("/leads/")
        self.assertEqual(len(response.context["rows"]), 25)
        self.assertEqual(response.context["total_count"], 30)
        self.assertEqual(response.context["page_obj"].paginator.num_pages, 2)

    def test_second_page_returns_the_remainder(self):
        response = self.client.get("/leads/?page=2")
        self.assertEqual(len(response.context["rows"]), 5)
        self.assertEqual(response.context["page_obj"].number, 2)

    def test_search_filters_across_text_columns(self):
        response = self.client.get("/leads/?q=Findable")
        self.assertEqual(response.context["match_count"], 7)
        self.assertEqual(len(response.context["rows"]), 7)

    def test_search_matches_email_and_is_case_insensitive(self):
        self.assertEqual(self.client.get("/leads/?q=BULK07").context["match_count"], 1)

    def test_search_with_no_matches_is_empty_not_everything(self):
        response = self.client.get("/leads/?q=zzz-no-such-record")
        self.assertEqual(response.context["match_count"], 0)
        self.assertContains(response, "Nothing matches")

    def test_delete_returns_to_the_same_search_and_page(self):
        lead = Lead.objects.filter(company="Findable Ltd").first()
        response = self.client.post("/leads/?q=Findable&page=1", {"action": "delete", "id": lead.pk})
        self.assertEqual(response.status_code, 302)
        self.assertIn("q=Findable", response["Location"])
        self.assertFalse(Lead.objects.filter(pk=lead.pk).exists())

    def test_readonly_module_still_paginates(self):
        response = self.client.get("/audit/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["readonly"])
