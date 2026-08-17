"""Audience segments for email campaigns.

`EmailCampaign.segment` was a free-text field that nothing read — every campaign
went to every consented lead regardless of what it said. This resolves that text
into an actual queryset.

Grammar is deliberately small and readable, comma separated, all terms ANDed:

    status:qualified, market:Ireland      status and market
    source:Website                        acquisition channel
    wholesale / retail                    has a company / does not
    score>=70                             minimum lead score
    opened / clicked                      engaged with a previous campaign
    not opened / not clicked              did not
    all  (or blank)                       every consented lead

Consent is never optional: every segment starts from leads that have consented
and are not unsubscribed, so no rule can widen the audience beyond that.

The engagement terms are what make a drip sequence possible: "clicked" is a
follow-up to interested readers, "not opened" is a resend with a new subject
line to everyone who missed the first attempt.
"""
import re

from .models import Lead, MessageDelivery

SCORE_RULE = re.compile(r"^score\s*>=\s*(\d+)$", re.I)
FIELD_RULE = re.compile(r"^(status|market|source|company)\s*:\s*(.+)$", re.I)


def consented_leads():
    """The widest audience any campaign may ever reach."""
    return Lead.objects.exclude(consent_at=None).exclude(status="unsubscribed")


# Phrases that plainly mean "the whole consented list".
EVERYONE = {"all", "all leads", "everyone", "consented leads", "consented customers",
            "active consented customers", "all consented", "active customers"}

# Engagement terms -> (delivery field, negate, description).
ENGAGEMENT = {
    "opened": ("opened_at", False, "opened a previous campaign"),
    "clicked": ("clicked_at", False, "clicked a previous campaign"),
    "engaged": ("clicked_at", False, "clicked a previous campaign"),
    "not opened": ("opened_at", True, "never opened a campaign"),
    "unopened": ("opened_at", True, "never opened a campaign"),
    "not clicked": ("clicked_at", True, "never clicked a campaign"),
    "unengaged": ("opened_at", True, "never opened a campaign"),
}


def _engaged_addresses(field):
    """Addresses with at least one email delivery where `field` is set."""
    return (MessageDelivery.objects.filter(channel="email").exclude(**{field: None})
            .values_list("recipient", flat=True))


def resolve(segment):
    """Return (queryset, description, unknown_terms) for a segment string."""
    queryset = consented_leads()
    text = (segment or "").strip()
    if not text or text.lower() in EVERYONE:
        return queryset, "every consented lead", []

    described, unknown = [], []
    for raw in text.split(","):
        term = raw.strip()
        if not term:
            continue
        lowered = term.lower()

        if lowered in {"wholesale", "has company", "companies"}:
            queryset = queryset.exclude(company=""); described.append("wholesale (has a company)")
        elif lowered in {"retail", "no company", "individuals"}:
            queryset = queryset.filter(company=""); described.append("retail (no company)")
        elif lowered in ENGAGEMENT:
            field, negate, label = ENGAGEMENT[lowered]
            addresses = list(_engaged_addresses(field))
            queryset = queryset.exclude(email__in=addresses) if negate else queryset.filter(email__in=addresses)
            described.append(label)
        elif match := SCORE_RULE.match(term):
            queryset = queryset.filter(score__gte=int(match.group(1)))
            described.append(f"score at least {match.group(1)}")
        elif match := FIELD_RULE.match(term):
            field, value = match.group(1).lower(), match.group(2).strip()
            queryset = queryset.filter(**{f"{field}__iexact": value})
            described.append(f"{field} is {value}")
        else:
            # An unrecognised term must narrow to nothing rather than silently
            # falling back to "everyone" — that is how the old behaviour mailed
            # the whole list.
            unknown.append(term)

    if unknown:
        return queryset.none(), "no recipients (unrecognised segment)", unknown
    return queryset, " and ".join(described), []


def describe(segment):
    """Short human summary plus the recipient count, for the campaign UI."""
    queryset, description, unknown = resolve(segment)
    return {"count": queryset.count(), "description": description, "unknown": unknown}
