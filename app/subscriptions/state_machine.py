"""Subscription state machine.

Every allowed transition is declared explicitly in ``SUBSCRIPTION_TRANSITIONS``
with the trigger that causes it noted alongside. ``transition_subscription`` is
the *only* sanctioned way to change ``Subscription.status``: it validates the
move, applies it, and writes an ``AuditLog`` row in a single atomic commit.

Allowed transitions (trigger in parentheses):

    incomplete         -> active              (first payment succeeded)
    incomplete         -> incomplete_expired  (first-invoice expiry timer fired unpaid)
    incomplete         -> canceled            (abandoned before first payment)
    trialing           -> active              (trial ended, payment method charged)
    trialing           -> paused              (trial ended, no valid payment method)
    trialing           -> canceled            (canceled during trial)
    active             -> past_due            (a renewal charge failed)
    active             -> paused              (explicitly paused)
    active             -> completed           (final installment invoice paid)
    active             -> canceled            (canceled while active)
    past_due           -> active              (a dunning retry succeeded)
    past_due           -> unpaid              (all dunning retries exhausted)
    past_due           -> canceled            (canceled while past due)
    unpaid             -> active              (recovered via manual payment)
    unpaid             -> canceled            (cancellation from unpaid)
    paused             -> active              (resumed)
    paused             -> canceled            (canceled while paused)

Terminal states (no outgoing transitions): incomplete_expired, canceled, completed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditEntityType
from app.audit.service import record_transition
from app.core.exceptions import InvalidStateTransition
from app.subscriptions.models import Subscription, SubscriptionStatus

S = SubscriptionStatus

SUBSCRIPTION_TRANSITIONS: dict[SubscriptionStatus, set[SubscriptionStatus]] = {
    S.incomplete: {S.active, S.incomplete_expired, S.canceled},
    S.trialing: {S.active, S.paused, S.canceled},
    S.active: {S.past_due, S.paused, S.completed, S.canceled},
    S.past_due: {S.active, S.unpaid, S.canceled},
    S.unpaid: {S.active, S.canceled},
    S.paused: {S.active, S.canceled},
    # Terminal states.
    S.incomplete_expired: set(),
    S.canceled: set(),
    S.completed: set(),
}


def is_allowed(old: SubscriptionStatus, new: SubscriptionStatus) -> bool:
    return new in SUBSCRIPTION_TRANSITIONS.get(old, set())


async def transition_subscription(
    session: AsyncSession,
    subscription: Subscription,
    new_status: SubscriptionStatus,
    *,
    reason: str | None = None,
    actor: str = "system",
) -> Subscription:
    """Validate, apply, and audit a subscription status change (atomic commit).

    Raises ``InvalidStateTransition`` if the move is not allowed — the status is
    left untouched and no audit row is written.
    """
    old_status = subscription.status
    if not is_allowed(old_status, new_status):
        raise InvalidStateTransition("subscription", old_status, new_status)

    subscription.status = new_status
    if new_status is S.canceled and subscription.canceled_at is None:
        subscription.canceled_at = datetime.now(timezone.utc)

    record_transition(
        session,
        entity_type=AuditEntityType.subscription,
        entity_id=subscription.id,
        tenant_id=subscription.tenant_id,
        old_status=old_status.value,
        new_status=new_status.value,
        reason=reason,
        actor=actor,
    )

    await session.commit()
    await session.refresh(subscription)
    return subscription
