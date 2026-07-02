"""Dunning retry policy.

Pure, side-effect-free mapping from an internal :class:`FailureReason` onto a
retry schedule. Keeping this free of any DB/HTTP access is deliberate: the
dunning rules are the most edge-case-prone part of the engine, so they are
exhaustively unit-tested in isolation (see ``tests/test_dunning_policy.py``).

A schedule is the list of *cumulative day offsets from the first failure* at
which the charge should be re-attempted. For example ``[1, 3, 7]`` means:
retry one day after the original failure, again on day 3, and a final time on
day 7 — after which the schedule is exhausted and the subscription moves to
``unpaid``.
"""

from __future__ import annotations

from app.providers.base import FailureReason

# Cumulative day offsets (from the first failure) at which to re-attempt.
RETRY_SCHEDULES: dict[FailureReason, list[int]] = {
    FailureReason.insufficient_funds: [1, 3, 7],
    FailureReason.do_not_honor: [1],
    FailureReason.generic_decline: [3, 7],
    FailureReason.card_declined: [3, 7],
    # Transport/provider hiccup — likely transient, retry promptly.
    FailureReason.processing_error: [1, 3, 7],
    FailureReason.unknown: [3, 7],
    # Hard failures: retrying the *same* card can never succeed. No retries —
    # the customer must supply a new payment method instead.
    FailureReason.expired_card: [],
    FailureReason.invalid_payment_method: [],
    # Not a billing failure: customer must complete step-up auth. Never dunned.
    FailureReason.requires_action: [],
}

# Applied when the reason is unknown to the table above.
DEFAULT_SCHEDULE: list[int] = [3, 7]

# Reasons that can never be recovered by retrying the same card.
_PAYMENT_METHOD_REASONS = frozenset(
    {FailureReason.expired_card, FailureReason.invalid_payment_method}
)


def get_retry_schedule(reason: FailureReason | None) -> list[int]:
    """Return the cumulative-day retry schedule for ``reason`` (a copy)."""
    if reason is None:
        return list(DEFAULT_SCHEDULE)
    return list(RETRY_SCHEDULES.get(reason, DEFAULT_SCHEDULE))


def next_retry_gap_days(reason: FailureReason | None, retries_completed: int) -> int | None:
    """Days to wait *from now* before the next retry, or ``None`` when the
    schedule for ``reason`` is exhausted.

    ``retries_completed`` is the number of retries already attempted: ``0``
    right after the first/original failure, ``1`` after the first retry, etc.
    Because the schedule is expressed as cumulative offsets from the first
    failure, the gap returned is the difference between the next milestone and
    the previous one (e.g. ``[1, 3, 7]`` yields gaps ``1, 2, 4``).
    """
    schedule = get_retry_schedule(reason)
    if retries_completed < 0 or retries_completed >= len(schedule):
        return None
    previous = schedule[retries_completed - 1] if retries_completed > 0 else 0
    return max(schedule[retries_completed] - previous, 0)


def requires_payment_method_update(reason: FailureReason | None) -> bool:
    """True when the only path to recovery is a new payment method (so the
    subscription should be flagged, not silently retried)."""
    return reason in _PAYMENT_METHOD_REASONS


def is_retryable(reason: FailureReason | None) -> bool:
    """True when ``reason`` has at least one scheduled retry."""
    return len(get_retry_schedule(reason)) > 0
