"""Pure-logic unit tests for the dunning retry policy .

These cover the day-1/3/7 edge cases the build plan calls out explicitly, with
no DB or HTTP in sight.
"""

import pytest

from app.dunning import policy
from app.providers.base import FailureReason


def test_insufficient_funds_schedule():
    assert policy.get_retry_schedule(FailureReason.insufficient_funds) == [1, 3, 7]


@pytest.mark.parametrize(
    "retries_done,expected_gap",
    [(0, 1), (1, 2), (2, 4), (3, None), (4, None)],
)
def test_insufficient_funds_gaps(retries_done, expected_gap):
    # Cumulative offsets [1, 3, 7] -> incremental gaps 1, 2, 4, then exhausted.
    assert policy.next_retry_gap_days(FailureReason.insufficient_funds, retries_done) == expected_gap


def test_do_not_honor_retries_once_then_stops():
    assert policy.get_retry_schedule(FailureReason.do_not_honor) == [1]
    assert policy.next_retry_gap_days(FailureReason.do_not_honor, 0) == 1
    assert policy.next_retry_gap_days(FailureReason.do_not_honor, 1) is None


@pytest.mark.parametrize(
    "retries_done,expected_gap",
    [(0, 3), (1, 4), (2, None)],
)
def test_generic_decline_gaps(retries_done, expected_gap):
    assert policy.next_retry_gap_days(FailureReason.generic_decline, retries_done) == expected_gap


def test_expired_card_no_retry_flag_for_update():
    assert policy.get_retry_schedule(FailureReason.expired_card) == []
    assert policy.next_retry_gap_days(FailureReason.expired_card, 0) is None
    assert policy.is_retryable(FailureReason.expired_card) is False
    assert policy.requires_payment_method_update(FailureReason.expired_card) is True


def test_invalid_payment_method_flag_for_update():
    assert policy.requires_payment_method_update(FailureReason.invalid_payment_method) is True
    assert policy.is_retryable(FailureReason.invalid_payment_method) is False


def test_requires_action_never_dunned():
    # Step-up auth is not a billing failure.
    assert policy.is_retryable(FailureReason.requires_action) is False
    assert policy.requires_payment_method_update(FailureReason.requires_action) is False


def test_unknown_and_none_use_default_schedule():
    assert policy.get_retry_schedule(None) == [3, 7]
    assert policy.get_retry_schedule(FailureReason.unknown) == [3, 7]


def test_negative_retries_completed_returns_none():
    assert policy.next_retry_gap_days(FailureReason.insufficient_funds, -1) is None


def test_get_retry_schedule_returns_copy():
    # Mutating the returned list must not corrupt the policy table.
    schedule = policy.get_retry_schedule(FailureReason.insufficient_funds)
    schedule.append(99)
    assert policy.get_retry_schedule(FailureReason.insufficient_funds) == [1, 3, 7]
