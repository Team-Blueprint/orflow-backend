"""Proration calculator.

A pure computation of what a mid-cycle plan change costs. No
DB, no HTTP — just arithmetic over two plans and how much of the current cycle
is left. 

Model: time-based proration . The customer is credited for the
unused portion of the *old* plan and charged for the remaining portion of the
*new* plan, both pro-rated by the same ``days_remaining / total_days`` fraction.
The net is what we actually collect; the two halves are surfaced as explicit
line items so the invoice reads as
"credit for unused time on Plan A + charge for remaining time on Plan B" —
never a silent balance adjustment.

"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProrationLineItem:
    """A single explicit, auditable entry on a proration invoice.

    ``amount_minor`` is signed: negative for a credit, positive for a charge.
    """

    description: str
    amount_minor: int


@dataclass(frozen=True)
class ProrationResult:
    currency: str
    credit_minor: int  # magnitude (>= 0) of the credit for the unused old plan
    charge_minor: int  # magnitude (>= 0) of the charge for the remaining new plan
    net_minor: int  # charge - credit; negative on a downgrade
    line_items: list[ProrationLineItem]


def _prorate(amount_minor: int, days_remaining: int, total_days: int) -> int:
    """Portion of ``amount_minor`` owed for ``days_remaining`` of ``total_days``.

    Days are clamped to ``[0, total_days]`` so out-of-range inputs (a clock skew,
    an already-elapsed period) can never produce a negative or inflated amount.
    """
    if total_days <= 0:
        return 0
    days = max(0, min(days_remaining, total_days))
    return round(amount_minor * days / total_days)


def calculate_proration(
    old_plan,
    new_plan,
    days_remaining_in_cycle: int,
    total_days_in_cycle: int,
) -> ProrationResult:
    """Credit the unused old plan and charge the remaining new plan, pro-rated.

    ``old_plan``/``new_plan`` only need ``.amount`` (minor units), ``.currency``
    and ``.name``. Raises ``ValueError`` if the two plans use different
    currencies — cross-currency proration is not a silent conversion.
    """
    if old_plan.currency != new_plan.currency:
        raise ValueError("Cannot prorate across different currencies")

    credit = _prorate(old_plan.amount, days_remaining_in_cycle, total_days_in_cycle)
    charge = _prorate(new_plan.amount, days_remaining_in_cycle, total_days_in_cycle)
    net = charge - credit

    line_items = [
        ProrationLineItem(
            description=f"Credit for unused time on {old_plan.name}",
            amount_minor=-credit,
        ),
        ProrationLineItem(
            description=f"Remaining time on {new_plan.name}",
            amount_minor=charge,
        ),
    ]

    return ProrationResult(
        currency=old_plan.currency,
        credit_minor=credit,
        charge_minor=charge,
        net_minor=net,
        line_items=line_items,
    )
