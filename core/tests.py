from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from .alerts import action_token, resolve_token
from .analytics import (attribute, attribution_gap, channel_performance, cohorts, forecast,
                        lifetime_value)
from .models import (
    AeoEntry, AiDecision, AttentionItem, Automation, AuditLog, Campaign, ConsentEvent,
    ContentItem, Conversion, EmailCampaign, EngagementEvent, FinancialRecord, FxRate, Integration,
    IntegrationCheck, Lead, MessageDelivery, Organisation, Setting, SupportTicket,
    WhatsAppTemplate, WorkflowRun,
)
from .money import to_base
from .segments import resolve as resolve_segment
from .tenancy import assign, is_multi_entity
from .tracking import sign_target
from .tasks import (attribute_conversions, build_digest, dispatch_critical_alerts,
                    escalate_attention, execute_workflow, expire_stale_consent,
                    flag_dormant_leads, parse_wait, process_bounces, process_due_automations,
                    review_campaign_engagement, roll_up_attribution, send_campaign,
                    send_owner_digest, send_scheduled_campaigns, send_weekly_review,
                    sweep_overdue_attention)


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
        with patch("core.tasks.execute_workflow.apply_async"):
            execute_workflow(automation.pk, {"lead_id": self.lead.pk})
        run = WorkflowRun.objects.get(automation=automation)
        outcomes = {r["action"]: r["status"] for r in run.actions_completed}
        self.assertEqual(outcomes["Send welcome email"], "completed")
        self.assertEqual(outcomes["Assign lead score"], "completed")
        # The wait now pauses the sequence rather than being ignored.
        self.assertEqual(outcomes["Wait 20 hours"], "waiting")
        self.assertNotIn("Fly to the moon", outcomes)
        self.assertEqual(run.status, "waiting")

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


class RecordEditTests(TestCase):
    """Update, bulk actions and the audit trail they produce."""

    def setUp(self):
        self.owner = User.objects.create_superuser("owner@example.ie", password="Strong-Test-Password-123")
        self.client.login(username="owner@example.ie", password="Strong-Test-Password-123")
        self.lead = Lead.objects.create(first_name="Aoife", last_name="Murphy", email="aoife@example.ie",
                                        company="Celtic Retail", market="Ireland", source="Website",
                                        status="new", score=40)

    def test_edit_form_is_prefilled(self):
        response = self.client.get(f"/leads/?edit={self.lead.pk}")
        self.assertEqual(response.context["editing"].pk, self.lead.pk)
        values = {i["field"].name: i["value"] for i in response.context["form_fields"]}
        self.assertEqual(values["first_name"], "Aoife")
        self.assertEqual(values["company"], "Celtic Retail")

    def test_update_changes_the_record(self):
        self.client.post("/leads/", {"action": "update", "id": self.lead.pk, "first_name": "Aoife",
                                     "last_name": "Murphy", "email": "aoife@example.ie",
                                     "company": "Celtic Wholesale", "market": "Ireland",
                                     "source": "Website", "status": "qualified", "score": 90, "phone": ""})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.company, "Celtic Wholesale")
        self.assertEqual(self.lead.status, "qualified")
        self.assertEqual(self.lead.score, 90)

    def test_update_writes_only_the_changed_fields_to_the_audit_log(self):
        self.client.post("/leads/", {"action": "update", "id": self.lead.pk, "first_name": "Aoife",
                                     "last_name": "Murphy", "email": "aoife@example.ie",
                                     "company": "Celtic Wholesale", "market": "Ireland",
                                     "source": "Website", "status": "new", "score": 40, "phone": ""})
        entry = AuditLog.objects.filter(event="lead.updated").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.old_values["company"], "Celtic Retail")
        self.assertEqual(entry.new_values["company"], "Celtic Wholesale")
        self.assertNotIn("first_name", entry.new_values)

    def test_invalid_update_is_rejected_and_record_untouched(self):
        self.client.post("/leads/", {"action": "update", "id": self.lead.pk, "first_name": "Aoife",
                                     "last_name": "Murphy", "email": "not-an-email",
                                     "company": "X", "market": "Ireland", "source": "Website",
                                     "status": "new", "score": 40, "phone": ""})
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.email, "aoife@example.ie")

    def test_editing_a_user_cannot_grant_rights(self):
        colleague = User.objects.create_user("colleague@example.ie", password="Strong-Test-Password-123")
        self.client.post("/users/", {"action": "update", "id": colleague.pk, "username": "colleague@example.ie",
                                     "email": "c@example.ie", "first_name": "Col", "last_name": "League",
                                     "is_superuser": "True", "is_staff": "True"})
        colleague.refresh_from_db()
        self.assertEqual(colleague.email, "c@example.ie")
        self.assertFalse(colleague.is_superuser)
        self.assertFalse(colleague.is_staff)

    def test_secret_setting_keeps_its_value_when_left_blank(self):
        setting = Setting.objects.create(group="smtp", key="api_key", value="KEEP-ME", is_secret=True)
        self.client.post("/settings/", {"action": "update", "id": setting.pk, "group": "smtp",
                                        "key": "api_key", "value": "", "is_secret": "on"})
        setting.refresh_from_db()
        self.assertEqual(setting.value, "KEEP-ME")

    def test_bulk_delete_removes_the_selection_only(self):
        keep = Lead.objects.create(first_name="Keep", last_name="Me", email="keep@example.ie")
        gone = [Lead.objects.create(first_name=f"Gone{i}", last_name="X", email=f"gone{i}@example.ie").pk
                for i in range(3)]
        self.client.post("/leads/", {"action": "bulk-delete", "selected": gone})
        self.assertFalse(Lead.objects.filter(pk__in=gone).exists())
        self.assertTrue(Lead.objects.filter(pk=keep.pk).exists())
        self.assertTrue(AuditLog.objects.filter(event="lead.bulk_deleted").exists())

    def test_bulk_unsubscribe_clears_consent(self):
        a = Lead.objects.create(first_name="A", last_name="A", email="a@example.ie", consent_at=timezone.now())
        b = Lead.objects.create(first_name="B", last_name="B", email="b@example.ie", consent_at=timezone.now())
        self.client.post("/leads/", {"action": "bulk-unsubscribe", "selected": [a.pk, b.pk]})
        for lead in (a, b):
            lead.refresh_from_db()
            self.assertEqual(lead.status, "unsubscribed")
            self.assertIsNone(lead.consent_at)

    def test_readonly_module_rejects_updates(self):
        entry = AuditLog.objects.create(event="system.note", new_values={"a": 1})
        self.client.post("/audit/", {"action": "update", "id": entry.pk, "event": "tampered"})
        entry.refresh_from_db()
        self.assertEqual(entry.event, "system.note")


