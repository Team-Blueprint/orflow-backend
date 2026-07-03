"""Dunning orchestration .

This module owns what happens *after* a charge fails: opening the dunning
cycle, scheduling retries per :mod:`app.dunning.policy`, and ultimately
revoking access when the schedule is exhausted. It is called from both failure
sites — the billing-cycle worker (sync renewal charge) and the inbound webhook
handler (async charge outcome) — so the recovery rules live in exactly one
place.

State-machine contract: subscriptions only enter dunning from ``active`` (first
failure -> ``past_due``) and leave it for ``unpaid`` once retries are exhausted.
All status changes go through ``transition_subscription`` so they stay audited.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dunning import policy
from app.invoices.models import Invoice
from app.providers.base import FailureReason
from app.subscriptions.models import Subscription, SubscriptionStatus
from app.subscriptions.state_machine import transition_subscription
from app.webhooks.models import PaymentAttempt
from app.webhooks.outbound import enqueue_webhook_event

logger = logging.getLogger(__name__)


async def _count_retries(session: AsyncSession, invoice_id) -> int:
    """Number of retry attempts already recorded for this invoice."""
    stmt = (
        select(func.count())
        .select_from(PaymentAttempt)
        .where(
            PaymentAttempt.invoice_id == invoice_id,
            PaymentAttempt.is_retry.is_(True),
        )
    )
    result = await session.execute(stmt)
    return int(result.scalar_one() or 0)


async def clear_dunning(session: AsyncSession, invoice: Invoice) -> None:
    """Reset an invoice's dunning state after a successful charge."""
    invoice.next_retry_at = None
    invoice.dunning_failure_reason = None
    await session.commit()
    await session.refresh(invoice)


async def open_or_advance_dunning(
    session: AsyncSession,
    *,
    invoice: Invoice,
    subscription: Subscription | None,
    failure_reason: FailureReason | None,
    actor: str = "system",
) -> None:
    """Drive the dunning cycle after a failed charge for ``invoice``.

    The caller must already have recorded the failed ``PaymentAttempt``. This
    function then, depending on the FailureReason policy and how many retries
    have run:

      * opens the cycle on the first failure (``active`` -> ``past_due``),
      * schedules the next retry by setting ``invoice.next_retry_at``,
      * flags the invoice for a payment-method update (no auto-retry), or
      * exhausts the schedule and revokes access (``past_due`` -> ``unpaid``).

    An outbound webhook event is enqueued at every stage transition.
    """
    # Step-up auth is not a billing failure — surfaced elsewhere, never dunned.
    if failure_reason is FailureReason.requires_action:
        logger.info("Invoice %s needs step-up auth; not entering dunning", invoice.id)
        return

    # Dunning only applies to a live subscription that is active or already
    # past_due. Incomplete first-charge failures are handled by the expiry
    # timer, not here.
    if subscription is None or subscription.status not in (
        SubscriptionStatus.active,
        SubscriptionStatus.past_due,
    ):
        logger.info(
            "Subscription for invoice %s not eligible for dunning (status=%s)",
            invoice.id,
            getattr(subscription, "status", None),
        )
        return

    # The reason that opened the cycle anchors the whole retry schedule.
    if invoice.dunning_failure_reason is None:
        invoice.dunning_failure_reason = failure_reason or FailureReason.generic_decline
    anchor_reason = invoice.dunning_failure_reason

    retries_done = await _count_retries(session, invoice.id)
    gap_days = policy.next_retry_gap_days(anchor_reason, retries_done)
    now = datetime.now(timezone.utc)

    # First failure of an active subscription opens the cycle. Access stays
    # provisioned while past_due; the customer is notified via the webhook.
    opened = subscription.status is SubscriptionStatus.active
    if opened:
        await transition_subscription(
            session,
            subscription,
            SubscriptionStatus.past_due,
            reason=f"payment_failed:{anchor_reason.value}",
            actor=actor,
        )
        await enqueue_webhook_event(
            session,
            tenant_id=invoice.tenant_id,
            event_type="subscription.past_due",
            payload={
                "subscription_id": str(subscription.id),
                "reason": anchor_reason.value,
                "requires_payment_method": policy.requires_payment_method_update(anchor_reason),
            },
        )

    if gap_days is not None:
        # More retries to go — schedule the next one.
        invoice.next_retry_at = now + timedelta(days=gap_days)
        await session.commit()
        await session.refresh(invoice)
        logger.info(
            "Scheduled dunning retry #%d for invoice %s at %s (reason=%s)",
            retries_done + 1,
            invoice.id,
            invoice.next_retry_at,
            anchor_reason.value,
        )
        await enqueue_webhook_event(
            session,
            tenant_id=invoice.tenant_id,
            event_type="invoice.payment_failed",
            payload={
                "invoice_id": str(invoice.id),
                "reason": anchor_reason.value,
                "next_retry_at": invoice.next_retry_at.isoformat(),
            },
        )
        return

    # No (further) retries scheduled.
    invoice.next_retry_at = None
    await session.commit()
    await session.refresh(invoice)

    await enqueue_webhook_event(
        session,
        tenant_id=invoice.tenant_id,
        event_type="invoice.payment_failed",
        payload={
            "invoice_id": str(invoice.id),
            "reason": anchor_reason.value,
            "next_retry_at": None,
        },
    )

    # A hard payment-method failure with no retries attempted: keep access
    # (stay past_due) and wait for the customer to supply a new card, rather
    # than revoking immediately. The grace-period worker is the backstop.
    if policy.requires_payment_method_update(anchor_reason) and retries_done == 0:
        logger.info(
            "Invoice %s flagged for payment-method update; no retry scheduled",
            invoice.id,
        )
        return

    # Retries exhausted — this is the access-revocation point (not cancellation).
    await transition_subscription(
        session,
        subscription,
        SubscriptionStatus.unpaid,
        reason=f"dunning_exhausted:{anchor_reason.value}",
        actor=actor,
    )
    await enqueue_webhook_event(
        session,
        tenant_id=invoice.tenant_id,
        event_type="subscription.unpaid",
        payload={"subscription_id": str(subscription.id), "reason": anchor_reason.value},
    )
    logger.info("Dunning exhausted for invoice %s; subscription %s -> unpaid", invoice.id, subscription.id)
