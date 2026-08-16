"""Background work.

The workflow engine executes a small, explicit vocabulary of actions. Anything
it does not recognise is recorded as `skipped` with a reason rather than being
counted as success — the previous implementation copied the action list onto the
run and reported "completed" without doing anything at all.
"""
import re

from celery import shared_task
from django.core.mail import EmailMultiAlternatives
from django.db.models import Count
from django.utils import timezone
from django.utils.html import strip_tags

from django.conf import settings
from django.template.loader import render_to_string

from .segments import resolve as resolve_segment
from .models import (
    AiDecision, AttentionItem, ConsentEvent, Automation, AuditLog, EmailCampaign, Integration,
    IntegrationCheck, Lead, MessageDelivery, WhatsAppTemplate, WorkflowRun,
)


# --- action handlers -------------------------------------------------------
# Each returns a short human-readable detail string, or raises on failure.

def _action_send_email(automation, lead, action, context):
    if lead is None:
        raise LookupError("no lead in the trigger payload to email")
    if not lead.consent_at or lead.status == "unsubscribed":
        raise PermissionError(f"{lead.email} has not consented")
    subject = f"{automation.name} — Emerald Rozalia Limited"
    body = f"Hello {lead.first_name},\n\nThis message was sent by the '{automation.name}' workflow."
    delivery = MessageDelivery.objects.create(
        channel="email", recipient=lead.email, template=automation.name,
        status="sending", metadata={"automation_id": automation.id, "depth": context.get("depth", 0)},
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


def _action_score_lead(automation, lead, action, context):
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


def _action_owner_summary(automation, lead, action, context):
    item = AttentionItem.objects.create(
        severity="low", category="Auto-Executed",
        title=f"{automation.name} completed",
        confidence=90, source="Automation Engine",
        recommendation=f"Automation '{automation.name}' ran its actions. No owner action required.",
        status="pending",
    )
    return f"raised attention item #{item.pk}"


def _action_send_whatsapp(automation, lead, action, context):
    from .services import send_whatsapp_template
    if lead is None or not lead.phone:
        raise LookupError("no lead phone number in the trigger payload")
    template = WhatsAppTemplate.objects.filter(status="approved").first()
    if template is None:
        raise LookupError("no approved WhatsApp template available")
    recipient = re.sub(r"[^0-9]", "", lead.phone)
    delivery = MessageDelivery.objects.create(
        channel="whatsapp", recipient=recipient, template=template.name,
        status="sending", metadata={"automation_id": automation.id, "depth": context.get("depth", 0)},
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


WAIT_AMOUNT = re.compile(r"wait\s+(\d+)\s*(minute|min|hour|hr|day)", re.I)
UNIT_SECONDS = {"minute": 60, "min": 60, "hour": 3600, "hr": 3600, "day": 86400}


def parse_wait(action):
    """Seconds a Wait step should pause for, or None when it is not a wait."""
    if not WAIT_PATTERN.match(action):
        return None
    match = WAIT_AMOUNT.search(action)
    if not match:
        return 3600  # "Wait" with no figure means an hour
    return int(match.group(1)) * UNIT_SECONDS[match.group(2).lower()]


def _run_action(automation, lead, action, context):
    """Return (status, detail) for a single action string."""
    if WAIT_PATTERN.match(action):
        return "skipped", "handled as a scheduled delay"
    for pattern, handler in ACTION_HANDLERS:
        if pattern.search(action):
            try:
                return "completed", handler(automation, lead, action, context)
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
def execute_workflow(automation_id, payload=None, start_index=0):
    """Run one workflow from `start_index`.

    A Wait step does not block a worker: the actions completed so far are
    recorded, the run is marked `waiting`, and the remainder is queued with a
    countdown. That is what turns a workflow into a multi-step drip sequence.
    """
    automation = Automation.objects.get(pk=automation_id)
    payload = payload or {}
    if int(payload.get("depth", 0)) > 2:
        return None  # event loop guard; see core/signals.py
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

        steps = list(automation.actions or [])
        waited = False
        for index in range(start_index, len(steps)):
            action = steps[index]
            seconds = parse_wait(action)
            if seconds is not None and index + 1 < len(steps):
                results.append({"action": action, "status": "waiting",
                                "detail": f"resuming in {seconds // 60} min"})
                execute_workflow.apply_async(
                    (automation_id, payload, index + 1), countdown=seconds)
                waited = True
                break
            if seconds is not None:
                results.append({"action": action, "status": "skipped",
                                "detail": "nothing follows this wait"})
                continue
            status, detail = _run_action(automation, lead, action, payload)
            results.append({"action": action, "status": status, "detail": detail})

        run.actions_completed = results
        if waited:
            run.status = "waiting"
            run.finished_at = timezone.now()
            run.save()
            automation.last_run_at = timezone.now()
            automation.save(update_fields=["last_run_at", "updated_at"])
            return run.id
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
    # The segment finally selects the audience instead of being decorative.
    audience, description, unknown = resolve_segment(campaign.segment)
    recipients = list(audience.values_list("email", flat=True))

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
        new_values={"id": campaign_id, "sent": sent, "failures": failures, "skipped": skipped,
                    "segment": campaign.segment, "audience": description},
    )
    return {"sent": sent, "failures": failures, "skipped": skipped, "audience": description}


@shared_task
def sweep_overdue_attention():
    """Raise `attention.overdue` once for each item that has passed its deadline.

    `overdue_notified_at` stops the same item being reported every time this runs.
    """
    from .signals import fire
    now = timezone.now()
    due = AttentionItem.objects.filter(due_at__lt=now, overdue_notified_at=None).exclude(status="resolved")
    raised = 0
    for item in due:
        fire("attention.overdue", {
            "attention_id": item.pk, "title": item.title,
            "severity": item.severity, "category": item.category,
        })
        raised += 1
    ids = list(due.values_list("pk", flat=True))
    AttentionItem.objects.filter(pk__in=ids).update(overdue_notified_at=now)
    return {"overdue": raised}


def build_digest(window_hours=24):
    """Everything the owner needs to know about the last day, as plain data."""
    now = timezone.now()
    since = now - timezone.timedelta(hours=window_hours)
    open_items = AttentionItem.objects.exclude(status="resolved")
    return {
        "generated_at": timezone.localtime(now),
        "window_hours": window_hours,
        "critical": list(open_items.filter(severity="critical").order_by("-created_at")[:10]),
        "open_count": open_items.count(),
        "overdue": list(open_items.filter(due_at__lt=now).order_by("due_at")[:10]),
        "new_leads": Lead.objects.filter(created_at__gte=since).count(),
        "consented": Lead.objects.filter(consent_at__gte=since).count(),
        "unsubscribed": Lead.objects.filter(status="unsubscribed", updated_at__gte=since).count(),
        "campaigns": list(EmailCampaign.objects.filter(sent_at__gte=since).order_by("-sent_at")[:5]),
        "failed_deliveries": MessageDelivery.objects.filter(status="failed", updated_at__gte=since).count(),
        "workflow_runs": WorkflowRun.objects.filter(started_at__gte=since).count(),
        "workflow_failures": WorkflowRun.objects.filter(started_at__gte=since, status="failed").count(),
        "pending_decisions": AiDecision.objects.filter(status="pending").count(),
        "broken_integrations": list(Integration.objects.exclude(status="connected")),
    }


@shared_task
def send_owner_digest(window_hours=24):
    """Email the owner a summary so the console does not have to be visited to learn anything."""
    recipient = getattr(settings, "OWNER_EMAIL", "") or settings.DEFAULT_FROM_EMAIL
    digest = build_digest(window_hours)
    subject = (f"Emerald Rozalia daily brief — {len(digest['critical'])} critical, "
               f"{len(digest['overdue'])} overdue")
    html = render_to_string("email/owner_digest.html", digest)
    message = EmailMultiAlternatives(subject, strip_tags(html), None, [recipient])
    message.attach_alternative(html, "text/html")
    message.send()
    AuditLog.objects.create(event="digest.sent", new_values={
        "recipient": recipient, "critical": len(digest["critical"]),
        "overdue": len(digest["overdue"]), "open": digest["open_count"],
    })
    return {"sent_to": recipient, "critical": len(digest["critical"]), "overdue": len(digest["overdue"])}


@shared_task
def send_scheduled_campaigns():
    """Queue campaigns whose scheduled time has arrived.

    `EmailCampaign.scheduled_at` existed from the start and nothing ever read it,
    so a scheduled campaign simply never sent.
    """
    now = timezone.now()
    due = EmailCampaign.objects.filter(status="scheduled", scheduled_at__lte=now)
    queued = list(due.values_list("pk", flat=True))
    for campaign_id in queued:
        EmailCampaign.objects.filter(pk=campaign_id).update(status="queued")
        send_campaign.delay(campaign_id)
    return {"queued": len(queued), "ids": queued}


@shared_task
def process_bounces():
    """Unsubscribe addresses that keep hard-failing.

    Continuing to mail an address that bounces damages sender reputation, which
    pushes legitimate campaigns into spam folders for everyone else.
    """
    from django.conf import settings as conf
    from .signals import fire
    limit = getattr(conf, "BOUNCE_LIMIT", 3)
    counts = (MessageDelivery.objects.filter(channel="email", status="failed")
              .values("recipient").annotate(n=Count("id")).filter(n__gte=limit))
    unsubscribed = []
    for row in counts:
        lead = (Lead.objects.filter(email__iexact=row["recipient"])
                .exclude(status="unsubscribed").first())
        if lead is None:
            continue
        Lead.objects.filter(pk=lead.pk).update(consent_at=None, status="unsubscribed")
        ConsentEvent.objects.create(email=lead.email.lower(), action="unsubscribe",
                                    source=f"bounce limit ({row['n']} failures)")
        AttentionItem.objects.create(
            severity="medium", category="Deliverability",
            title=f"{lead.email} unsubscribed after {row['n']} bounces",
            confidence=100, source="Deliverability Monitor",
            recommendation="Address repeatedly rejected mail. Remove it from any external list too.")
        fire("lead.bounced", {"lead_id": lead.pk, "email": lead.email, "failures": row["n"]})
        unsubscribed.append(lead.email)
    if unsubscribed:
        AuditLog.objects.create(event="deliverability.auto_unsubscribed",
                                new_values={"emails": unsubscribed, "limit": limit})
    return {"unsubscribed": len(unsubscribed), "limit": limit}


@shared_task
def expire_stale_consent():
    """Lapse consent that has gone stale, and tell the owner.

    Consent gathered years ago is hard to defend under GDPR. These leads stop
    receiving campaigns until they opt in again.
    """
    from django.conf import settings as conf
    months = getattr(conf, "CONSENT_EXPIRY_MONTHS", 24)
    cutoff = timezone.now() - timezone.timedelta(days=months * 30)
    stale = list(Lead.objects.exclude(consent_at=None)
                 .exclude(status="unsubscribed").filter(consent_at__lt=cutoff))
    for lead in stale:
        Lead.objects.filter(pk=lead.pk).update(consent_at=None, status="consent-expired")
        ConsentEvent.objects.create(email=lead.email.lower(), action="expired",
                                    source=f"older than {months} months")
    if stale:
        AttentionItem.objects.create(
            severity="high", category="Governance / Ethical Alert",
            title=f"{len(stale)} lead consent record(s) expired",
            confidence=100, source="Compliance Engine",
            recommendation=f"Consent older than {months} months has lapsed. Re-confirm before mailing these contacts again.")
        AuditLog.objects.create(event="consent.expired",
                                new_values={"count": len(stale), "months": months,
                                            "emails": [l.email for l in stale][:50]})
    return {"expired": len(stale), "months": months}


@shared_task
def flag_dormant_leads():
    """Raise `lead.dormant` for contacts who have gone quiet, so a win-back can run."""
    from django.conf import settings as conf
    from .signals import fire
    months = getattr(conf, "DORMANT_MONTHS", 6)
    cutoff = timezone.now() - timezone.timedelta(days=months * 30)
    active_recipients = set(
        MessageDelivery.objects.filter(created_at__gte=cutoff).values_list("recipient", flat=True))
    dormant = [lead for lead in Lead.objects.exclude(consent_at=None).exclude(status="unsubscribed")
               .filter(updated_at__lt=cutoff)
               if lead.email not in active_recipients]
    for lead in dormant:
        fire("lead.dormant", {"lead_id": lead.pk, "email": lead.email})
    if dormant:
        AttentionItem.objects.create(
            severity="low", category="High-Impact Opportunity",
            title=f"{len(dormant)} lead(s) have gone quiet",
            confidence=80, source="Engagement Monitor",
            recommendation=f"No contact in {months} months. Consider a win-back campaign or archive them.")
    return {"dormant": len(dormant), "months": months}
