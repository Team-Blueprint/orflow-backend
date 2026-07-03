import logging
import re
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.webhooks.models import WebhookEvent, PaymentAttempt
from app.webhooks.schemas import NombaWebhookPayload
from app.invoices.models import Invoice, InvoiceStatus
from app.subscriptions.models import Subscription, SubscriptionStatus
from app.invoices.state_machine import transition_invoice
from app.subscriptions.state_machine import transition_subscription
from app.providers.base import PaymentStatus, FailureReason
from app.core.context import current_tenant_id
from app.dunning.service import clear_dunning, open_or_advance_dunning
from app.webhooks.outbound import enqueue_webhook_event
from app.payment_methods.models import PaymentMethod, PaymentMethodType

logger = logging.getLogger(__name__)

async def process_nomba_webhook(session: AsyncSession, event_id: str, payload: NombaWebhookPayload) -> None:
    # Check idempotency
    stmt = select(WebhookEvent).where(WebhookEvent.event_id == event_id, WebhookEvent.provider == "nomba")
    result = await session.execute(stmt)
    if result.scalar_one_or_none():
        logger.info(f"Webhook {event_id} already processed")
        return

    order_data = payload.data.order or {}
    order_reference = order_data.get("orderReference")
    if not order_reference:
        logger.error("Webhook missing orderReference")
        return

    try:
        invoice_id = UUID(order_reference)
    except ValueError:
        logger.error(f"Invalid orderReference UUID: {order_reference}")
        return

    # Find the invoice
    stmt = select(Invoice).where(Invoice.id == invoice_id)
    result = await session.execute(stmt)
    invoice = result.scalar_one_or_none()
    
    if not invoice:
        logger.error(f"Invoice {invoice_id} not found for webhook {event_id}")
        return

    # Find subscription
    subscription = None
    if invoice.subscription_id:
        stmt = select(Subscription).where(Subscription.id == invoice.subscription_id)
        result = await session.execute(stmt)
        subscription = result.scalar_one_or_none()

    # Determine status from event_type
    event_type = (payload.event_type or "").lower()
    
    if "success" in event_type:
        payment_status = PaymentStatus.success
        failure_reason = None
    elif "fail" in event_type:
        payment_status = PaymentStatus.failed
        failure_reason = FailureReason.card_declined
    else:
        logger.info(f"Unhandled event type: {payload.event_type}")
        event = WebhookEvent(provider="nomba", event_id=event_id, event_type=payload.event_type)
        session.add(event)
        await session.commit()
        return

    # Create PaymentAttempt
    transaction_data = payload.data.transaction or {}
    error_message = transaction_data.get("responseCodeMessage") or order_data.get("message")

    attempt = PaymentAttempt(
        tenant_id=invoice.tenant_id,
        invoice_id=invoice.id,
        status=payment_status,
        failure_reason=failure_reason,
        provider_reference=event_id,
        error_message=error_message,
    )
    session.add(attempt)
    
    # Process outcome with tenant context
    token = current_tenant_id.set(invoice.tenant_id)
    try:
        if payment_status == PaymentStatus.success:
            if invoice.status != InvoiceStatus.paid:
                await transition_invoice(session, invoice, InvoiceStatus.paid, actor="nomba_webhook")

            await clear_dunning(session, invoice)
            if subscription and subscription.status in (SubscriptionStatus.incomplete, SubscriptionStatus.past_due):
                await transition_subscription(session, subscription, SubscriptionStatus.active, reason="payment_succeeded", actor="nomba_webhook")
                await enqueue_webhook_event(
                    session,
                    tenant_id=invoice.tenant_id,
                    event_type="subscription.activated",
                    payload={"subscription_id": str(subscription.id), "reason": "payment_succeeded"},
                )
            await enqueue_webhook_event(
                session,
                tenant_id=invoice.tenant_id,
                event_type="invoice.paid",
                payload={"invoice_id": str(invoice.id)},
            )

            # Save tokenized card as a payment method
            tokenized_data = payload.data.tokenizedCardData
            if tokenized_data and tokenized_data.tokenKey and tokenized_data.tokenKey.strip() not in ("", "N/A"):
                dup_stmt = select(PaymentMethod).where(
                    PaymentMethod.tenant_id == invoice.tenant_id,
                    PaymentMethod.customer_id == invoice.customer_id,
                    PaymentMethod.provider_token == tokenized_data.tokenKey,
                )
                existing = (await session.execute(dup_stmt)).scalar_one_or_none()

                if not existing:
                    last_four = order_data.get("cardLast4Digits")
                    if not last_four and tokenized_data.cardPan:
                        digits = re.sub(r"[^\d]", "", tokenized_data.cardPan)
                        if len(digits) >= 4:
                            last_four = digits[-4:]

                    expiry_month = None
                    expiry_year = None
                    if tokenized_data.tokenExpiryMonth and tokenized_data.tokenExpiryMonth not in ("N/A", ""):
                        try:
                            expiry_month = int(tokenized_data.tokenExpiryMonth)
                        except ValueError:
                            pass
                    if tokenized_data.tokenExpiryYear and tokenized_data.tokenExpiryYear not in ("N/A", ""):
                        try:
                            expiry_year = int(tokenized_data.tokenExpiryYear)
                        except ValueError:
                            pass

                    existing_stmt = select(PaymentMethod).where(
                        PaymentMethod.tenant_id == invoice.tenant_id,
                        PaymentMethod.customer_id == invoice.customer_id,
                    )
                    has_existing = (await session.execute(existing_stmt)).scalar_one_or_none() is not None

                    pm = PaymentMethod(
                        tenant_id=invoice.tenant_id,
                        customer_id=invoice.customer_id,
                        type=PaymentMethodType.card,
                        provider_token=tokenized_data.tokenKey,
                        last_four=last_four,
                        expiry_month=expiry_month,
                        expiry_year=expiry_year,
                        is_default=not has_existing,
                    )
                    session.add(pm)
                    await session.flush()

                    # Link to subscription if it doesn't have a payment method yet
                    if subscription and subscription.payment_method_id is None:
                        subscription.payment_method_id = pm.id

                    logger.info(
                        "Saved tokenized card as payment method for customer %s "
                        "(token=%s, last_four=%s)",
                        invoice.customer_id, tokenized_data.tokenKey, last_four,
                    )

        elif payment_status == PaymentStatus.failed:
            await open_or_advance_dunning(
                session,
                invoice=invoice,
                subscription=subscription,
                failure_reason=failure_reason,
                actor="nomba_webhook",
            )
    finally:
        current_tenant_id.reset(token)

    # Save idempotency record
    event = WebhookEvent(
        provider="nomba",
        event_id=event_id,
        event_type=payload.event_type,
    )
    session.add(event)
    await session.commit()
