import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core.context import current_tenant_id
from app.db.database import AsyncSessionLocal
from app.subscriptions.models import Subscription, SubscriptionStatus, SubscriptionType
from app.plans.service import PlanService
from app.customers.service import CustomerService
from app.payment_methods.service import PaymentMethodService
from app.invoices.models import Invoice, InvoiceStatus
from app.invoices.service import InvoiceService
from app.invoices.state_machine import transition_invoice
from app.providers.base import FailureReason
from app.providers.deps import get_payment_provider
from app.worker.utils import advance_billing_period
from app.webhooks.models import PaymentAttempt
from app.dunning.service import open_or_advance_dunning

logger = logging.getLogger(__name__)

async def process_billing_cycle(ctx: dict[str, Any]) -> None:
    """
    Arq cron job that runs hourly to process due subscriptions.
    Queries all active recurring subscriptions where the current period has ended.
    """
    logger.info("Starting billing cycle worker")
    provider = get_payment_provider()
    now = datetime.now(timezone.utc)
    
    async with AsyncSessionLocal() as session:
        # Bypassing BaseRepository to query across all tenants
        stmt = (
            select(Subscription)
            .where(
                Subscription.current_period_end <= now,
                Subscription.status == SubscriptionStatus.active,
                Subscription.type == SubscriptionType.recurring,
            )
        )
        result = await session.execute(stmt)
        subscriptions = result.scalars().all()
        
        for sub in subscriptions:
            # Enforce tenant isolation for the repository services
            token = current_tenant_id.set(sub.tenant_id)
            try:
                plan_svc = PlanService(session)
                plan = await plan_svc.get(sub.plan_id)
                if not plan:
                    logger.error(f"Plan {sub.plan_id} not found for subscription {sub.id}")
                    continue
                
                customer_svc = CustomerService(session)
                customer = await customer_svc.get(sub.customer_id)
                if not customer:
                    logger.error(f"Customer {sub.customer_id} not found for subscription {sub.id}")
                    continue
                
                payment_method = None
                if sub.payment_method_id:
                    pm_svc = PaymentMethodService(session)
                    payment_method = await pm_svc.get(sub.payment_method_id)

                invoice_svc = InvoiceService(session)
                
                # Check for existing open invoice for this period idempotency
                existing_invoice_stmt = (
                    select(Invoice)
                    .where(
                        Invoice.subscription_id == sub.id,
                        Invoice.status == InvoiceStatus.open,
                        Invoice.period_start == sub.current_period_start,
                    )
                )
                existing_invoice_result = await session.execute(existing_invoice_stmt)
                invoice = existing_invoice_result.scalar_one_or_none()
                
                if not invoice:
                    # Create new invoice
                    invoice = await invoice_svc.create(
                        customer_id=customer.id,
                        subscription_id=sub.id,
                        status=InvoiceStatus.open,
                        amount_due=plan.amount,
                        currency=plan.currency,
                        period_start=sub.current_period_start,
                        period_end=sub.current_period_end,
                    )
                
                if not payment_method or not payment_method.provider_token:
                    logger.warning(f"No valid payment method for subscription {sub.id}")
                    # No card to charge — treat as a payment-method failure so
                    # the subscription is flagged (past_due) for an update.
                    await open_or_advance_dunning(
                        session,
                        invoice=invoice,
                        subscription=sub,
                        failure_reason=FailureReason.invalid_payment_method,
                        actor="system",
                    )
                    continue
                
                idempotency_key = f"{invoice.id}-{invoice.attempt_count}"
                
                charge_result = await provider.charge_tokenized_card(
                    token=payment_method.provider_token,
                    amount_minor=plan.amount,
                    currency=plan.currency,
                    customer_email=customer.email,
                    idempotency_key=idempotency_key,
                    order_reference=str(invoice.id),
                )
                
                attempt = PaymentAttempt(
                    tenant_id=invoice.tenant_id,
                    invoice_id=invoice.id,
                    status=charge_result.status,
                    failure_reason=charge_result.failure_reason,
                    provider_reference=charge_result.provider_reference or idempotency_key,
                    error_message=charge_result.message
                )
                session.add(attempt)
                
                invoice.attempt_count += 1
                await session.commit()
                await session.refresh(invoice)
                
                if charge_result.succeeded:
                    await transition_invoice(session, invoice, InvoiceStatus.paid, actor="system")
                    
                    new_end = advance_billing_period(sub.current_period_end, plan.interval, plan.interval_count)
                    sub.current_period_start = sub.current_period_end
                    sub.current_period_end = new_end
                    await session.commit()
                    logger.info(f"Successfully billed subscription {sub.id}")
                else:
                    logger.warning(f"Charge failed for subscription {sub.id}: {charge_result.failure_reason}")
                    # Hand off to the dunning flow: it opens the cycle, schedules
                    # retries per the FailureReason policy, and advances toward
                    # unpaid once retries are exhausted. The billing period is
                    # deliberately NOT advanced on failure.
                    await open_or_advance_dunning(
                        session,
                        invoice=invoice,
                        subscription=sub,
                        failure_reason=charge_result.failure_reason,
                        actor="system",
                    )
            except Exception as e:
                logger.error(f"Error processing subscription {sub.id}: {e}")
                await session.rollback()
            finally:
                current_tenant_id.reset(token)
