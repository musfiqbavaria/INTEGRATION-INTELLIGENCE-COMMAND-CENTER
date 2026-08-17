"""Currency conversion for multi-entity reporting.

Two rules, both deliberate:

* A conversion uses the newest rate dated on or before the record's own date,
  never today's rate. Historical figures must not move when a new rate lands.
* A missing rate returns None rather than the unconverted number. Treating one
  US dollar as one euro because nobody loaded a rate is the kind of quiet error
  that reaches a board pack.
"""
from decimal import Decimal, ROUND_HALF_UP

from .models import FxRate

CENTS = Decimal("0.01")


def rate_for(currency, base="EUR", on=None):
    """The applicable rate converting `currency` into `base`, or None."""
    if not currency or currency == base:
        return Decimal("1")
    rates = FxRate.objects.filter(base=base, currency=currency)
    if on:
        rates = rates.filter(as_of__lte=on)
    row = rates.order_by("-as_of").first()
    return row.rate if row else None


def to_base(amount, currency, base="EUR", on=None):
    """`amount` in `base` currency, or None when no rate is on file."""
    rate = rate_for(currency, base, on)
    if rate is None:
        return None
    return (Decimal(amount or 0) * rate).quantize(CENTS, rounding=ROUND_HALF_UP)


def missing_rates(currencies, base="EUR", on=None):
    """Currencies among `currencies` that cannot be converted yet."""
    return sorted({c for c in currencies if c and c != base and rate_for(c, base, on) is None})
