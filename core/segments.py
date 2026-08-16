"""Audience segments for email campaigns.

`EmailCampaign.segment` was a free-text field that nothing read — every campaign
went to every consented lead regardless of what it said. This resolves that text
into an actual queryset.

Grammar is deliberately small and readable, comma separated, all terms ANDed:

    status:qualified, market:Ireland      status and market
    source:Website                        acquisition channel
    wholesale / retail                    has a company / does not
    score>=70                             minimum lead score
    all  (or blank)                       every consented lead

Consent is never optional: every segment starts from leads that have consented
and are not unsubscribed, so no rule can widen the audience beyond that.
"""
import re

from .models import Lead

SCORE_RULE = re.compile(r"^score\s*>=\s*(\d+)$", re.I)
FIELD_RULE = re.compile(r"^(status|market|source|company)\s*:\s*(.+)$", re.I)


def consented_leads():
    """The widest audience any campaign may ever reach."""
    return Lead.objects.exclude(consent_at=None).exclude(status="unsubscribed")


# Phrases that plainly mean "the whole consented list".
EVERYONE = {"all", "all leads", "everyone", "consented leads", "consented customers",
            "active consented customers", "all consented", "active customers"}


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
