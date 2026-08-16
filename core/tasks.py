"""Background work.

The workflow engine executes a small, explicit vocabulary of actions. Anything
it does not recognise is recorded as `skipped` with a reason rather than being
counted as success — the previous implementation copied the action list onto the
run and reported "completed" without doing anything at all.
"""
import re

from celery import shared_task
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone
from django.utils.html import strip_tags

from .models import (
    AttentionItem, Automation, AuditLog, EmailCampaign, Lead, MessageDelivery,
    WhatsAppTemplate, WorkflowRun,
)


# --- action handlers -------------------------------------------------------
# Each returns a short human-readable detail string, or raises on failure.

def _action_send_email(automation, lead, action):
    if lead is None:
        raise LookupError("no lead in the trigger payload to email")
    if not lead.consent_at or lead.status == "unsubscribed":
        raise PermissionError(f"{lead.email} has not consented")
    subject = f"{automation.name} — Emerald Rozalia Limited"
    body = f"Hello {lead.first_name},\n\nThis message was sent by the '{automation.name}' workflow."
    delivery = MessageDelivery.objects.create(
        channel="email", recipient=lead.email, template=automation.name,
        status="sending", metadata={"automation_id": automation.id},
    )
    try:
        message = EmailMultiAlternatives(subject, body, None, [lead.email])
        message.send()
        delivery.status = "sent"
        delivery.save()
    except Exception as exc:
        delivery.status = "failed"
        delivery.error = str(exc)[:1000]
        delivery.save()
        raise
    return f"emailed {lead.email}"


def _action_score_lead(automation, lead, action):
    if lead is None:
        raise LookupError("no lead in the trigger payload to score")
    score = 20
    if lead.consent_at:
        score += 40
    if lead.company:
        score += 20
    if lead.phone:
        score += 20
    lead.score = min(score, 100)
    lead.save(update_fields=["score", "updated_at"])
    return f"scored {lead.email} at {lead.score}"


def _action_owner_summary(automation, lead, action):
    item = AttentionItem.objects.create(
        severity="low", category="Auto-Executed",
        title=f"{automation.name} completed",
        confidence=90, source="Automation Engine",
        recommendation=f"Automation '{automation.name}' ran its actions. No owner action required.",
        status="pending",
    )
    return f"raised attention item #{item.pk}"


def _action_send_whatsapp(automation, lead, action):
    from .services import send_whatsapp_template
    if lead is None or not lead.phone:
        raise LookupError("no lead phone number in the trigger payload")
    template = WhatsAppTemplate.objects.filter(status="approved").first()
    if template is None:
        raise LookupError("no approved WhatsApp template available")
    recipient = re.sub(r"[^0-9]", "", lead.phone)
    delivery = MessageDelivery.objects.create(
        channel="whatsapp", recipient=recipient, template=template.name,
        status="sending", metadata={"automation_id": automation.id},
    )
    try:
        delivery.external_id = send_whatsapp_template(recipient, template.name, template.language)
        delivery.status = "sent"
        delivery.save()
        WhatsAppTemplate.objects.filter(pk=template.pk).update(sent=template.sent + 1)
    except Exception as exc:
        delivery.status = "failed"
        delivery.error = str(exc)[:1000]
        delivery.save()
        raise
    return f"sent {template.name} to {recipient}"


# Matched in order; the first pattern found in the action text wins.
ACTION_HANDLERS = [
    (re.compile(r"whats\s*app", re.I), _action_send_whatsapp),
    (re.compile(r"\bemail\b", re.I), _action_send_email),
    (re.compile(r"score", re.I), _action_score_lead),
    (re.compile(r"summary|report|notify", re.I), _action_owner_summary),
]
WAIT_PATTERN = re.compile(r"^\s*wait\b", re.I)


def _run_action(automation, lead, action):
    """Return (status, detail) for a single action string."""
    if WAIT_PATTERN.match(action):
        return "skipped", "delay steps are not executed inline"
    for pattern, handler in ACTION_HANDLERS:
        if pattern.search(action):
            try:
                return "completed", handler(automation, lead, action)
            except Exception as exc:
                return "failed", f"{type(exc).__name__}: {exc}"[:300]
    return "skipped", "no handler matches this action"