class EventTriggerTests(TestCase):
    """Automation.trigger_event must actually fire workflows on real events."""

    def _flow(self, event, actions=None, status="active"):
        return Automation.objects.create(
            name=f"On {event}", trigger=event, trigger_event=event, conditions=[],
            actions=actions or ["Assign lead score"], status=status)

    def test_creating_a_lead_fires_its_workflow(self):
        flow = self._flow("lead.created")
        with self.captureOnCommitCallbacks(execute=True):
            Lead.objects.create(first_name="Niamh", last_name="Byrne", email="niamh@example.ie")
        run = WorkflowRun.objects.filter(automation=flow).first()
        self.assertIsNotNone(run, "lead.created should have queued the workflow")
        self.assertEqual(run.trigger_payload["event"], "lead.created")

    def test_workflows_listening_for_other_events_do_not_fire(self):
        other = self._flow("campaign.sent")
        with self.captureOnCommitCallbacks(execute=True):
            Lead.objects.create(first_name="A", last_name="B", email="ab@example.ie")
        self.assertFalse(WorkflowRun.objects.filter(automation=other).exists())

    def test_paused_workflows_do_not_fire(self):
        paused = self._flow("lead.created", status="paused")
        with self.captureOnCommitCallbacks(execute=True):
            Lead.objects.create(first_name="C", last_name="D", email="cd@example.ie")
        self.assertFalse(WorkflowRun.objects.filter(automation=paused).exists())

    def test_consent_fires_only_on_the_transition(self):
        flow = self._flow("lead.consented")
        with self.captureOnCommitCallbacks(execute=True):
            lead = Lead.objects.create(first_name="E", last_name="F", email="ef@example.ie")
        self.assertEqual(WorkflowRun.objects.filter(automation=flow).count(), 0)

        with self.captureOnCommitCallbacks(execute=True):
            lead.consent_at = timezone.now()
            lead.save()
        self.assertEqual(WorkflowRun.objects.filter(automation=flow).count(), 1)

        with self.captureOnCommitCallbacks(execute=True):
            lead.company = "Changed Ltd"
            lead.save()
        self.assertEqual(WorkflowRun.objects.filter(automation=flow).count(), 1,
                         "editing an already-consented lead must not raise the event again")

    def test_campaign_sent_fires_once(self):
        flow = self._flow("campaign.sent", actions=["Create owner summary"])
        campaign = EmailCampaign.objects.create(name="C", subject="S", content="x", segment="all")
        with self.captureOnCommitCallbacks(execute=True):
            campaign.status = "sent"
            campaign.save()
        self.assertEqual(WorkflowRun.objects.filter(automation=flow).count(), 1)
        with self.captureOnCommitCallbacks(execute=True):
            campaign.recipients = 5
            campaign.save()
        self.assertEqual(WorkflowRun.objects.filter(automation=flow).count(), 1)

    def test_failed_delivery_fires_its_workflow(self):
        flow = self._flow("delivery.failed", actions=["Create owner summary"])
        with self.captureOnCommitCallbacks(execute=True):
            MessageDelivery.objects.create(channel="email", recipient="x@example.ie",
                                           template="t", status="failed", error="boom")
        self.assertEqual(WorkflowRun.objects.filter(automation=flow).count(), 1)

    def test_event_loop_is_bounded(self):
        """A workflow whose email fails raises delivery.failed; it must not recurse forever."""
        self._flow("delivery.failed", actions=["Send welcome email"])
        lead = Lead.objects.create(first_name="G", last_name="H", email="gh@example.ie")
        with self.captureOnCommitCallbacks(execute=True):
            MessageDelivery.objects.create(channel="email", recipient=lead.email,
                                           template="t", status="failed", error="boom")
        self.assertLess(WorkflowRun.objects.count(), 10)


class OverdueSweepTests(TestCase):
    def test_overdue_items_raise_the_event_once(self):
        flow = Automation.objects.create(name="Escalate", trigger="overdue",
                                         trigger_event="attention.overdue", conditions=[],
                                         actions=["Create owner summary"], status="active")
        item = AttentionItem.objects.create(severity="critical", category="Governance",
                                            title="Late", confidence=90, source="Test",
                                            recommendation="Act",
                                            due_at=timezone.now() - timezone.timedelta(hours=2))
        with self.captureOnCommitCallbacks(execute=True):
            result = sweep_overdue_attention()
        self.assertEqual(result["overdue"], 1)
        item.refresh_from_db()
        self.assertIsNotNone(item.overdue_notified_at)

        with self.captureOnCommitCallbacks(execute=True):
            again = sweep_overdue_attention()
        self.assertEqual(again["overdue"], 0, "the same item must not be reported twice")
        self.assertEqual(WorkflowRun.objects.filter(automation=flow).count(), 1)

    def test_items_still_in_time_are_untouched(self):
        AttentionItem.objects.create(severity="low", category="X", title="Future", confidence=50,
                                     source="Test", recommendation="Wait",
                                     due_at=timezone.now() + timezone.timedelta(days=1))
        self.assertEqual(sweep_overdue_attention()["overdue"], 0)


class OwnerDigestTests(TestCase):
    def setUp(self):
        AttentionItem.objects.create(severity="critical", category="Governance / Ethical Alert",
                                     title="Data privacy compliance risk", confidence=92,
                                     source="Compliance Engine", recommendation="Review immediately")
        AttentionItem.objects.create(severity="low", category="Auto-Executed", title="Backup done",
                                     confidence=100, source="System", recommendation="None",
                                     due_at=timezone.now() - timezone.timedelta(hours=3))
        Lead.objects.create(first_name="New", last_name="Lead", email="new@example.ie")

    def test_digest_collects_the_right_figures(self):
        digest = build_digest()
        self.assertEqual(len(digest["critical"]), 1)
        self.assertEqual(len(digest["overdue"]), 1)
        self.assertEqual(digest["new_leads"], 1)
        self.assertEqual(digest["open_count"], 2)

    def test_digest_email_is_sent_and_readable(self):
        result = send_owner_digest()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertIn("critical", message.subject)
        self.assertIn("Data privacy compliance risk", message.body)
        self.assertTrue(message.alternatives, "digest should carry an HTML part")
        self.assertEqual(result["critical"], 1)
        self.assertTrue(AuditLog.objects.filter(event="digest.sent").exists())


class SegmentTests(TestCase):
    """The segment field must select the audience, not decorate the form."""

    def setUp(self):
        now = timezone.now()
        self.wholesale = Lead.objects.create(first_name="W", last_name="One", email="w1@example.ie",
                                             company="Celtic Retail", market="Ireland",
                                             source="Website", status="qualified", score=80,
                                             consent_at=now)
        self.retail = Lead.objects.create(first_name="R", last_name="Two", email="r2@example.ie",
                                          company="", market="Ireland", source="Instagram",
                                          status="new", score=30, consent_at=now)
        self.italy = Lead.objects.create(first_name="I", last_name="Three", email="i3@example.it",
                                         company="Verde Moda", market="Italy", source="Website",
                                         status="new", score=60, consent_at=now)
        # Never contactable: no consent.
        Lead.objects.create(first_name="N", last_name="Four", email="n4@example.ie")

    def test_blank_and_all_mean_every_consented_lead(self):
        for value in ["", "all", "Consented leads"]:
            queryset, _, unknown = resolve_segment(value)
            self.assertEqual(queryset.count(), 3, value)
            self.assertEqual(unknown, [])

    def test_no_segment_can_reach_a_lead_without_consent(self):
        queryset, _, _ = resolve_segment("all")
        self.assertNotIn("n4@example.ie", [l.email for l in queryset])

    def test_wholesale_and_retail(self):
        self.assertEqual(resolve_segment("wholesale")[0].count(), 2)
        self.assertEqual(resolve_segment("retail")[0].count(), 1)

    def test_field_and_score_rules_combine(self):
        self.assertEqual(resolve_segment("market:Ireland")[0].count(), 2)
        self.assertEqual(resolve_segment("status:qualified")[0].count(), 1)
        self.assertEqual(resolve_segment("score>=70")[0].count(), 1)
        combined = resolve_segment("market:Ireland, wholesale")[0]
        self.assertEqual(combined.count(), 1)
        self.assertEqual(combined.first().email, "w1@example.ie")

    def test_unknown_segment_sends_to_nobody(self):
        queryset, _, unknown = resolve_segment("purple squirrels")
        self.assertEqual(queryset.count(), 0)
        self.assertEqual(unknown, ["purple squirrels"])

    def test_campaign_send_uses_the_segment(self):
        campaign = EmailCampaign.objects.create(name="Wholesale only", subject="S",
                                                content="<p>hi</p>", segment="wholesale")
        result = send_campaign(campaign.pk)
        self.assertEqual(result["sent"], 2)
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["i3@example.it", "w1@example.ie"])

    def test_unrecognised_segment_sends_nothing(self):
        campaign = EmailCampaign.objects.create(name="Bad", subject="S", content="x",
                                                segment="not a real segment")
        send_campaign(campaign.pk)
        self.assertEqual(len(mail.outbox), 0)


class ScheduledCampaignTests(TestCase):
    def setUp(self):
        Lead.objects.create(first_name="A", last_name="B", email="a@example.ie",
                            consent_at=timezone.now())

    def test_due_campaign_is_queued(self):
        due = EmailCampaign.objects.create(name="Due", subject="S", content="x", segment="all",
                                           status="scheduled",
                                           scheduled_at=timezone.now() - timezone.timedelta(minutes=1))
        result = send_scheduled_campaigns()
        self.assertEqual(result["queued"], 1)
        self.assertIn(due.pk, result["ids"])

    def test_future_campaign_is_left_alone(self):
        EmailCampaign.objects.create(name="Later", subject="S", content="x", segment="all",
                                     status="scheduled",
                                     scheduled_at=timezone.now() + timezone.timedelta(hours=3))
        self.assertEqual(send_scheduled_campaigns()["queued"], 0)

    def test_drafts_are_never_auto_sent(self):
        EmailCampaign.objects.create(name="Draft", subject="S", content="x", segment="all",
                                     status="draft",
                                     scheduled_at=timezone.now() - timezone.timedelta(hours=1))
        self.assertEqual(send_scheduled_campaigns()["queued"], 0)


