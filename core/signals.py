"""Event triggers for the workflow engine.

`Automation.trigger` has always been free text — stored, displayed, never
evaluated — so a workflow reading "WHEN Lead created" actually ran on a timer.
These receivers give that field meaning: when a real event happens, every active
workflow whose `trigger_event` matches is queued immediately.

Three safeguards:

* A `pre_save` snapshot records the previous state, so `lead.consented` and
  `campaign.sent` fire on the transition only. Without it, editing an already
  consented lead would raise the event again on every save.
* Tasks are queued through `transaction.on_commit`, so a worker never picks up a
  row the sending transaction has not committed yet.
* The payload carries a `depth` that `execute_workflow` refuses to recurse past.
  Otherwise a workflow whose "send email" action fails would raise
  `delivery.failed`, which could trigger the same workflow again, forever.
"""
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Automation, EmailCampaign, Lead, MessageDelivery

MAX_DEPTH = 2


def fire(event, payload=None, depth=0):
    """Queue every active workflow listening for this event. Returns how many."""
    if depth >= MAX_DEPTH:
        return 0
    automations = list(Automation.objects.filter(status="active", trigger_event=event))
    if not automations:
        return 0
    body = dict(payload or {}, source="event", event=event, depth=depth + 1)
    for automation in automations:
        transaction.on_commit(lambda a_id=automation.pk: _queue(a_id, body))
    return len(automations)


def _queue(automation_id, payload):
    from .tasks import execute_workflow
    execute_workflow.delay(automation_id, payload)


@receiver(pre_save, sender=Lead, dispatch_uid="core.lead_snapshot")
def lead_snapshot(sender, instance, **kwargs):
    instance._had_consent = bool(
        instance.pk and Lead.objects.filter(pk=instance.pk).exclude(consent_at=None).exists()
    )


@receiver(post_save, sender=Lead, dispatch_uid="core.lead_events")
def lead_events(sender, instance, created, **kwargs):
    if created:
        fire("lead.created", {"lead_id": instance.pk, "email": instance.email})
    # Consent is the moment a lead becomes contactable, so it gets its own event,
    # raised only on the transition from no-consent to consent.
    if instance.consent_at and not getattr(instance, "_had_consent", False):
        fire("lead.consented", {"lead_id": instance.pk, "email": instance.email})


@receiver(pre_save, sender=EmailCampaign, dispatch_uid="core.campaign_snapshot")
def campaign_snapshot(sender, instance, **kwargs):
    instance._was_sent = bool(
        instance.pk and EmailCampaign.objects.filter(pk=instance.pk, status="sent").exists()
    )


@receiver(post_save, sender=EmailCampaign, dispatch_uid="core.campaign_events")
def campaign_events(sender, instance, created, **kwargs):
    if instance.status == "sent" and not getattr(instance, "_was_sent", False):
        fire("campaign.sent", {"campaign_id": instance.pk, "recipients": instance.recipients,
                               "failures": instance.failures})


@receiver(post_save, sender=MessageDelivery, dispatch_uid="core.delivery_events")
def delivery_events(sender, instance, created, **kwargs):
    if instance.status != "failed":
        return
    # Deliveries written by a workflow carry the depth that produced them.
    depth = int((instance.metadata or {}).get("depth", 0))
    fire("delivery.failed", {"delivery_id": instance.pk, "recipient": instance.recipient,
                             "channel": instance.channel, "error": (instance.error or "")[:200]},
         depth=depth)
