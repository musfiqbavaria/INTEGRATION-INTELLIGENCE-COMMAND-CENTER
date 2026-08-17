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
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import (Automation, AttentionItem, Conversion, EmailCampaign, Lead, MessageDelivery,
                     Organisation)

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


@receiver(pre_save, sender=Conversion, dispatch_uid="core.conversion_base_amount")
def conversion_base_amount(sender, instance, **kwargs):
    """Convert to the entity's base currency at write time.

    Using the rate that applied on the day of the conversion, so a rate loaded
    next month cannot move last month's reported revenue. Left as None when no
    rate is on file — see the note on the field.
    """
    if instance.base_amount is not None:
        return
    from .money import to_base
    base = instance.organisation.base_currency if instance.organisation_id else "EUR"
    currency = instance.currency or base
    occurred = instance.occurred_at or timezone.now()
    instance.base_amount = to_base(instance.amount, currency, base, timezone.localtime(occurred).date())


@receiver(pre_save, sender=AttentionItem, dispatch_uid="core.attention_deadline")
def attention_deadline(sender, instance, **kwargs):
    """Turn an SLA into a real deadline, so escalation has something to measure."""
    if instance.sla_hours and not instance.due_at:
        instance.due_at = timezone.now() + timezone.timedelta(hours=instance.sla_hours)


@receiver(post_save, sender=Conversion, dispatch_uid="core.conversion_events")
def conversion_events(sender, instance, created, **kwargs):
    if created:
        fire("conversion.recorded", {"conversion_id": instance.pk, "amount": str(instance.amount),
                                     "currency": instance.currency,
                                     "lead_id": instance.lead_id, "email": instance.email})


@receiver(post_save, sender=Organisation, dispatch_uid="core.organisation_saved")
@receiver(post_delete, sender=Organisation, dispatch_uid="core.organisation_deleted")
def organisation_changed(sender, instance, **kwargs):
    """Adding the second entity switches strict scoping on everywhere at once,
    so the cached count cannot be allowed to outlive the change that matters."""
    from .tenancy import forget_count
    forget_count()


def stamp_organisation(sender, instance, **kwargs):
    """Give every business record an entity, including ones nobody passed one to.

    Background work has no request to read the active entity from — a workflow
    raising an owner summary, the bounce monitor, the consent sweep. Those
    records would carry a null tenant key and vanish the moment a second entity
    switched strict scoping on. One extra query, on creation only: once the key
    is set, later saves return on the first line.
    """
    from .tenancy import default_organisation
    if getattr(instance, "organisation_id", None):
        return
    default = default_organisation()
    if default is not None:
        instance.organisation = default


def _register_organisation_stamp():
    """Connect the stamp to every model carrying the tenant key.

    Discovered from the app registry rather than listed by hand, so a model
    added later is covered without anyone remembering to wire it up.
    """
    from django.apps import apps as registry
    from .tenancy import has_organisation
    for model in registry.get_app_config("core").get_models():
        if has_organisation(model):
            pre_save.connect(stamp_organisation, sender=model,
                             dispatch_uid=f"core.stamp_organisation.{model._meta.model_name}")


_register_organisation_stamp()