class WaitStepTests(TestCase):
    """Wait steps must become real delays, not be skipped."""

    def setUp(self):
        self.lead = Lead.objects.create(first_name="D", last_name="Rip", email="drip@example.ie",
                                        company="Celtic Retail", phone="0871234567",
                                        consent_at=timezone.now())

    def test_wait_is_parsed_into_seconds(self):
        self.assertEqual(parse_wait("Wait 20 hours"), 72000)
        self.assertEqual(parse_wait("Wait 30 minutes"), 1800)
        self.assertEqual(parse_wait("Wait 2 days"), 172800)
        self.assertIsNone(parse_wait("Send welcome email"))

    def test_workflow_pauses_and_schedules_the_remainder(self):
        automation = Automation.objects.create(
            name="Basket recovery", trigger="t", conditions=[],
            actions=["Send recovery email", "Wait 20 hours", "Assign lead score"], status="active")
        with patch("core.tasks.execute_workflow.apply_async") as later:
            execute_workflow(automation.pk, {"lead_id": self.lead.pk})
        run = WorkflowRun.objects.get(automation=automation)
        self.assertEqual(run.status, "waiting")
        statuses = [r["status"] for r in run.actions_completed]
        self.assertEqual(statuses, ["completed", "waiting"])
        later.assert_called_once()
        args, kwargs = later.call_args
        self.assertEqual(args[0], (automation.pk, {"lead_id": self.lead.pk}, 2))
        self.assertEqual(kwargs["countdown"], 72000)

    def test_resuming_runs_only_the_remaining_steps(self):
        automation = Automation.objects.create(
            name="Basket recovery", trigger="t", conditions=[],
            actions=["Send recovery email", "Wait 20 hours", "Assign lead score"], status="active")
        execute_workflow(automation.pk, {"lead_id": self.lead.pk}, 2)
        run = WorkflowRun.objects.filter(automation=automation).latest("started_at")
        self.assertEqual(run.status, "completed")
        self.assertEqual([r["action"] for r in run.actions_completed], ["Assign lead score"])
        self.assertEqual(len(mail.outbox), 0, "the earlier email must not be resent")

    def test_a_trailing_wait_ends_the_sequence(self):
        automation = Automation.objects.create(name="Trailing", trigger="t", conditions=[],
                                               actions=["Assign lead score", "Wait 1 hour"],
                                               status="active")
        with patch("core.tasks.execute_workflow.apply_async") as later:
            execute_workflow(automation.pk, {"lead_id": self.lead.pk})
        later.assert_not_called()
        run = WorkflowRun.objects.get(automation=automation)
        self.assertEqual(run.status, "completed")


@override_settings(BOUNCE_LIMIT=3, CONSENT_EXPIRY_MONTHS=24, DORMANT_MONTHS=6)
class DeliverabilityTests(TestCase):
    """Repeated hard failures must stop the sending, to protect sender reputation."""

    def setUp(self):
        self.lead = Lead.objects.create(first_name="B", last_name="Ounce", email="bounce@example.ie",
                                        consent_at=timezone.now())

    def _fail(self, times, email="bounce@example.ie"):
        for _ in range(times):
            MessageDelivery.objects.create(channel="email", recipient=email, template="t",
                                           status="failed", error="550 mailbox unavailable")

    def test_below_the_limit_nothing_happens(self):
        self._fail(2)
        self.assertEqual(process_bounces()["unsubscribed"], 0)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "new")

    def test_at_the_limit_the_lead_is_unsubscribed(self):
        self._fail(3)
        with self.captureOnCommitCallbacks(execute=True):
            result = process_bounces()
        self.assertEqual(result["unsubscribed"], 1)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "unsubscribed")
        self.assertIsNone(self.lead.consent_at)
        self.assertTrue(ConsentEvent.objects.filter(email="bounce@example.ie").exists())
        self.assertTrue(AttentionItem.objects.filter(category="Deliverability").exists())

    def test_it_does_not_run_twice_on_the_same_lead(self):
        self._fail(3)
        with self.captureOnCommitCallbacks(execute=True):
            process_bounces()
            second = process_bounces()
        self.assertEqual(second["unsubscribed"], 0)

    def test_bounce_raises_its_event(self):
        Automation.objects.create(name="On bounce", trigger="bounce", trigger_event="lead.bounced",
                                  conditions=[], actions=["Create owner summary"], status="active")
        self._fail(3)
        with self.captureOnCommitCallbacks(execute=True):
            process_bounces()
        self.assertTrue(WorkflowRun.objects.filter(automation__name="On bounce").exists())


@override_settings(CONSENT_EXPIRY_MONTHS=24)
class ConsentExpiryTests(TestCase):
    def test_stale_consent_lapses(self):
        old = Lead.objects.create(first_name="O", last_name="Ld", email="old@example.ie",
                                  consent_at=timezone.now() - timezone.timedelta(days=800))
        fresh = Lead.objects.create(first_name="F", last_name="Resh", email="fresh@example.ie",
                                    consent_at=timezone.now() - timezone.timedelta(days=30))
        result = expire_stale_consent()
        self.assertEqual(result["expired"], 1)
        old.refresh_from_db(); fresh.refresh_from_db()
        self.assertEqual(old.status, "consent-expired")
        self.assertIsNone(old.consent_at)
        self.assertIsNotNone(fresh.consent_at, "recent consent must be left alone")
        self.assertTrue(AttentionItem.objects.filter(category="Governance / Ethical Alert").exists())

    def test_expired_leads_leave_the_sendable_audience(self):
        Lead.objects.create(first_name="O", last_name="Ld", email="old@example.ie",
                            consent_at=timezone.now() - timezone.timedelta(days=800))
        self.assertEqual(resolve_segment("all")[0].count(), 1)
        expire_stale_consent()
        self.assertEqual(resolve_segment("all")[0].count(), 0)


@override_settings(DORMANT_MONTHS=6)
class DormantLeadTests(TestCase):
    def test_quiet_leads_raise_the_event(self):
        Automation.objects.create(name="Win back", trigger="dormant", trigger_event="lead.dormant",
                                  conditions=[], actions=["Create owner summary"], status="active")
        quiet = Lead.objects.create(first_name="Q", last_name="Uiet", email="quiet@example.ie",
                                    consent_at=timezone.now())
        Lead.objects.filter(pk=quiet.pk).update(updated_at=timezone.now() - timezone.timedelta(days=400))
        with self.captureOnCommitCallbacks(execute=True):
            result = flag_dormant_leads()
        self.assertEqual(result["dormant"], 1)
        self.assertTrue(WorkflowRun.objects.filter(automation__name="Win back").exists())

    def test_recently_contacted_leads_are_not_dormant(self):
        lead = Lead.objects.create(first_name="A", last_name="Ctive", email="active@example.ie",
                                   consent_at=timezone.now())
        Lead.objects.filter(pk=lead.pk).update(updated_at=timezone.now() - timezone.timedelta(days=400))
        MessageDelivery.objects.create(channel="email", recipient="active@example.ie",
                                       template="t", status="sent")
        self.assertEqual(flag_dormant_leads()["dormant"], 0)


class CsvExportTests(TestCase):
    def setUp(self):
        User.objects.create_superuser("owner@example.ie", password="Strong-Test-Password-123")
        self.client.login(username="owner@example.ie", password="Strong-Test-Password-123")
        Lead.objects.create(first_name="Aoife", last_name="Murphy", email="aoife@example.ie",
                            company="Celtic Retail")
        Lead.objects.create(first_name="James", last_name="Kelly", email="james@example.com",
                            company="Heritage Outfitters")

    def test_export_returns_csv_with_a_header_and_every_row(self):
        response = self.client.get("/leads/?export=csv")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment; filename=", response["Content-Disposition"])
        lines = response.content.decode().strip().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("email", lines[0].lower())
        self.assertIn("aoife@example.ie", response.content.decode())

    def test_export_honours_the_active_search(self):
        response = self.client.get("/leads/?q=Celtic&export=csv")
        body = response.content.decode()
        self.assertIn("aoife@example.ie", body)
        self.assertNotIn("james@example.com", body)

    def test_export_is_audited(self):
        self.client.get("/leads/?export=csv")
        self.assertTrue(AuditLog.objects.filter(event="lead.exported").exists())

    def test_secret_settings_are_masked_in_export(self):
        Setting.objects.create(group="smtp", key="api_key", value="SUPER-SECRET-VALUE", is_secret=True)
        body = self.client.get("/settings/?export=csv").content.decode()
        self.assertNotIn("SUPER-SECRET-VALUE", body)
        self.assertIn("<hidden>", body)


