"""Dunning workers .

Two poll-based Arq cron jobs, mirroring the billing-cycle worker's shape:

``process_dunning_retries``
    Hourly. Picks up open invoices whose ``next_retry_at`` is due and re-charges
    them. Each retry is a fresh ``PaymentAttempt`` with its own idempotency key
    (``invoice_id + attempt_count``) so a retried provider call can never
    double-charge. Success recovers the subscription (``past_due`` -> ``active``)
    and advances the period; failure hands back to :mod:`app.dunning.service`,
    which schedules the next retry or exhausts to ``unpaid``.

``process_unpaid_grace``
    Daily. Cancels subscriptions that have sat in ``unpaid`` beyond the
    configured grace period — the timer-driven path of "cancellation from
    unpaid is a separate explicit step".
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.context import current_project_id, current_tenant_id
from app.customers.models import Customer
from app.db.database import AsyncSessionLocal
from app.dunning.service import clear_dunning, open_or_advance_dunning
from app.invoices.models import Invoice, InvoiceStatus
from app.invoices.state_machine import transition_invoice
from app.payment_methods.models import PaymentMethod
from app.plans.models import Plan
from app.providers.deps import get_payment_provider
from app.subscriptions.models import Subscription, SubscriptionStatus
from app.subscriptions.state_machine import transition_subscription
from app.webhooks.models import PaymentAttempt
from app.webhooks.outbound import enqueue_webhook_event
from app.worker.utils import advance_billing_period

logger = logging.getLogger(__name__)


async def process_dunning_retries(ctx: dict[str, Any]) -> None:
    """Re-attempt every open invoice whose scheduled retry is due."""
    logger.info("Starting dunning retry worker")
    provider = get_payment_provider()
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        stmt = select(Invoice).where(
            Invoice.status == InvoiceStatus.open,
            Invoice.next_retry_at.is_not(None),
            Invoice.next_retry_at <= now,
        )
        invoices = (await session.execute(stmt)).scalars().all()

        for invoice in invoices:
            token = current_tenant_id.set(invoice.tenant_id)
            proj_token = current_project_id.set(invoice.project_id)
            try:
                await _retry_invoice(session, provider, invoice)
            except Exception as exc:  # noqa: BLE001 — isolate one bad invoice
                logger.error("Dunning retry failed for invoice %s: %s", invoice.id, exc)
                await session.rollback()
            finally:
                current_tenant_id.reset(token)
                current_project_id.reset(proj_token)


async def _retry_invoice(session, provider, invoice: Invoice) -> None:
    subscription = (
        await session.get(Subscription, invoice.subscription_id)
        if invoice.subscription_id
        else None
    )

    # Only past_due subscriptions are still in dunning. If it recovered, was
    # canceled, or paused elsewhere, stop retrying this invoice.
    if subscription is None or subscription.status is not SubscriptionStatus.past_due:
        invoice.next_retry_at = None
        await session.commit()
        logger.info("Invoice %s no longer dunning; clearing retry", invoice.id)
        return

    payment_method = (
        await session.get(PaymentMethod, subscription.payment_method_id)
        if subscription.payment_method_id
        else None
    )
    if payment_method is None or not payment_method.provider_token:
        # Nothing to charge — stop retrying and let the grace timer take over.
        invoice.next_retry_at = None
        await session.commit()
        logger.warning("Invoice %s has no usable payment method; stopping retries", invoice.id)
        return

    customer = await session.get(Customer, subscription.customer_id)
    plan = await session.get(Plan, subscription.plan_id)
    if customer is None or plan is None:
        logger.error("Missing customer/plan for invoice %s; skipping", invoice.id)
        return

    idempotency_key = f"{invoice.id}-{invoice.attempt_count}"
    charge_result = await provider.charge_tokenized_card(
        token=payment_method.provider_token,
        amount_minor=invoice.amount_due,
        currency=invoice.currency,
        customer_email=customer.email,
        customer_id=str(customer.id),
        idempotency_key=idempotency_key,
    )

    attempt = PaymentAttempt(
        tenant_id=invoice.tenant_id,
        project_id=invoice.project_id,
        invoice_id=invoice.id,
        status=charge_result.status,
        failure_reason=charge_result.failure_reason,
        provider_reference=charge_result.provider_reference or idempotency_key,
        error_message=charge_result.message,
        attempt_number=invoice.attempt_count,
        is_retry=True,
    )
    session.add(attempt)
    invoice.attempt_count += 1
    await session.commit()
    await session.refresh(invoice)

    if charge_result.succeeded:
        await transition_invoice(session, invoice, InvoiceStatus.paid, actor="dunning")
        await clear_dunning(session, invoice)

        # Recovered: advance the billing period and re-provision access.
        if subscription.current_period_end is not None:
            subscription.current_period_start = subscription.current_period_end
            subscription.current_period_end = advance_billing_period(
                subscription.current_period_end, plan.interval, plan.interval_count
            )
        await transition_subscription(
            session,
            subscription,
            SubscriptionStatus.active,
            reason="dunning_recovered",
            actor="dunning",
        )
        await enqueue_webhook_event(
            session,
            tenant_id=invoice.tenant_id,
            event_type="subscription.activated",
            payload={"subscription_id": str(subscription.id), "reason": "dunning_recovered"},
        )
        await enqueue_webhook_event(
            session,
            tenant_id=invoice.tenant_id,
            event_type="invoice.paid",
            payload={"invoice_id": str(invoice.id)},
        )
        logger.info("Dunning retry recovered subscription %s", subscription.id)
        return

    # Still failing — schedule the next retry or exhaust to unpaid.
    await open_or_advance_dunning(
        session,
        invoice=invoice,
        subscription=subscription,
        failure_reason=charge_result.failure_reason,
        actor="dunning",
    )


async def process_unpaid_grace(ctx: dict[str, Any]) -> None:
    """Cancel subscriptions left in ``unpaid`` beyond the grace period."""
    logger.info("Starting unpaid grace-period worker")
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.DUNNING_GRACE_DAYS)

    async with AsyncSessionLocal() as session:
        stmt = select(Subscription).where(
            Subscription.status == SubscriptionStatus.unpaid,
            Subscription.updated_at <= cutoff,
        )
        subscriptions = (await session.execute(stmt)).scalars().all()

        for subscription in subscriptions:
            token = current_tenant_id.set(subscription.tenant_id)
            proj_token = current_project_id.set(subscription.project_id)
            try:
                from app.subscriptions.models import SubscriptionType
                target_status = (
                    SubscriptionStatus.defaulted
                    if subscription.type == SubscriptionType.installment
                    else SubscriptionStatus.canceled
                )
                await transition_subscription(
                    session,
                    subscription,
                    target_status,
                    reason="grace_period_expired",
                    actor="dunning",
                )
                await enqueue_webhook_event(
                    session,
                    tenant_id=subscription.tenant_id,
                    event_type="subscription.canceled" if target_status == SubscriptionStatus.canceled else "subscription.defaulted",
                    payload={"subscription_id": str(subscription.id), "reason": "grace_period_expired"},
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Grace cancel failed for subscription %s: %s", subscription.id, exc)
                await session.rollback()
            finally:
                current_tenant_id.reset(token)
                current_project_id.reset(proj_token)
