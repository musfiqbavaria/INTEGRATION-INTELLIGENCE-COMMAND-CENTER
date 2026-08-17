"""Revenue attribution, cohorts and forecasting.

`Campaign` and `FinancialRecord` were unconnected: revenue existed as a total
nobody could trace to the work that earned it, which is exactly why the
Executive Dashboard printed reconciliation warnings instead of an answer.

`Conversion` is the missing primitive — one payment, one lead, and the campaign
credited with it. Attribution is **last touch within a window**: the campaign a
lead most recently engaged with before paying gets the credit. That model is
simple enough to explain to an accountant and simple enough to audit, which
matters more here than a cleverer split nobody can reproduce.
"""
from decimal import Decimal

from django.db.models import Avg, Count, F, Sum
from django.utils import timezone

from .models import (Campaign, Conversion, EmailCampaign, FinancialRecord, Lead,
                     MessageDelivery)

# How far back a campaign may reach to claim credit for a payment.
ATTRIBUTION_WINDOW_DAYS = 30
# A forecast drawn from a handful of points is a straight line through noise.
MINIMUM_HISTORY_POINTS = 7


# --- attribution -----------------------------------------------------------

def last_touch(conversion, window_days=ATTRIBUTION_WINDOW_DAYS):
    """The email campaign this conversion should be credited to, or None.

    Preference order is click, then open, then plain delivery: a click is a
    deliberate act, an open may be an image proxy, and a delivery only proves
    the message was sent.
    """
    address = (conversion.email or (conversion.lead.email if conversion.lead else "")).strip()
    if not address:
        return None
    since = conversion.occurred_at - timezone.timedelta(days=window_days)
    # `has_key`, not `exclude(...=None)`: a JSON lookup for a *missing* key and
    # one for a stored null are not the same query, and only the first is meant.
    deliveries = (MessageDelivery.objects
                  .filter(channel="email", recipient__iexact=address, created_at__gte=since,
                          created_at__lte=conversion.occurred_at,
                          metadata__has_key="campaign_id"))
    for field in ("clicked_at", "opened_at", "created_at"):
        touch = deliveries.exclude(**{field: None}).order_by(f"-{field}").first()
        if touch is not None:
            campaign_id = (touch.metadata or {}).get("campaign_id")
            if campaign_id:
                return EmailCampaign.objects.filter(pk=campaign_id).first()
    return None


def attribute(conversion, window_days=ATTRIBUTION_WINDOW_DAYS):
    """Credit a conversion to a campaign. Returns the campaign, or None."""
    if conversion.attribution == "manual":
        return conversion.email_campaign
    campaign = last_touch(conversion, window_days)
    if campaign is None:
        Conversion.objects.filter(pk=conversion.pk).update(attribution="unattributed")
        return None
    Conversion.objects.filter(pk=conversion.pk).update(
        email_campaign=campaign, attribution="last-touch",
        channel=conversion.channel or "Email")
    conversion.refresh_from_db()
    return campaign


# --- reporting -------------------------------------------------------------

def _rate(part, whole):
    return round(float(part) / float(whole) * 100, 1) if whole else 0


def channel_performance():
    """Revenue, cost, ROI and conversion count per channel."""
    rows = (FinancialRecord.objects.values("channel")
            .annotate(revenue=Sum("revenue"), cost=Sum("cost"),
                      leads=Sum("leads"), customers=Sum("customers"))
            .order_by("-revenue"))
    out = []
    for row in rows:
        revenue = row["revenue"] or Decimal(0)
        cost = row["cost"] or Decimal(0)
        out.append({
            "channel": row["channel"] or "Unattributed",
            "revenue": revenue, "cost": cost, "profit": revenue - cost,
            "roi": round(float(revenue - cost) / float(cost) * 100, 1) if cost else None,
            "margin": _rate(revenue - cost, revenue),
            "leads": row["leads"] or 0, "customers": row["customers"] or 0,
            "conversion": _rate(row["customers"] or 0, row["leads"] or 0),
        })
    return out


def campaign_performance(limit=12):
    """Per-campaign engagement against the money it brought in."""
    revenue_by_campaign = {
        row["email_campaign"]: row["total"]
        for row in Conversion.objects.exclude(email_campaign=None)
        .values("email_campaign").annotate(total=Sum("base_amount"))
    }
    out = []
    for campaign in EmailCampaign.objects.exclude(sent_at=None).order_by("-sent_at")[:limit]:
        revenue = revenue_by_campaign.get(campaign.pk, Decimal(0))
        out.append({
            "campaign": campaign, "revenue": revenue,
            "open_rate": campaign.open_rate, "click_rate": campaign.click_rate,
            "revenue_per_recipient": (revenue / campaign.recipients) if campaign.recipients else None,
            "unsubscribe_rate": _rate(campaign.unsubscribes, campaign.recipients),
        })
    return out