class CsvImportTests(TestCase):
    def setUp(self):
        User.objects.create_superuser("owner@example.ie", password="Strong-Test-Password-123")
        self.client.login(username="owner@example.ie", password="Strong-Test-Password-123")

    def _upload(self, text, dry_run=False):
        payload = {"action": "import", "file": SimpleUploadedFile("leads.csv", text.encode("utf-8"),
                                                                  content_type="text/csv")}
        if dry_run:
            payload["dry_run"] = "on"
        return self.client.post("/leads/", payload)

    def test_dry_run_writes_nothing(self):
        self._upload("first_name,last_name,email\nNiamh,Byrne,niamh@example.ie\n", dry_run=True)
        self.assertEqual(Lead.objects.count(), 0)

    def test_import_creates_leads(self):
        self._upload("first_name,last_name,email,company\nNiamh,Byrne,niamh@example.ie,Byrne Ltd\n")
        lead = Lead.objects.get(email="niamh@example.ie")
        self.assertEqual(lead.first_name, "Niamh")
        self.assertEqual(lead.company, "Byrne Ltd")
        self.assertTrue(AuditLog.objects.filter(event="leads.imported").exists())

    def test_existing_leads_are_updated_by_email_not_duplicated(self):
        Lead.objects.create(first_name="Old", last_name="Name", email="niamh@example.ie")
        self._upload("first_name,last_name,email\nNiamh,Byrne,NIAMH@example.ie\n")
        self.assertEqual(Lead.objects.filter(email__iexact="niamh@example.ie").count(), 1)
        self.assertEqual(Lead.objects.get(email__iexact="niamh@example.ie").first_name, "Niamh")

    def test_rows_without_a_name_are_reported_not_imported(self):
        self._upload("first_name,last_name,email\n,,nameless@example.ie\n")
        self.assertFalse(Lead.objects.filter(email="nameless@example.ie").exists())

    def test_a_bad_score_does_not_import_the_row(self):
        self._upload("first_name,last_name,email,score\nA,B,bad@example.ie,not-a-number\n")
        self.assertFalse(Lead.objects.filter(email="bad@example.ie").exists())

    def test_a_file_without_an_email_column_is_rejected(self):
        self._upload("name,phone\nSomebody,0871234567\n")
        self.assertEqual(Lead.objects.count(), 0)

    def test_import_is_only_offered_on_leads(self):
        self.assertTrue(self.client.get("/leads/").context["can_import"])
        self.assertFalse(self.client.get("/campaigns/").context["can_import"])


class DashboardSplitTests(TestCase):
    """`/` is the owner overview; `/command-center/` is the operational console.

    The two were the same view. Merging them meant the owner had to read an
    operations console to answer "how is the business doing".
    """

    def setUp(self):
        User.objects.create_superuser("owner@example.ie", password="Strong-Test-Password-123")
        self.client.login(username="owner@example.ie", password="Strong-Test-Password-123")

    def test_both_dashboards_render_from_their_own_template(self):
        executive = self.client.get("/")
        command = self.client.get("/command-center/")
        self.assertEqual(executive.status_code, 200)
        self.assertEqual(command.status_code, 200)
        self.assertTemplateUsed(executive, "executive_dashboard.html")
        self.assertTemplateUsed(command, "dashboard.html")

    def test_disagreeing_totals_are_reported_not_hidden(self):
        Lead.objects.create(first_name="A", last_name="B", email="a@example.ie")
        FinancialRecord.objects.create(market="Ireland", system="Email Marketing",
                                       campaign="C", channel="Email", revenue=1000,
                                       cost=250, leads=42, customers=7)
        response = self.client.get("/")
        self.assertEqual(len(response.context["reconciliations"]), 1)
        self.assertContains(response, "DATA RECONCILIATION")

    def test_no_warning_when_the_figures_agree(self):
        Lead.objects.create(first_name="A", last_name="B", email="a@example.ie")
        FinancialRecord.objects.create(market="Ireland", system="Email Marketing",
                                       campaign="C", channel="Email", revenue=1000,
                                       cost=250, leads=1, customers=1)
        response = self.client.get("/")
        self.assertEqual(response.context["reconciliations"], [])
        self.assertNotContains(response, "DATA RECONCILIATION")


class SystemOwnedFieldTests(TestCase):
    """Derived outcomes must not be settable through generic CRUD.

    A hand-typed revenue or SEO score is indistinguishable from a measured one
    once it reaches analytics, so these fields are removed from the form.
    """

    def setUp(self):
        User.objects.create_superuser("owner@example.ie", password="Strong-Test-Password-123")
        self.client.login(username="owner@example.ie", password="Strong-Test-Password-123")

    def _form_field_names(self, slug):
        return {i["field"].name for i in self.client.get(f"/{slug}/").context["form_fields"]}

    def test_campaign_form_offers_only_the_descriptive_fields(self):
        self.assertEqual(self._form_field_names("campaigns"), {"name", "channel", "status"})

    def test_content_form_excludes_the_derived_scores(self):
        names = self._form_field_names("content")
        self.assertNotIn("seo_score", names)
        self.assertNotIn("ai_confidence", names)
        self.assertIn("title", names)

    def test_posted_revenue_is_ignored_when_creating_a_campaign(self):
        self.client.post("/campaigns/", {"action": "create", "name": "Injected", "channel": "Email",
                                         "status": "active", "revenue": "999999", "cost": "1",
                                         "audience_size": "500"})
        campaign = Campaign.objects.get(name="Injected")
        self.assertEqual(campaign.revenue, 0)
        self.assertEqual(campaign.cost, 0)
        self.assertEqual(campaign.audience_size, 0)


class ReadOnlyModuleTests(TestCase):
    """Finance and AI Intelligence are reports, not editable record sets."""

    def setUp(self):
        User.objects.create_superuser("owner@example.ie", password="Strong-Test-Password-123")
        self.client.login(username="owner@example.ie", password="Strong-Test-Password-123")

    def test_finance_and_intelligence_offer_no_form(self):
        for slug in ["finance", "intelligence"]:
            response = self.client.get(f"/{slug}/")
            self.assertTrue(response.context["readonly"], slug)
            self.assertEqual(list(response.context["fields"]), [], slug)

    def test_finance_rejects_a_posted_record(self):
        self.client.post("/finance/", {"action": "create", "market": "Ireland",
                                       "system": "Email Marketing", "campaign": "Invented",
                                       "channel": "Email", "revenue": "50000", "cost": "10"})
        self.assertEqual(FinancialRecord.objects.count(), 0)

    def test_intelligence_rejects_a_posted_decision(self):
        self.client.post("/intelligence/", {"action": "create", "decision_id": "ER-FAKE",
                                            "engine": "Made up", "title": "Invented",
                                            "recommendation": "x", "confidence": "99",
                                            "impact": "high", "risk_score": "1",
                                            "governance_level": "review"})
        self.assertEqual(AiDecision.objects.count(), 0)

    def test_audit_no_longer_borrows_the_analytics_report(self):
        response = self.client.get("/audit/")
        self.assertContains(response, "AUDIT LOGS")
        self.assertNotContains(response, "TOTAL REVENUE")

    def test_analytics_still_shows_its_own_totals(self):
        self.assertContains(self.client.get("/analytics/"), "TOTAL REVENUE")


class SettingsModuleTests(TestCase):
    """Approved keys can be changed; arbitrary new keys cannot be invented."""

    def setUp(self):
        User.objects.create_superuser("owner@example.ie", password="Strong-Test-Password-123")
        self.client.login(username="owner@example.ie", password="Strong-Test-Password-123")
        self.setting = Setting.objects.create(group="brand", key="company_name",
                                              value="Emerald Rozalia Limited")

    def test_no_create_panel_is_offered(self):
        self.assertEqual(self.client.get("/settings/").context["form_fields"], [])

    def test_editing_an_approved_key_offers_only_its_value(self):
        response = self.client.get(f"/settings/?edit={self.setting.pk}")
        self.assertEqual([i["field"].name for i in response.context["form_fields"]], ["value"])

    def test_a_new_key_cannot_be_created(self):
        self.client.post("/settings/", {"action": "create", "group": "rogue",
                                        "key": "backdoor", "value": "yes"})
        self.assertFalse(Setting.objects.filter(key="backdoor").exists())
        self.assertEqual(Setting.objects.count(), 1)

    def test_an_existing_value_can_still_be_updated(self):
        self.client.post("/settings/", {"action": "update", "id": self.setting.pk,
                                        "value": "Emerald Rozalia Ltd"})
        self.setting.refresh_from_db()
        self.assertEqual(self.setting.value, "Emerald Rozalia Ltd")


