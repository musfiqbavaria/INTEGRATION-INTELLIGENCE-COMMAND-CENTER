"""Owner alerting and one-click approvals.

The console's only outbound signal was the 07:00 digest, so a critical item
raised at 07:05 waited most of a day to be seen. Two additions:

* `send_critical_alert` pushes critical items out immediately, over email and —
  when Meta credentials are configured — WhatsApp.
* Signed action links let the owner resolve an item or approve a decision from
  the message itself.

On the security of those links: the signature *is* the credential, exactly as
in a password-reset mail, so it is time-limited, single-purpose (one record,
one action), and every use is written to the audit log with the requesting IP.

`GET` on an action link never changes anything — it renders a confirmation the
owner has to submit. Security appliances fetch every URL in an inbound message,
and a mutating `GET` would let a scanner approve decisions on its own.
"""
from django.conf import settings
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

from .models import AiDecision, AttentionItem, AuditLog, MessageDelivery

ACTION_SALT = "core.alerts.action"
ACTION_MAX_AGE = 7 * 24 * 3600

# Which records can be actioned from a message, and what each action means.
TARGETS = {
    "attention": {
        "model": AttentionItem,
        "label": "attention item",
        "title": lambda obj: obj.title,
        "actions": {"resolve": "resolved", "dismiss": "dismissed"},
    },
    "decision": {
        "model": AiDecision,
        "label": "AI decision",
        "title": lambda obj: f"{obj.decision_id} — {obj.title}",
        "actions": {"approve": "approved", "reject": "rejected"},
    },
}


def action_token(target, pk, action):
    return signing.dumps({"t": target, "p": pk, "a": action}, salt=ACTION_SALT)


def action_url(target, pk, action, base=None):
    base = (base or getattr(settings, "SITE_URL", "") or "").rstrip("/")
    return f"{base}{reverse('owner-action', args=[action_token(target, pk, action)])}"


def resolve_token(payload, max_age=ACTION_MAX_AGE):
    """Return (target_key, object, action) for a signed link, or None."""
    try:
        data = signing.loads(payload, salt=ACTION_SALT, max_age=max_age)
    except signing.BadSignature:
        return None
    target = TARGETS.get(data.get("t"))
    if target is None or data.get("a") not in target["actions"]:
        return None
    obj = target["model"].objects.filter(pk=data.get("p")).first()
    if obj is None:
        return None
    return data["t"], obj, data["a"]


def apply_action(target_key, obj, action, request=None, user=None):
    """Perform an approved action and record who did it. Returns the new status."""
    target = TARGETS[target_key]
    status = target["actions"][action]
    obj.status = status
    fields = ["status"]
    if hasattr(obj, "decided_at"):
        obj.decided_at = timezone.now()
        fields.append("decided_at")
    if hasattr(obj, "updated_at"):
        fields.append("updated_at")
    obj.save(update_fields=fields)
    AuditLog.objects.create(
        user=user if (user and user.is_authenticated) else None,
        event=f"{target_key}.{action}", path=request.path if request else "",
        method=request.method if request else "",
        ip_address=request.META.get("REMOTE_ADDR") if request else None,
        new_values={"record_id": obj.pk, "status": status, "via": "signed link"})
    return status


# --- outbound alerts -------------------------------------------------------

def _owner_address():
    return getattr(settings, "OWNER_EMAIL", "") or settings.DEFAULT_FROM_EMAIL


def send_critical_alert(item, base=None):
    """Email the owner about one critical item, with act-now links.

    `alerted_at` is stamped by the caller so a retry cannot mail twice.
    """
    context = {
        "item": item,
        "resolve_url": action_url("attention", item.pk, "resolve", base),
        "dismiss_url": action_url("attention", item.pk, "dismiss", base),
        "generated_at": timezone.localtime(),
    }
    html = render_to_string("email/critical_alert.html", context)
    subject = f"[{item.severity.upper()}] {item.title}"[:180]
    message = EmailMultiAlternatives(subject, strip_tags(html), None, [_owner_address()])
    message.attach_alternative(html, "text/html")
    message.send()
    AuditLog.objects.create(event="alert.critical_sent",
                            new_values={"attention_id": item.pk, "title": item.title[:180]})
    return True


def send_whatsapp_alert(item):
    """Push the same alert over WhatsApp when Meta credentials are configured.

    Returns False rather than raising when WhatsApp is not set up: an alert
    that cannot reach one channel must still reach the other.
    """
    from .services import send_whatsapp_template
    from .models import WhatsAppTemplate
    number = (getattr(settings, "OWNER_WHATSAPP", "") or "").replace(" ", "").replace("+", "")
    if not number or not getattr(settings, "WHATSAPP_ACCESS_TOKEN", ""):
        return False
    template = WhatsAppTemplate.objects.filter(status="approved", category="utility").first()
    if template is None:
        return False
    delivery = MessageDelivery.objects.create(
        channel="whatsapp", recipient=number, template=template.name, status="sending",
        metadata={"alert": "critical", "attention_id": item.pk})
    try:
        delivery.external_id = send_whatsapp_template(number, template.name, template.language)
        delivery.status = "sent"
    except Exception as exc:
        delivery.status = "failed"
        delivery.error = str(exc)[:1000]
    delivery.save()
    return delivery.status == "sent"