def _conditions_pass(automation, lead):
    """Evaluate the workflow's conditions against the triggering lead."""
    for condition in automation.conditions or []:
        text = str(condition).lower()
        if "consent" in text:
            if lead is None or not lead.consent_at:
                return False, f"condition not met: {condition}"
        elif "not purchased" in text or "contactable" in text:
            if lead is None:
                return False, f"condition not met: {condition}"
    return True, ""


@shared_task
def execute_workflow(automation_id, payload=None):
    """Run one workflow. Records what actually happened per action."""
    automation = Automation.objects.get(pk=automation_id)
    payload = payload or {}
    run = WorkflowRun.objects.create(automation=automation, status="running", trigger_payload=payload)

    lead = None
    if payload.get("lead_id"):
        lead = Lead.objects.filter(pk=payload["lead_id"]).first()
    elif payload.get("email"):
        lead = Lead.objects.filter(email__iexact=payload["email"]).first()

    results = []
    try:
        passed, reason = _conditions_pass(automation, lead)
        if not passed:
            run.status = "skipped"
            run.actions_completed = [{"action": "conditions", "status": "skipped", "detail": reason}]
            automation.runs += 1
            automation.last_run_at = timezone.now()
            automation.save(update_fields=["runs", "last_run_at", "updated_at"])
            return run.id

        for action in automation.actions or []:
            status, detail = _run_action(automation, lead, action)
            results.append({"action": action, "status": status, "detail": detail})

        run.actions_completed = results
        failed = [r for r in results if r["status"] == "failed"]
        run.status = "failed" if failed else "completed"
        if failed:
            run.error = "; ".join(r["detail"] for r in failed)[:900]

        automation.runs += 1
        if failed:
            automation.failures += 1
        else:
            automation.successes += 1
        automation.last_run_at = timezone.now()
        automation.save(update_fields=["runs", "successes", "failures", "last_run_at", "updated_at"])
    except Exception as exc:
        run.status = "failed"
        run.error = str(exc)[:900]
        run.actions_completed = results
        automation.runs += 1
        automation.failures += 1
        automation.last_run_at = timezone.now()
        automation.save(update_fields=["runs", "failures", "last_run_at", "updated_at"])
        raise
    finally:
        run.finished_at = timezone.now()
        run.save()
    return run.id


@shared_task
def process_due_automations():
    """Queue workflows that are actually due.

    This used to increment `runs` and `successes` for every active automation
    once a minute without doing any work, which fabricated roughly 1,440
    successful runs per automation per day.
    """
    queued = []
    for automation in Automation.objects.filter(status="active"):
        if automation.is_due:
            execute_workflow.delay(automation.id, {"source": "scheduler"})
            queued.append(automation.id)
    return {"queued": len(queued), "ids": queued}


@shared_task(autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def send_campaign(campaign_id):
    """Send one email campaign to every consented lead.

    Recipients already marked `sent` for this campaign are skipped, so a retry
    after a mid-run failure does not deliver the same message twice.
    """
    campaign = EmailCampaign.objects.get(pk=campaign_id)
    campaign.status = "sending"
    campaign.save(update_fields=["status", "updated_at"])

    already_sent = set(
        MessageDelivery.objects
        .filter(channel="email", status="sent", metadata__campaign_id=campaign.id)
        .values_list("recipient", flat=True)
    )
    recipients = list(
        Lead.objects.exclude(consent_at=None).exclude(status="unsubscribed")
        .values_list("email", flat=True)
    )

    sent = failures = skipped = 0
    for address in recipients:
        if address in already_sent:
            skipped += 1
            continue
        delivery = MessageDelivery.objects.create(
            channel="email", recipient=address, template=campaign.name,
            status="sending", metadata={"campaign_id": campaign.id},
        )
        try:
            message = EmailMultiAlternatives(campaign.subject, strip_tags(campaign.content), None, [address])
            message.attach_alternative(campaign.content, "text/html")
            message.send()
            delivery.status = "sent"
            sent += 1
        except Exception as exc:
            delivery.status = "failed"
            delivery.error = str(exc)[:1000]
            failures += 1
        delivery.save()

    campaign.recipients = len(recipients)
    campaign.failures = failures
    campaign.status = "sent" if (sent or skipped) else "failed"
    campaign.sent_at = timezone.now()
    campaign.save()
    AuditLog.objects.create(
        event="email_campaign.delivery_completed",
        new_values={"id": campaign_id, "sent": sent, "failures": failures, "skipped": skipped},
    )
    return {"sent": sent, "failures": failures, "skipped": skipped}