class SupportTicketTests(TestCase):
    """Ticket references are generated server-side and the creation is audited."""

    def setUp(self):
        User.objects.create_superuser("owner@example.ie", password="Strong-Test-Password-123")
        self.client.login(username="owner@example.ie", password="Strong-Test-Password-123")

    def _create(self, **extra):
        payload = {"action": "create", "subject": "Printer offline", "category": "Hardware",
                   "priority": "high", "description": "The label printer stopped responding.",
                   "status": "open"}
        payload.update(extra)
        return self.client.post("/support/", payload)

    def test_reference_is_generated_and_audited(self):
        self._create()
        ticket = SupportTicket.objects.get(subject="Printer offline")
        self.assertTrue(ticket.reference.startswith("ER-"), ticket.reference)
        self.assertTrue(AuditLog.objects.filter(event="support_ticket.created").exists())

    def test_a_posted_reference_is_ignored(self):
        self._create(reference="ER-CHOSEN-BY-HAND")
        ticket = SupportTicket.objects.get(subject="Printer offline")
        self.assertNotEqual(ticket.reference, "ER-CHOSEN-BY-HAND")

    def test_two_tickets_do_not_collide(self):
        self._create()
        self._create(subject="Second ticket")
        references = set(SupportTicket.objects.values_list("reference", flat=True))
        self.assertEqual(len(references), 2)


# ===========================================================================
# Engagement tracking
# ===========================================================================

@override_settings(SITE_URL="http://testserver")
class EngagementTrackingTests(TestCase):
    """Opens and clicks were rendered as rates but never measured."""

    def setUp(self):
        self.campaign = EmailCampaign.objects.create(name="C", subject="S",
                                                     content="<p>hi</p>", segment="all",
                                                     recipients=1, status="sent")
        self.delivery = MessageDelivery.objects.create(
            channel="email", recipient="reader@example.ie", template="C", status="sent",
            token="tok-123", metadata={"campaign_id": self.campaign.pk})

    def _open(self, **extra):
        return self.client.get("/e/o/tok-123.gif", **extra)

    def test_pixel_is_a_gif_and_records_the_open(self):
        response = self._open()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/gif")
        self.assertIn("no-store", response["Cache-Control"])
        self.delivery.refresh_from_db(); self.campaign.refresh_from_db()
        self.assertIsNotNone(self.delivery.opened_at)
        self.assertEqual(self.delivery.open_count, 1)
        self.assertEqual(self.campaign.opens, 1)

    def test_repeat_opens_do_not_inflate_the_campaign_rate(self):
        self._open(); self._open(); self._open()
        self.delivery.refresh_from_db(); self.campaign.refresh_from_db()
        self.assertEqual(self.delivery.open_count, 3, "raw volume is still recorded")
        self.assertEqual(self.campaign.opens, 1, "the rate counts recipients, not fetches")

    def test_unknown_token_still_returns_a_pixel(self):
        response = self.client.get("/e/o/not-a-real-token.gif")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/gif")

    def test_a_scanner_is_recorded_but_not_counted(self):
        self._open(headers={"user-agent": "Mozilla/5.0 (compatible; Barracuda Link Protection)"})
        self.delivery.refresh_from_db(); self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.opens, 0)
        self.assertIsNone(self.delivery.opened_at)
        self.assertEqual(EngagementEvent.objects.filter(kind="open").count(), 1,
                         "the hit is still stored, just not counted")

    def test_click_records_and_redirects_to_the_signed_destination(self):
        target = "https://emeraldrozalia.ie/shop"
        response = self.client.get(f"/e/c/tok-123?u={sign_target(target)}")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], target)
        self.delivery.refresh_from_db(); self.campaign.refresh_from_db()
        self.assertIsNotNone(self.delivery.clicked_at)
        self.assertEqual(self.campaign.clicks, 1)

    def test_a_click_counts_as_an_open_when_the_pixel_was_blocked(self):
        self.client.get(f"/e/c/tok-123?u={sign_target('https://example.ie/x')}")
        self.delivery.refresh_from_db(); self.campaign.refresh_from_db()
        self.assertIsNotNone(self.delivery.opened_at)
        self.assertEqual(self.campaign.opens, 1)

    def test_an_unsigned_destination_is_refused(self):
        """Without this the endpoint is an open redirect on the company domain."""
        response = self.client.get("/e/c/tok-123?u=https://evil.example.com")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(EngagementEvent.objects.filter(kind="click").exists())

    def test_a_tampered_signature_is_refused(self):
        payload = sign_target("https://emeraldrozalia.ie/shop")
        response = self.client.get(f"/e/c/tok-123?u={payload[:-3]}xyz")
        self.assertEqual(response.status_code, 400)

    def test_first_click_fires_the_engagement_event(self):
        Lead.objects.create(first_name="R", last_name="Eader", email="reader@example.ie")
        Automation.objects.create(name="Follow up", trigger="engaged",
                                  trigger_event="lead.engaged", conditions=[],
                                  actions=["Create owner summary"], status="active")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.get(f"/e/c/tok-123?u={sign_target('https://example.ie/x')}")
        self.assertTrue(WorkflowRun.objects.filter(automation__name="Follow up").exists())


@override_settings(SITE_URL="http://testserver")
class OneClickUnsubscribeTests(TestCase):
    """Gmail and Yahoo require this of bulk senders, and it was absent entirely."""

    def setUp(self):
        self.lead = Lead.objects.create(first_name="R", last_name="Eader",
                                        email="reader@example.ie", consent_at=timezone.now())
        self.delivery = MessageDelivery.objects.create(
            channel="email", recipient="reader@example.ie", template="C",
            status="sent", token="tok-u", metadata={})

    def test_post_unsubscribes_immediately(self):
        response = self.client.post("/e/u/tok-u")
        self.assertEqual(response.status_code, 200)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "unsubscribed")
        self.assertIsNone(self.lead.consent_at)
        self.assertTrue(ConsentEvent.objects.filter(email="reader@example.ie").exists())

    def test_get_only_asks_and_changes_nothing(self):
        """A mail scanner following the link must not unsubscribe the reader."""
        response = self.client.get("/e/u/tok-u")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Unsubscribe reader@example.ie?")
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "new")
        self.assertIsNotNone(self.lead.consent_at)

    def test_an_unknown_token_is_not_an_error_page(self):
        response = self.client.get("/e/u/nope")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "not recognised", status_code=404)


