"""Pure-logic unit tests for the proration calculator.

Day 1, mid-cycle, last day, upgrade, downgrade, and clamp/edge cases — no DB.
"""

import pytest

from app.plans.models import Plan, PlanInterval
from app.proration.calculator import calculate_proration


def _plan(amount: int, name: str = "Plan", currency: str = "USD") -> Plan:
    # Unpersisted plain object — the calculator only reads amount/currency/name.
    return Plan(name=name, amount=amount, currency=currency, interval=PlanInterval.monthly)


def test_day_one_full_cycle_remaining():
    result = calculate_proration(_plan(1000, "Basic"), _plan(3000, "Pro"), 30, 30)
    assert result.credit_minor == 1000
    assert result.charge_minor == 3000
    assert result.net_minor == 2000
    assert result.line_items[0].amount_minor == -1000
    assert "Basic" in result.line_items[0].description
    assert result.line_items[1].amount_minor == 3000
    assert "Pro" in result.line_items[1].description


def test_mid_cycle_upgrade():
    result = calculate_proration(_plan(1000), _plan(3000), 15, 30)
    assert result.credit_minor == 500
    assert result.charge_minor == 1500
    assert result.net_minor == 1000


def test_last_day_no_value_left():
    result = calculate_proration(_plan(1000), _plan(3000), 0, 30)
    assert result.credit_minor == 0
    assert result.charge_minor == 0
    assert result.net_minor == 0


def test_downgrade_yields_negative_net():
    result = calculate_proration(_plan(3000), _plan(1000), 15, 30)
    assert result.credit_minor == 1500
    assert result.charge_minor == 500
    assert result.net_minor == -1000


def test_days_remaining_clamped_to_total():
    result = calculate_proration(_plan(1000), _plan(1000), 40, 30)
    assert result.credit_minor == 1000
    assert result.charge_minor == 1000
    assert result.net_minor == 0


def test_negative_days_remaining_clamped_to_zero():
    result = calculate_proration(_plan(1000), _plan(2000), -5, 30)
    assert result.credit_minor == 0
    assert result.charge_minor == 0
    assert result.net_minor == 0


def test_zero_total_days_no_division_error():
    result = calculate_proration(_plan(1000), _plan(2000), 0, 0)
    assert result.net_minor == 0


def test_rounding_to_nearest_minor_unit():
    # 1000 * 10 / 30 = 333.33... -> 333
    result = calculate_proration(_plan(1000), _plan(1000), 10, 30)
    assert result.credit_minor == 333
    assert result.charge_minor == 333
    assert result.net_minor == 0


def test_currency_mismatch_raises():
    with pytest.raises(ValueError):
        calculate_proration(_plan(1000, currency="USD"), _plan(1000, currency="NGN"), 15, 30)
