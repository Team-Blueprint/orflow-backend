"""Tests for the subscription state machine + audit logging."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.audit.models import AuditEntityType, AuditLog
from app.core.exceptions import InvalidStateTransition
from app.subscriptions.models import Subscription, SubscriptionStatus
from app.subscriptions.state_machine import (
    SUBSCRIPTION_TRANSITIONS,
    is_allowed,
    transition_subscription,
)

S = SubscriptionStatus
TERMINAL = {S.incomplete_expired, S.canceled, S.completed}


async def _make_subscription(session, status: S = S.incomplete) -> Subscription:
    sub = Subscription(
        tenant_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        plan_id=uuid.uuid4(),
        status=status,
    )
    session.add(sub)
    await session.commit()
    await session.refresh(sub)
    return sub


async def _audit_rows(session, entity_id) -> list[AuditLog]:
    result = await session.execute(
        select(AuditLog).where(AuditLog.entity_id == entity_id)
    )
    return list(result.scalars().all())


# ----------------------------------------------------------------- pure map

def test_terminal_states_have_no_outgoing_transitions():
    for state in TERMINAL:
        assert SUBSCRIPTION_TRANSITIONS[state] == set()


def test_every_status_is_present_in_the_map():
    assert set(SUBSCRIPTION_TRANSITIONS.keys()) == set(SubscriptionStatus)


def test_is_allowed_matches_map():
    assert is_allowed(S.incomplete, S.active)
    assert not is_allowed(S.active, S.incomplete)


# -------------------------------------------------------- allowed transitions

@pytest.mark.parametrize(
    "start,target",
    [(start, target) for start, targets in SUBSCRIPTION_TRANSITIONS.items() for target in targets],
)
async def test_allowed_transition_applies_and_audits(db_session, start, target):
    sub = await _make_subscription(db_session, start)
    returned = await transition_subscription(
        db_session, sub, target, reason="test", actor="worker"
    )

    assert returned.status is target
    rows = await _audit_rows(db_session, sub.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.entity_type is AuditEntityType.subscription
    assert row.entity_id == sub.id
    assert row.tenant_id == sub.tenant_id
    assert row.old_status == start.value
    assert row.new_status == target.value
    assert row.reason == "test"
    assert row.actor == "worker"


async def test_canceled_sets_canceled_at(db_session):
    sub = await _make_subscription(db_session, S.active)
    assert sub.canceled_at is None
    await transition_subscription(db_session, sub, S.canceled)
    assert sub.canceled_at is not None


# ------------------------------------------------------ illegal transitions

async def test_illegal_transition_raises_and_leaves_state_untouched(db_session):
    sub = await _make_subscription(db_session, S.active)
    with pytest.raises(InvalidStateTransition) as exc:
        await transition_subscription(db_session, sub, S.incomplete)

    assert exc.value.from_status == "active"
    assert exc.value.to_status == "incomplete"
    # Roll back the failed transaction before re-querying.
    await db_session.rollback()
    await db_session.refresh(sub)
    assert sub.status is S.active
    assert await _audit_rows(db_session, sub.id) == []


@pytest.mark.parametrize("state", sorted(TERMINAL, key=lambda s: s.value))
async def test_terminal_states_reject_all_transitions(db_session, state):
    sub = await _make_subscription(db_session, state)
    for target in SubscriptionStatus:
        if target is state:
            continue
        # Validation fails before any DB I/O, so no rollback is needed.
        with pytest.raises(InvalidStateTransition):
            await transition_subscription(db_session, sub, target)
    assert await _audit_rows(db_session, sub.id) == []