@override_settings(SITE_URL="http://testserver")
class CampaignTrackingTests(TestCase):
    def setUp(self):
        Lead.objects.create(first_name="A", last_name="B", email="a@example.ie",
                            consent_at=timezone.now())

    def test_sent_campaign_carries_tracking_and_unsubscribe(self):
        campaign = EmailCampaign.objects.create(
            name="Shop", subject="S", segment="all",
            content='<p>Hello</p><p><a href="https://emeraldrozalia.ie/shop">Shop now</a></p>')
        send_campaign(campaign.pk)
        message = mail.outbox[0]
        html = message.alternatives[0][0]
        self.assertIn("/e/o/", html, "the open pixel should be present")
        self.assertIn("/e/c/", html, "links should be rewritten for click tracking")
        self.assertNotIn('href="https://emeraldrozalia.ie/shop"', html)
        self.assertIn("/e/u/", html, "an unsubscribe link belongs in every campaign")
        self.assertIn("List-Unsubscribe", message.extra_headers)
        self.assertEqual(message.extra_headers["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        self.assertIn("Unsubscribe: http", message.body, "the plain-text part needs it too")

    def test_every_recipient_gets_their_own_token(self):
        Lead.objects.create(first_name="C", last_name="D", email="c@example.ie",
                            consent_at=timezone.now())
        campaign = EmailCampaign.objects.create(name="Shop", subject="S", segment="all",
                                                content="<p>Hello</p>")
        send_campaign(campaign.pk)
        tokens = set(MessageDelivery.objects.values_list("token", flat=True))
        self.assertEqual(len(tokens), 2)
        self.assertNotIn("", tokens)

    def test_tracking_does_not_rewrite_mailto_or_anchors(self):
        campaign = EmailCampaign.objects.create(
            name="Shop", subject="S", segment="all",
            content='<a href="mailto:hi@example.ie">Mail</a><a href="#top">Top</a>')
        send_campaign(campaign.pk)
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('href="mailto:hi@example.ie"', html)
        self.assertIn('href="#top"', html)


class EngagementSegmentTests(TestCase):
    """Engagement segments are what make a real drip sequence possible."""

    def setUp(self):
        now = timezone.now()
        self.opener = Lead.objects.create(first_name="O", last_name="P", email="opener@example.ie",
                                          consent_at=now)
        self.clicker = Lead.objects.create(first_name="C", last_name="L", email="clicker@example.ie",
                                           consent_at=now)
        self.quiet = Lead.objects.create(first_name="Q", last_name="T", email="quiet@example.ie",
                                         consent_at=now)
        MessageDelivery.objects.create(channel="email", recipient="opener@example.ie",
                                       status="sent", opened_at=now)
        MessageDelivery.objects.create(channel="email", recipient="clicker@example.ie",
                                       status="sent", opened_at=now, clicked_at=now)

    def test_opened_and_clicked_select_the_right_people(self):
        self.assertEqual(resolve_segment("opened")[0].count(), 2)
        self.assertEqual(resolve_segment("clicked")[0].count(), 1)
        self.assertEqual(resolve_segment("clicked")[0].first().email, "clicker@example.ie")

    def test_not_opened_is_the_resend_audience(self):
        queryset, description, unknown = resolve_segment("not opened")
        self.assertEqual(unknown, [])
        self.assertEqual([lead.email for lead in queryset], ["quiet@example.ie"])

    def test_engagement_combines_with_the_other_rules(self):
        Lead.objects.filter(pk=self.clicker.pk).update(company="Celtic Ltd")
        self.assertEqual(resolve_segment("clicked, wholesale")[0].count(), 1)
        self.assertEqual(resolve_segment("clicked, retail")[0].count(), 0)


class EngagementScoringTests(TestCase):
    def test_engagement_raises_the_score(self):
        lead = Lead.objects.create(first_name="A", last_name="B", email="a@example.ie",
                                   consent_at=timezone.now())
        automation = Automation.objects.create(name="Score", trigger="t", conditions=[],
                                               actions=["Assign lead score"], status="active")
        execute_workflow(automation.pk, {"lead_id": lead.pk})
        lead.refresh_from_db()
        self.assertEqual(lead.score, 60)

        MessageDelivery.objects.create(channel="email", recipient="a@example.ie",
                                       status="sent", clicked_at=timezone.now())
        execute_workflow(automation.pk, {"lead_id": lead.pk})
        lead.refresh_from_db()
        self.assertEqual(lead.score, 80, "a click is worth more than a filled-in field")


@override_settings(OPEN_RATE_TARGET=15.0, CLICK_RATE_TARGET=2.0)
class CampaignReviewTests(TestCase):
    def _campaign(self, **extra):
        values = {"name": "C", "subject": "S", "content": "x", "segment": "all",
                  "status": "sent", "recipients": 100, "opens": 40, "clicks": 10,
                  "sent_at": timezone.now() - timezone.timedelta(days=2)}
        values.update(extra)
        return EmailCampaign.objects.create(**values)

    def test_a_good_campaign_is_reviewed_and_left_alone(self):
        campaign = self._campaign()
        result = review_campaign_engagement()
        self.assertEqual(result, {"reviewed": 1, "flagged": 0})
        campaign.refresh_from_db()
        self.assertIsNotNone(campaign.reviewed_at)
        self.assertFalse(AttentionItem.objects.filter(category="Campaign Performance").exists())

    def test_a_poor_campaign_is_flagged_once(self):
        self._campaign(opens=3, clicks=0)
        Automation.objects.create(name="Rescue", trigger="poor",
                                  trigger_event="campaign.underperforming", conditions=[],
                                  actions=["Create owner summary"], status="active")
        with self.captureOnCommitCallbacks(execute=True):
            first = review_campaign_engagement()
        self.assertEqual(first["flagged"], 1)
        self.assertTrue(WorkflowRun.objects.filter(automation__name="Rescue").exists())
        self.assertEqual(review_campaign_engagement()["reviewed"], 0,
                         "a campaign is judged once, not every morning forever")

    def test_a_campaign_sent_an_hour_ago_is_too_early_to_judge(self):
        self._campaign(opens=0, clicks=0, sent_at=timezone.now() - timezone.timedelta(hours=1))
        self.assertEqual(review_campaign_engagement()["reviewed"], 0)


# ===========================================================================
# Revenue attribution and forecasting
# ===========================================================================

class AttributionTests(TestCase):
    def setUp(self):
        self.lead = Lead.objects.create(first_name="A", last_name="B", email="buyer@example.ie",
                                        consent_at=timezone.now())
        self.campaign = EmailCampaign.objects.create(name="Autumn", subject="S", content="x",
                                                     segment="all", status="sent",
                                                     sent_at=timezone.now())

    def _delivery(self, **extra):
        values = {"channel": "email", "recipient": "buyer@example.ie", "template": "Autumn",
                  "status": "sent", "metadata": {"campaign_id": self.campaign.pk}}
        values.update(extra)
        return MessageDelivery.objects.create(**values)

    def _conversion(self, **extra):
        values = {"lead": self.lead, "email": "buyer@example.ie", "amount": 250,
                  "occurred_at": timezone.now() + timezone.timedelta(seconds=5)}
        values.update(extra)
        return Conversion.objects.create(**values)

    def test_a_click_wins_the_credit(self):
        self._delivery(opened_at=timezone.now())
        self._delivery(clicked_at=timezone.now(), opened_at=timezone.now())
        conversion = self._conversion()
        self.assertEqual(attribute(conversion), self.campaign)
        conversion.refresh_from_db()
        self.assertEqual(conversion.attribution, "last-touch")
        self.assertEqual(conversion.email_campaign, self.campaign)

    def test_a_payment_with_no_prior_campaign_stays_unattributed(self):
        conversion = self._conversion()
        self.assertIsNone(attribute(conversion))
        conversion.refresh_from_db()
        self.assertEqual(conversion.attribution, "unattributed")

    def test_engagement_older_than_the_window_does_not_claim_credit(self):
        old = self._delivery(clicked_at=timezone.now())
        MessageDelivery.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=90))
        conversion = self._conversion()
        self.assertIsNone(attribute(conversion, window_days=30))

    def test_the_task_attributes_everything_waiting(self):
        self._delivery(clicked_at=timezone.now())
        self._conversion()
        self._conversion(amount=100)
        self.assertEqual(attribute_conversions(), {"reviewed": 2, "attributed": 2})

    def test_a_manual_attribution_is_never_overwritten(self):
        self._delivery(clicked_at=timezone.now())
        conversion = self._conversion(attribution="manual", email_campaign=None)
        attribute(conversion)
        conversion.refresh_from_db()
        self.assertEqual(conversion.attribution, "manual")


class RollUpTests(TestCase):
    def setUp(self):
        self.lead = Lead.objects.create(first_name="A", last_name="B", email="buyer@example.ie")

    def test_conversions_become_financial_records(self):
        Conversion.objects.create(lead=self.lead, amount=500, market="Ireland", channel="Email")
        result = roll_up_attribution()
        self.assertEqual(result["rolled_up"], 1)
        record = FinancialRecord.objects.get(generated=True)
        self.assertEqual(record.revenue, Decimal("500.00"))
        self.assertEqual(record.customers, 1)

    def test_running_twice_does_not_double_count(self):
        Conversion.objects.create(lead=self.lead, amount=500, market="Ireland", channel="Email")
        roll_up_attribution()
        self.assertEqual(roll_up_attribution()["rolled_up"], 0)
        self.assertEqual(FinancialRecord.objects.get(generated=True).revenue, Decimal("500.00"))

    def test_a_hand_written_record_is_never_touched(self):
        manual = FinancialRecord.objects.create(market="Ireland", system="Manual", campaign="X",
                                                channel="Email", revenue=1000)
        Conversion.objects.create(lead=self.lead, amount=500, market="Ireland", channel="Email")
        roll_up_attribution()
        manual.refresh_from_db()
        self.assertEqual(manual.revenue, Decimal("1000.00"))

    def test_revenue_with_no_exchange_rate_is_held_back_and_reported(self):
        """Counting 500 US dollars as 500 euro is the error worth preventing."""
        Conversion.objects.create(lead=self.lead, amount=500, currency="USD", market="US")
        result = roll_up_attribution()
        self.assertEqual(result["rolled_up"], 0)
        self.assertEqual(result["waiting_on_fx"], 1)
        self.assertFalse(FinancialRecord.objects.filter(generated=True).exists())
        item = AttentionItem.objects.get(source="Attribution Engine")
        self.assertIn("USD", item.recommendation)


