"""Tests for the invoice state machine + audit logging."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.audit.models import AuditEntityType, AuditLog
from app.core.context import current_tenant_id
from app.core.exceptions import InvalidStateTransition
from app.invoices.models import Invoice, InvoiceStatus
from app.invoices.state_machine import (
    INVOICE_TRANSITIONS,
    is_allowed,
    transition_invoice,
)

I = InvoiceStatus
TERMINAL = {I.paid, I.void, I.uncollectible}


async def _make_invoice(session, status: I = I.draft) -> Invoice:
    inv = Invoice(
        tenant_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        subscription_id=uuid.uuid4(),
        status=status,
        amount_due=250000,
        currency="NGN",
    )
    session.add(inv)
    await session.commit()
    await session.refresh(inv)
    return inv


async def _audit_rows(session, entity_id) -> list[AuditLog]:
    result = await session.execute(
        select(AuditLog).where(AuditLog.entity_id == entity_id)
    )
    return list(result.scalars().all())


# ----------------------------------------------------------------- pure map

def test_terminal_states_have_no_outgoing_transitions():
    for state in TERMINAL:
        assert INVOICE_TRANSITIONS[state] == set()


def test_every_status_is_present_in_the_map():
    assert set(INVOICE_TRANSITIONS.keys()) == set(InvoiceStatus)


def test_is_allowed_matches_map():
    assert is_allowed(I.draft, I.open)
    assert not is_allowed(I.draft, I.paid)


# -------------------------------------------------------- allowed transitions

@pytest.mark.parametrize(
    "start,target",
    [(start, target) for start, targets in INVOICE_TRANSITIONS.items() for target in targets],
)
async def test_allowed_transition_applies_and_audits(db_session, start, target):
    inv = await _make_invoice(db_session, start)
    returned = await transition_invoice(db_session, inv, target, actor="system")

    assert returned.status is target
    rows = await _audit_rows(db_session, inv.id)
    assert len(rows) == 1
    assert rows[0].entity_type is AuditEntityType.invoice
    assert rows[0].old_status == start.value
    assert rows[0].new_status == target.value


async def test_paid_sets_paid_at(db_session):
    inv = await _make_invoice(db_session, I.open)
    assert inv.paid_at is None
    await transition_invoice(db_session, inv, I.paid)
    assert inv.paid_at is not None


# ------------------------------------------------------ illegal transitions

async def test_illegal_transition_raises_and_leaves_state_untouched(db_session):
    inv = await _make_invoice(db_session, I.draft)
    with pytest.raises(InvalidStateTransition):
        await transition_invoice(db_session, inv, I.paid)
    await db_session.rollback()
    await db_session.refresh(inv)
    assert inv.status is I.draft
    assert await _audit_rows(db_session, inv.id) == []


@pytest.mark.parametrize("state", sorted(TERMINAL, key=lambda s: s.value))
async def test_terminal_states_reject_all_transitions(db_session, state):
    inv = await _make_invoice(db_session, state)
    for target in InvoiceStatus:
        if target is state:
            continue
        # Validation fails before any DB I/O, so no rollback is needed.
        with pytest.raises(InvalidStateTransition):
            await transition_invoice(db_session, inv, target)
    assert await _audit_rows(db_session, inv.id) == []
