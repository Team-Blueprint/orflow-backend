import logging
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.context import current_tenant_id
from app.db.database import AsyncSessionLocal
from app.invoices.models import Invoice, InvoiceStatus
from app.subscriptions.models import Subscription, SubscriptionStatus, SubscriptionType
from app.providers.deps import get_payment_provider
from app.payment_methods.service import PaymentMethodService
from app.dunning.service import open_or_advance_dunning
from app.providers.base import FailureReason
from app.webhooks.models import PaymentAttempt

logger = logging.getLogger(__name__)

async def process_due_installment_invoices(ctx: Dict[str, Any]):
    """
    Cron job that processes all open installment invoices whose due_date has passed.
    """
    logger.info("Starting process_due_installment_invoices...")
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        # Query open invoices whose due_date <= now, joined with installment subscriptions
        query = (
            select(Invoice, Subscription)
            .join(Subscription, Invoice.subscription_id == Subscription.id)
            .where(
                Invoice.status == InvoiceStatus.open,
                Invoice.due_date <= now,
                Subscription.type == SubscriptionType.installment,
                Subscription.status == SubscriptionStatus.active,
                # Avoid picking up invoices that are already in dunning
                Invoice.next_retry_at.is_(None)
            )
        )
        result = await session.execute(query)
        rows = result.all()
        
        if not rows:
            logger.info("No due installment invoices to process.")
            return

        provider = get_payment_provider()
        pm_svc = PaymentMethodService(session)

        for invoice, subscription in rows:
            token = current_tenant_id.set(invoice.tenant_id)
            try:
                payment_method = None
                if subscription.payment_method_id:
                    payment_method = await pm_svc.get(subscription.payment_method_id)

                if not payment_method or not payment_method.provider_token:
                    logger.warning(f"Invoice {invoice.id} lacks valid payment method.")
                    await open_or_advance_dunning(
                        session,
                        invoice=invoice,
                        subscription=subscription,
                        failure_reason=FailureReason.invalid_payment_method,
                        actor="installment_worker"
                    )
                    continue

                # Just get customer via simple query
                from app.customers.models import Customer
                cust_res = await session.execute(select(Customer).where(Customer.id == subscription.customer_id))
                customer = cust_res.scalar_one_or_none()

                idempotency_key = f"{invoice.id}-{invoice.attempt_count}"
                
                try:
                    charge_result = await provider.charge_tokenized_card(
                        token=payment_method.provider_token,
                        amount_minor=invoice.amount_due,
                        currency=invoice.currency,
                        customer_email=customer.email,
                        customer_id=str(customer.id),
                        idempotency_key=idempotency_key,
                    )
                except Exception as e:
                    logger.error(f"Provider error for invoice {invoice.id}: {e}")
                    continue

                attempt = PaymentAttempt(
                    tenant_id=invoice.tenant_id,
                    invoice_id=invoice.id,
                    status=charge_result.status,
                    failure_reason=charge_result.failure_reason,
                    provider_reference=charge_result.provider_reference or idempotency_key,
                    error_message=charge_result.message,
                    attempt_number=invoice.attempt_count,
                    is_retry=False,
                )
                session.add(attempt)
                invoice.attempt_count += 1
                await session.commit()
                
                if charge_result.succeeded:
                    from app.invoices.state_machine import transition_invoice
                    await transition_invoice(session, invoice, InvoiceStatus.paid, actor="installment_worker")
                else:
                    await open_or_advance_dunning(
                        session,
                        invoice=invoice,
                        subscription=subscription,
                        failure_reason=charge_result.failure_reason,
                        actor="installment_worker"
                    )
            finally:
                current_tenant_id.reset(token)
                
    logger.info("Finished process_due_installment_invoices.")