class CurrencyTests(TestCase):
    def setUp(self):
        FxRate.objects.create(base="EUR", currency="USD", rate=Decimal("0.90"),
                              as_of=timezone.localdate() - timezone.timedelta(days=10))
        FxRate.objects.create(base="EUR", currency="USD", rate=Decimal("0.80"),
                              as_of=timezone.localdate())

    def test_conversion_uses_the_rate_that_applied_on_the_day(self):
        old = to_base(Decimal("100"), "USD", "EUR",
                      timezone.localdate() - timezone.timedelta(days=5))
        self.assertEqual(old, Decimal("90.00"))
        self.assertEqual(to_base(Decimal("100"), "USD", "EUR", timezone.localdate()),
                         Decimal("80.00"))

    def test_a_missing_rate_returns_none_rather_than_the_raw_number(self):
        self.assertIsNone(to_base(Decimal("100"), "JPY", "EUR"))

    def test_the_base_currency_needs_no_rate(self):
        self.assertEqual(to_base(Decimal("100"), "EUR", "EUR"), Decimal("100.00"))

    def test_a_conversion_stores_its_base_amount_on_save(self):
        conversion = Conversion.objects.create(amount=Decimal("100"), currency="USD")
        self.assertEqual(conversion.base_amount, Decimal("80.00"))

    def test_a_conversion_without_a_rate_stores_no_base_amount(self):
        conversion = Conversion.objects.create(amount=Decimal("100"), currency="JPY")
        self.assertIsNone(conversion.base_amount)


class ForecastTests(TestCase):
    def _history(self, days, revenue=100):
        today = timezone.localdate()
        for offset in range(days):
            FinancialRecord.objects.create(
                recorded_on=today - timezone.timedelta(days=offset), market="Ireland",
                system="Test", campaign="C", channel="Email", revenue=revenue + offset)

    def test_a_forecast_is_refused_when_there_is_no_history(self):
        self._history(3)
        result = forecast()
        self.assertFalse(result["available"])
        self.assertIn("needed", result["reason"])

    def test_a_forecast_is_produced_once_there_is_enough(self):
        self._history(30)
        result = forecast(horizon_days=30)
        self.assertTrue(result["available"])
        self.assertGreater(result["total"], 0)
        self.assertIn(result["direction"], {"rising", "falling", "flat"})
        self.assertGreaterEqual(result["fit"], 0.0)

    def test_the_fit_is_reported_so_a_noisy_trend_is_visible(self):
        self._history(20)
        self.assertGreater(forecast()["fit"], 0.9, "a clean ramp should fit almost perfectly")


