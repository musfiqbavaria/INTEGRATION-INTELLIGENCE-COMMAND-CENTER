"""Email engagement tracking.

`EmailCampaign.opens` and `.clicks` were rendered on the Executive Dashboard as
open and click rates, but nothing in the system ever incremented them — every
percentage shown was derived from a permanent zero. This module makes those
two numbers measured rather than decorative.

Three things are added to every campaign message:

* a 1x1 pixel whose URL carries the delivery token, recording opens;
* every `href` rewritten through a redirect that records the click and then
  forwards. The destination is signed, so the endpoint cannot be turned into an
  open redirect by editing the URL;
* `List-Unsubscribe` and `List-Unsubscribe-Post` headers plus a footer link.
  Gmail and Yahoo require one-click unsubscribe from bulk senders, and the
  console had no unsubscribe link in its campaign mail at all.

The delivery token is the only identifier in these URLs. The recipient's
address never appears in a link, so forwarding a campaign cannot leak it.
"""
import re
import secrets

from django.conf import settings
from django.core import signing
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from .models import EmailCampaign, EngagementEvent, MessageDelivery

# 43-byte transparent GIF. Smaller than any PNG and understood everywhere.
PIXEL = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00"
         b"\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")

LINK_SALT = "core.tracking.link"
HREF = re.compile(r'''(<a\b[^>]*?\bhref\s*=\s*)(["'])(.*?)\2''', re.I | re.S)
# Anything that is not a destination we can meaningfully track.
SKIP_PREFIXES = ("mailto:", "tel:", "sms:", "#", "javascript:", "data:", "{{", "{%")

# Security appliances and chat clients that fetch every link in a message to
# inspect it. Their hits are stored, so nothing is lost, but they are not
# counted towards a campaign's engagement — otherwise a single scanned message
# reads as a fully engaged recipient.
SCANNERS = ("barracuda", "proofpoint", "mimecast", "symantec", "forcepoint",
            "microsoft-preview", "skypeuripreview", "slackbot", "bitdefender",
            "trendmicro", "safelinks")


def new_token():
    """An unguessable identifier for one delivery."""
    return secrets.token_urlsafe(24)


def site_url():
    return (getattr(settings, "SITE_URL", "") or "").rstrip("/")


def sign_target(url):
    return signing.dumps(url, salt=LINK_SALT)


def unsign_target(payload, max_age=None):
    """The destination for a click, or None when the signature does not hold."""
    try:
        return signing.loads(payload, salt=LINK_SALT, max_age=max_age)
    except signing.BadSignature:
        return None


def pixel_url(token, base=None):
    return f"{base or site_url()}{reverse('track-open', args=[token])}"


def click_url(token, target, base=None):
    return f"{base or site_url()}{reverse('track-click', args=[token])}?u={sign_target(target)}"


def unsubscribe_url(token, base=None):
    return f"{base or site_url()}{reverse('track-unsubscribe', args=[token])}"


def is_scanner(user_agent):
    agent = (user_agent or "").lower()
    return any(marker in agent for marker in SCANNERS)


def decorate(html, token, base=None, sender=""):
    """Return the campaign body with click tracking, an unsubscribe footer and
    the open pixel. Called once per recipient, because every URL carries that
    recipient's own delivery token."""
    base = base or site_url()
    tracked_prefix = f"{base}/e/"

    def rewrite(match):
        prefix, quote, target = match.group(1), match.group(2), match.group(3).strip()
        if not target or target.lower().startswith(SKIP_PREFIXES) or target.startswith(tracked_prefix):
            return match.group(0)
        return f"{prefix}{quote}{click_url(token, target, base)}{quote}"

    body = HREF.sub(rewrite, html or "")
    return body + _footer(token, base, sender) + _pixel_tag(token, base)


def _footer(token, base, sender):
    who = sender or "us"
    return ('<p style="margin:28px 0 0;padding-top:14px;border-top:1px solid #dde4e6;'
            'font:12px/1.6 Arial,Helvetica,sans-serif;color:#6c7d87">'
            f"You are receiving this because you consented to marketing from {who}. "
            f'<a href="{unsubscribe_url(token, base)}" style="color:#6c7d87">Unsubscribe</a>.</p>')


def _pixel_tag(token, base):
    return (f'<img src="{pixel_url(token, base)}" width="1" height="1" alt="" '
            'style="display:none;width:1px;height:1px">')


def unsubscribe_headers(token, base=None):
    """RFC 8058 one-click unsubscribe, plus the mailto fallback older clients use."""
    address = getattr(settings, "OWNER_EMAIL", "") or settings.DEFAULT_FROM_EMAIL
    return {
        "List-Unsubscribe": f"<{unsubscribe_url(token, base)}>, <mailto:{address}?subject=unsubscribe>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def _agent(request):
    return (request.headers.get("User-Agent", "") if request else "")[:300]


def _ip(request):
    return request.META.get("REMOTE_ADDR") if request else None


def record(delivery, kind, url="", request=None):
    """Store one engagement event and keep the denormalised counters in step.

    Returns True when this was the recipient's first event of that kind, which
    is what the campaign-level rates count. Repeat opens are kept on the
    delivery so raw volume is still available, but a rate above 100% would be
    meaningless.
    """
    agent = _agent(request)
    counted = not is_scanner(agent)
    EngagementEvent.objects.create(delivery=delivery, kind=kind, url=(url or "")[:600],
                                   user_agent=agent, ip_address=_ip(request))
    if not counted:
        return False

    now = timezone.now()
    first_open = delivery.opened_at is None
    first_click = delivery.clicked_at is None
    fields = {}

    if kind == "open":
        fields = {"open_count": F("open_count") + 1, "opened_at": delivery.opened_at or now}
        first = first_open
    elif kind == "click":
        # A click proves the message was read even when the pixel was blocked.
        fields = {"click_count": F("click_count") + 1, "clicked_at": delivery.clicked_at or now,
                  "opened_at": delivery.opened_at or now}
        first = first_click
    else:
        first = True

    if fields:
        MessageDelivery.objects.filter(pk=delivery.pk).update(**fields)

    campaign_id = (delivery.metadata or {}).get("campaign_id")
    if campaign_id:
        bump = {}
        if kind == "open" and first_open:
            bump["opens"] = F("opens") + 1
        elif kind == "click":
            if first_click:
                bump["clicks"] = F("clicks") + 1
            if first_open:
                bump["opens"] = F("opens") + 1
        elif kind == "unsubscribe":
            bump["unsubscribes"] = F("unsubscribes") + 1
        if bump:
            EmailCampaign.objects.filter(pk=campaign_id).update(**bump)

    # A click is the only engagement signal reliable enough to act on: image
    # proxies open every message, but nothing clicks a link on its own.
    if kind == "click" and first_click:
        from .signals import fire
        fire("lead.engaged", {"email": delivery.recipient, "delivery_id": delivery.pk,
                              "campaign_id": campaign_id, "url": url[:200]})
    return first