def cohorts(months=6):
    """Leads grouped by the month they arrived, and what they went on to spend."""
    start = (timezone.localdate().replace(day=1)
             - timezone.timedelta(days=31 * (months - 1))).replace(day=1)
    buckets = {}
    for lead in Lead.objects.filter(created_at__date__gte=start).only("id", "created_at"):
        key = timezone.localtime(lead.created_at).strftime("%Y-%m")
        bucket = buckets.setdefault(key, {"month": key, "leads": 0, "customers": set(), "revenue": Decimal(0)})
        bucket["leads"] += 1
    for row in (Conversion.objects.filter(lead__created_at__date__gte=start)
                .values("lead_id", "lead__created_at", "base_amount")):
        key = timezone.localtime(row["lead__created_at"]).strftime("%Y-%m")
        bucket = buckets.get(key)
        if bucket is None:
            continue
        bucket["customers"].add(row["lead_id"])
        bucket["revenue"] += row["base_amount"] or Decimal(0)
    out = []
    for key in sorted(buckets):
        bucket = buckets[key]
        customers = len(bucket["customers"])
        out.append({
            "month": key, "leads": bucket["leads"], "customers": customers,
            "revenue": bucket["revenue"],
            "conversion": _rate(customers, bucket["leads"]),
            "value_per_lead": (bucket["revenue"] / bucket["leads"]) if bucket["leads"] else Decimal(0),
        })
    return out


def lifetime_value():
    """Average revenue per paying customer, and per lead acquired."""
    paying = (Conversion.objects.exclude(lead=None).values("lead_id")
              .annotate(total=Sum("base_amount")))
    totals = [row["total"] or Decimal(0) for row in paying]
    leads = Lead.objects.count()
    revenue = sum(totals, Decimal(0))
    repeat = (Conversion.objects.exclude(lead=None).values("lead_id")
              .annotate(n=Count("id")).filter(n__gte=2).count())
    return {
        "customers": len(totals),
        "revenue": revenue,
        "ltv": (revenue / len(totals)) if totals else None,
        "value_per_lead": (revenue / leads) if leads else None,
        "repeat_customers": repeat,
        "repeat_rate": _rate(repeat, len(totals)),
    }


def _least_squares(xs, ys):
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    variance = sum((x - mean_x) ** 2 for x in xs)
    if not variance:
        return 0.0, mean_y
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / variance
    return slope, mean_y - slope * mean_x


def _r_squared(xs, ys, slope, intercept):
    mean_y = sum(ys) / len(ys)
    total = sum((y - mean_y) ** 2 for y in ys)
    if not total:
        return 1.0
    residual = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    return max(0.0, 1 - residual / total)


def forecast(horizon_days=30, history_days=90):
    """Straight-line projection of revenue over the next `horizon_days`.

    Reports `available: False` rather than drawing a line through two points.
    `fit` is the r² of the trend — a low value means the history is too noisy
    for the number beside it to be worth acting on, and the UI says so.
    """
    since = timezone.localdate() - timezone.timedelta(days=history_days)
    rows = list(FinancialRecord.objects.filter(recorded_on__gte=since)
                .values("recorded_on").annotate(total=Sum("revenue")).order_by("recorded_on"))
    if len(rows) < MINIMUM_HISTORY_POINTS:
        return {"available": False, "points": len(rows), "needed": MINIMUM_HISTORY_POINTS,
                "horizon_days": horizon_days,
                "reason": f"{len(rows)} day(s) of revenue history; {MINIMUM_HISTORY_POINTS} needed"}
    xs = list(range(len(rows)))
    ys = [float(row["total"] or 0) for row in rows]
    slope, intercept = _least_squares(xs, ys)
    projected = [max(0.0, slope * (len(rows) + step) + intercept) for step in range(horizon_days)]
    recent = sum(ys[-min(len(ys), horizon_days):])
    total = sum(projected)
    return {
        "available": True, "points": len(rows), "horizon_days": horizon_days,
        "total": Decimal(str(round(total, 2))),
        "daily_average": Decimal(str(round(total / horizon_days, 2))),
        "direction": "rising" if slope > 0 else ("falling" if slope < 0 else "flat"),
        "change": round((total - recent) / recent * 100, 1) if recent else None,
        "fit": round(_r_squared(xs, ys, slope, intercept), 2),
    }


def attribution_gap():
    """Revenue that no campaign can currently claim — the honest denominator
    for any statement about marketing performance."""
    total = Conversion.objects.aggregate(t=Sum("base_amount"))["t"] or Decimal(0)
    unattributed = (Conversion.objects.filter(email_campaign=None)
                    .aggregate(t=Sum("base_amount"))["t"] or Decimal(0))
    return {"total": total, "unattributed": unattributed,
            "attributed": total - unattributed,
            "coverage": _rate(total - unattributed, total)}