class RevenueReportingTests(TestCase):
    def setUp(self):
        self.lead = Lead.objects.create(first_name="A", last_name="B", email="buyer@example.ie")
        FinancialRecord.objects.create(market="Ireland", system="Email Marketing", campaign="C",
                                       channel="Email", revenue=1000, cost=250, leads=10, customers=4)
        Conversion.objects.create(lead=self.lead, amount=400)
        Conversion.objects.create(lead=self.lead, amount=200)

    def test_channel_performance_reports_roi(self):
        row = channel_performance()[0]
        self.assertEqual(row["channel"], "Email")
        self.assertEqual(row["profit"], Decimal("750.00"))
        self.assertEqual(row["roi"], 300.0)

    def test_lifetime_value_counts_a_repeat_buyer_once(self):
        value = lifetime_value()
        self.assertEqual(value["customers"], 1)
        self.assertEqual(value["revenue"], Decimal("600.00"))
        self.assertEqual(value["ltv"], Decimal("600.00"))
        self.assertEqual(value["repeat_customers"], 1)

    def test_attribution_gap_is_stated_rather_than_hidden(self):
        gap = attribution_gap()
        self.assertEqual(gap["total"], Decimal("600.00"))
        self.assertEqual(gap["unattributed"], Decimal("600.00"))
        self.assertEqual(gap["coverage"], 0)

    def test_cohorts_group_leads_by_the_month_they_arrived(self):
        rows = cohorts()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["leads"], 1)
        self.assertEqual(rows[0]["customers"], 1)
        self.assertEqual(rows[0]["revenue"], Decimal("600.00"))

    def test_the_revenue_page_renders(self):
        User.objects.create_superuser("owner@example.ie", password="Strong-Test-Password-123")
        self.client.login(username="owner@example.ie", password="Strong-Test-Password-123")
        response = self.client.get("/revenue/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "revenue_center.html")
        self.assertContains(response, "LIFETIME VALUE")


class WeeklyReviewTests(TestCase):
    def test_the_review_is_sent_and_readable(self):
        lead = Lead.objects.create(first_name="A", last_name="B", email="buyer@example.ie")
        Conversion.objects.create(lead=lead, amount=750)
        result = send_weekly_review()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("750", mail.outbox[0].subject)
        self.assertTrue(mail.outbox[0].alternatives)
        self.assertEqual(result["conversions"], 1)
        self.assertTrue(AuditLog.objects.filter(event="weekly_review.sent").exists())

    def test_it_still_sends_with_no_data_at_all(self):
        """The first Monday of a new install must not raise."""
        send_weekly_review()
        self.assertEqual(len(mail.outbox), 1)


# ===========================================================================
# Owner alerting and one-click approvals
# ===========================================================================

@override_settings(SITE_URL="http://testserver")
class CriticalAlertTests(TestCase):
    def _item(self, **extra):
        values = {"severity": "critical", "category": "High-Risk Decision", "title": "Budget overrun",
                  "confidence": 90, "source": "Campaign Engine", "recommendation": "Pause the campaign"}
        values.update(extra)
        return AttentionItem.objects.create(**values)

    def test_a_critical_item_is_pushed_out_immediately(self):
        item = self._item()
        result = dispatch_critical_alerts()
        self.assertEqual(result["alerted"], 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Budget overrun", mail.outbox[0].subject)
        self.assertIn("/act/", mail.outbox[0].alternatives[0][0])
        item.refresh_from_db()
        self.assertIsNotNone(item.alerted_at)

    def test_the_same_item_is_never_alerted_twice(self):
        self._item()
        dispatch_critical_alerts()
        self.assertEqual(dispatch_critical_alerts()["alerted"], 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_a_resolved_item_is_not_alerted(self):
        self._item(status="resolved")
        self.assertEqual(dispatch_critical_alerts()["alerted"], 0)

    def test_lesser_severities_wait_for_the_digest(self):
        self._item(severity="medium")
        self.assertEqual(dispatch_critical_alerts()["alerted"], 0)


@override_settings(SITE_URL="http://testserver")
class OwnerActionLinkTests(TestCase):
    def setUp(self):
        self.item = AttentionItem.objects.create(
            severity="critical", category="High-Risk Decision", title="Budget overrun",
            confidence=90, source="Campaign Engine", recommendation="Pause it")
        self.url = "/act/" + action_token("attention", self.item.pk, "resolve")

    def test_a_get_asks_before_changing_anything(self):
        """Mail scanners fetch every link; a mutating GET would let one decide."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "CONFIRM RESOLVE")
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "pending")

    def test_a_post_applies_the_action_and_audits_it(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "resolved")
        entry = AuditLog.objects.get(event="attention.resolve")
        self.assertEqual(entry.new_values["record_id"], self.item.pk)
        self.assertEqual(entry.new_values["via"], "signed link")

    def test_a_tampered_link_is_refused(self):
        response = self.client.post(self.url[:-4] + "xxxx")
        self.assertEqual(response.status_code, 400)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "pending")

    def test_an_expired_link_is_refused(self):
        payload = action_token("attention", self.item.pk, "resolve")
        self.assertIsNone(resolve_token(payload, max_age=-1))

    def test_a_decision_can_be_approved_from_a_message(self):
        decision = AiDecision.objects.create(
            decision_id="ER-TEST", engine="E", title="Raise budget", recommendation="Do it",
            confidence=80, impact="high", risk_score=20, governance_level="review")
        self.client.post("/act/" + action_token("decision", decision.pk, "approve"))
        decision.refresh_from_db()
        self.assertEqual(decision.status, "approved")
        self.assertIsNotNone(decision.decided_at)


@override_settings(ESCALATION_STEPS=[4, 24, 72])
class EscalationTests(TestCase):
    def _overdue(self, hours, severity="low"):
        return AttentionItem.objects.create(
            severity=severity, category="Governance", title=f"Late by {hours}h",
            confidence=50, source="Test", recommendation="Act",
            due_at=timezone.now() - timezone.timedelta(hours=hours))

    def test_an_item_just_past_its_deadline_is_not_escalated_yet(self):
        self._overdue(1)
        self.assertEqual(escalate_attention()["escalated"], 0)

    def test_each_step_raises_the_severity_once(self):
        item = self._overdue(5)
        self.assertEqual(escalate_attention()["escalated"], 1)
        item.refresh_from_db()
        self.assertEqual(item.severity, "medium")
        self.assertEqual(item.escalation_level, 1)
        self.assertEqual(escalate_attention()["escalated"], 0, "one step, one escalation")

    def test_a_long_ignored_item_climbs_to_critical_and_re_arms_the_alert(self):
        item = self._overdue(100, severity="high")
        AttentionItem.objects.filter(pk=item.pk).update(alerted_at=timezone.now())
        escalate_attention()
        item.refresh_from_db()
        self.assertEqual(item.severity, "critical")
        self.assertIsNone(item.alerted_at, "reaching critical should page the owner again")
        self.assertEqual(dispatch_critical_alerts()["alerted"], 1)

    def test_escalation_fires_its_event(self):
        Automation.objects.create(name="Escalated", trigger="e",
                                  trigger_event="attention.escalated", conditions=[],
                                  actions=["Create owner summary"], status="active")
        self._overdue(5)
        with self.captureOnCommitCallbacks(execute=True):
            escalate_attention()
        self.assertTrue(WorkflowRun.objects.filter(automation__name="Escalated").exists())

    def test_a_resolved_item_stops_climbing(self):
        item = self._overdue(100)
        AttentionItem.objects.filter(pk=item.pk).update(status="resolved")
        self.assertEqual(escalate_attention()["escalated"], 0)


class SlaDeadlineTests(TestCase):
    def test_an_sla_becomes_a_real_deadline(self):
        item = AttentionItem.objects.create(
            severity="medium", category="X", title="With SLA", confidence=50,
            source="Test", recommendation="Act", sla_hours=8)
        self.assertIsNotNone(item.due_at)
        self.assertGreater(item.due_at, timezone.now())

    def test_an_explicit_deadline_wins(self):
        chosen = timezone.now() + timezone.timedelta(days=3)
        item = AttentionItem.objects.create(
            severity="medium", category="X", title="Explicit", confidence=50,
            source="Test", recommendation="Act", sla_hours=8, due_at=chosen)
        self.assertEqual(item.due_at, chosen)


# ===========================================================================
# Multi-entity foundation
# ===========================================================================

class TenancyTests(TestCase):
    """One entity behaves exactly as before; a second turns isolation on."""

    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_superuser("owner@example.ie",
                                                   password="Strong-Test-Password-123")
        self.client.login(username="owner@example.ie", password="Strong-Test-Password-123")
        self.first = Organisation.objects.get(code="ER")

    def tearDown(self):
        # The entity count is cached, and the cache outlives the test rollback.
        cache.clear()

    def _second(self):
        return Organisation.objects.create(name="Rozalia Italia", code="IT", country="IT",
                                           base_currency="EUR")

    def test_the_migration_created_the_founding_entity(self):
        self.assertEqual(self.first.name, "Emerald Rozalia Limited")
        self.assertEqual(self.first.base_currency, "EUR")
        self.assertFalse(is_multi_entity())

    def test_with_one_entity_nothing_is_scoped_away(self):
        lead = Lead.objects.create(first_name="A", last_name="B", email="a@example.ie")
        # Forced past the stamping signal, to prove scoping itself cannot hide a
        # record that somehow reached the database without an entity.
        Lead.objects.filter(pk=lead.pk).update(organisation=None)
        response = self.client.get("/leads/")
        self.assertEqual(response.context["total_count"], 1,
                         "a single entity must never hide an unassigned record")

    def test_every_new_record_is_stamped_with_an_entity(self):
        lead = Lead.objects.create(first_name="A", last_name="B", email="a@example.ie")
        self.assertEqual(lead.organisation, self.first)

    def test_a_record_raised_by_a_workflow_is_not_orphaned(self):
        """The owner-summary action runs in Celery, with no request to read."""
        automation = Automation.objects.create(name="Summarise", trigger="t", conditions=[],
                                               actions=["Create owner summary"], status="active")
        execute_workflow(automation.pk, {})
        item = AttentionItem.objects.get(source="Automation Engine")
        self.assertEqual(item.organisation, self.first)

    def test_a_second_entity_isolates_the_records(self):
        second = self._second()
        Lead.objects.create(first_name="I", last_name="E", email="ie@example.ie",
                            organisation=self.first)
        Lead.objects.create(first_name="I", last_name="T", email="it@example.it",
                            organisation=second)
        self.assertTrue(is_multi_entity())
        response = self.client.get("/leads/")
        self.assertEqual(response.context["total_count"], 1)
        self.assertEqual(response.context["rows"][0]["pk"],
                         Lead.objects.get(email="ie@example.ie").pk)

    def test_switching_entity_changes_what_is_listed(self):
        second = self._second()
        Lead.objects.create(first_name="I", last_name="E", email="ie@example.ie",
                            organisation=self.first)
        Lead.objects.create(first_name="I", last_name="T", email="it@example.it",
                            organisation=second)
        self.client.post("/organisation/switch", {"organisation": second.pk, "next": "/leads/"})
        response = self.client.get("/leads/")
        self.assertEqual(response.context["rows"][0]["pk"],
                         Lead.objects.get(email="it@example.it").pk)

    def test_the_switcher_will_not_redirect_off_site(self):
        second = self._second()
        response = self.client.post("/organisation/switch",
                                    {"organisation": second.pk, "next": "https://evil.example.com/"})
        self.assertEqual(response["Location"], "/")

    def test_a_new_record_joins_the_entity_being_viewed(self):
        second = self._second()
        self.client.post("/organisation/switch", {"organisation": second.pk})
        self.client.post("/leads/", {"action": "create", "first_name": "New", "last_name": "Lead",
                                     "email": "new@example.it", "phone": "", "company": "",
                                     "market": "Italy", "source": "Website", "status": "new",
                                     "score": 0})
        self.assertEqual(Lead.objects.get(email="new@example.it").organisation, second)

    def test_the_tenant_key_is_not_an_editable_field(self):
        names = {item["field"].name for item in self.client.get("/leads/").context["form_fields"]}
        self.assertNotIn("organisation", names)

    def test_the_entity_column_appears_only_when_it_means_something(self):
        # `columns` carries the raw verbose names; the template title-cases them.
        self.assertNotIn("organisation", self.client.get("/leads/").context["columns"])
        self._second()
        self.assertIn("organisation", self.client.get("/leads/").context["columns"])

    def test_the_entity_register_is_owner_only(self):
        User.objects.create_user("staff@example.ie", password="Strong-Test-Password-123")
        self.client.login(username="staff@example.ie", password="Strong-Test-Password-123")
        for slug in ["organisations", "fx-rates"]:
            self.assertEqual(self.client.get(f"/{slug}/").status_code, 403, slug)

    def test_background_work_still_lands_in_an_entity(self):
        """A Celery-created record has no request, so it must fall back to the default."""
        item = assign(AttentionItem(severity="low", category="X", title="From a task",
                                    confidence=1, source="Task", recommendation="none"))
        self.assertEqual(item.organisation, self.first)


class SecurityHeaderTests(TestCase):
    def test_the_referrer_policy_is_actually_sent(self):
        """It was set under a name Django does not read, so it never was."""
        response = self.client.get("/up")
        self.assertEqual(response["Referrer-Policy"], "strict-origin-when-cross-origin")


class UnsubscribeRateLimitTests(TestCase):
    def test_the_public_endpoint_is_throttled(self):
        Lead.objects.create(first_name="A", last_name="B", email="a@example.ie")
        statuses = [self.client.post("/api/consent/unsubscribe", data='{"email":"a@example.ie"}',
                                     content_type="application/json").status_code
                    for _ in range(40)]
        self.assertIn(429, statuses, "an unauthenticated endpoint that confirms membership "
                                     "of the list should not be free to enumerate")
