from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import (
    AeoEntry, AiDecision, AttentionItem, Automation, AuditLog, Campaign, ConsentEvent,
    ContentItem, EmailCampaign, FinancialRecord, Integration, IntegrationCheck, Lead,
    MessageDelivery, Setting, SupportTicket, WhatsAppTemplate, WorkflowRun,
)
from .segments import resolve as resolve_segment
from .tasks import (build_digest, execute_workflow, expire_stale_consent, flag_dormant_leads,
                    parse_wait, process_bounces, process_due_automations, send_campaign,
                    send_owner_digest, send_scheduled_campaigns, sweep_overdue_attention)


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

