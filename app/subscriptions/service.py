import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import BaseRepository
from app.subscriptions.models import Subscription, SubscriptionStatus
from app.subscriptions.schemas import (
    ChangePlanResponse,
    ProrationLineItemRead,
    SubscriptionCreate,
    SubscriptionCreateResponse,
    SubscriptionCreateResponse,
    SubscriptionRead,
)
from app.core.exceptions import EntityNotFoundError, InvalidStateTransition
from app.subscriptions.state_machine import transition_subscription
from app.customers.service import CustomerService
from app.plans.service import PlanService
from app.payment_methods.service import PaymentMethodService
from app.payment_methods.models import PaymentMethodType
from app.invoices.models import InvoiceLineItem, InvoiceStatus
from app.invoices.schemas import InvoiceRead
from app.invoices.service import InvoiceService
from app.invoices.state_machine import transition_invoice
from app.proration.calculator import calculate_proration
from app.dunning.service import open_or_advance_dunning
from app.webhooks.models import PaymentAttempt
from app.worker.tasks import schedule_subscription_expiry, schedule_trial_activation
from app.providers.base import FailureReason, PaymentProviderAdapter
from dateutil.relativedelta import relativedelta
from app.plans.models import PlanInterval
from app.subscriptions.models import SubscriptionType


def _as_utc(dt: datetime) -> datetime:
    """Coerce a possibly-naive datetime (SQLite round-trips drop tzinfo) to UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class SubscriptionService(BaseRepository[Subscription]):
    """
    Tenant-scoped repository for subscriptions.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Subscription, session)

    async def create_subscription_flow(
        self,
        payload: SubscriptionCreate,
        provider: PaymentProviderAdapter,
    ) -> SubscriptionCreateResponse:
        
        # 1. Fetch & validate related entities
        customer_svc = CustomerService(self.session)
        customer = await customer_svc.get(payload.customer_id)
        if not customer:
            raise EntityNotFoundError("Customer", str(payload.customer_id))

        plan_svc = PlanService(self.session)
        plan = await plan_svc.get(payload.plan_id)
        if not plan:
            raise EntityNotFoundError("Plan", str(payload.plan_id))

        payment_method = None
        if payload.payment_method_id:
            pm_svc = PaymentMethodService(self.session)
            payment_method = await pm_svc.get(payload.payment_method_id)
            if not payment_method:
                raise EntityNotFoundError("PaymentMethod", str(payload.payment_method_id))

        # 2. Determine initial status and trial end
        is_trial = payload.trial or (plan.trial_period_days and plan.trial_period_days > 0)
        
        initial_status = SubscriptionStatus.incomplete
        trial_end = None
        
        if payment_method and payment_method.type == PaymentMethodType.bank_transfer:
            initial_status = SubscriptionStatus.active
        elif is_trial:
            initial_status = SubscriptionStatus.trialing
            trial_days = plan.trial_period_days or 14
            trial_end = datetime.now(timezone.utc) + timedelta(days=trial_days)

        sub_type = SubscriptionType.installment if plan.installments_count else SubscriptionType.recurring

        # 3. Create Subscription
        subscription = await self.create(
            customer_id=payload.customer_id,
            plan_id=payload.plan_id,
            payment_method_id=payload.payment_method_id,
            status=initial_status,
            type=sub_type,
            trial_end=trial_end,
        )
        
        checkout_link = None
        order_reference = None
        
        # 4. Handle trial scheduling
        if initial_status == SubscriptionStatus.trialing:
            if trial_end:
                await schedule_trial_activation(subscription.id, trial_end)
            
        # 5. Handle immediate charge
        elif initial_status == SubscriptionStatus.incomplete:
            invoice_svc = InvoiceService(self.session)
            
            now = datetime.now(timezone.utc)
            invoices_to_create = plan.installments_count if sub_type == SubscriptionType.installment else 1
            
            first_invoice = None
            for i in range(invoices_to_create):
                # Calculate period and due date
                delta_kwargs = {}
                if plan.interval == PlanInterval.daily:
                    delta_kwargs = {"days": i * plan.interval_count}
                elif plan.interval == PlanInterval.weekly:
                    delta_kwargs = {"weeks": i * plan.interval_count}
                elif plan.interval == PlanInterval.monthly:
                    delta_kwargs = {"months": i * plan.interval_count}
                elif plan.interval == PlanInterval.quarterly:
                    delta_kwargs = {"months": 3 * i * plan.interval_count}
                elif plan.interval in (PlanInterval.yearly, PlanInterval.annually):
                    delta_kwargs = {"years": i * plan.interval_count}
                elif plan.interval == PlanInterval.biannually:
                    delta_kwargs = {"months": 6 * i * plan.interval_count}
                    
                delta = relativedelta(**delta_kwargs)
                due_date = now + delta
                
                # Period end is due_date + 1 interval
                next_delta_kwargs = {}
                if plan.interval == PlanInterval.daily:
                    next_delta_kwargs = {"days": (i + 1) * plan.interval_count}
                elif plan.interval == PlanInterval.weekly:
                    next_delta_kwargs = {"weeks": (i + 1) * plan.interval_count}
                elif plan.interval == PlanInterval.monthly:
                    next_delta_kwargs = {"months": (i + 1) * plan.interval_count}
                elif plan.interval == PlanInterval.quarterly:
                    next_delta_kwargs = {"months": 3 * (i + 1) * plan.interval_count}
                elif plan.interval in (PlanInterval.yearly, PlanInterval.annually):
                    next_delta_kwargs = {"years": (i + 1) * plan.interval_count}
                elif plan.interval == PlanInterval.biannually:
                    next_delta_kwargs = {"months": 6 * (i + 1) * plan.interval_count}
                next_delta = relativedelta(**next_delta_kwargs)
                period_end = now + next_delta
                
                invoice = await invoice_svc.create(
                    customer_id=customer.id,
                    subscription_id=subscription.id,
                    status=InvoiceStatus.open,
                    amount_due=plan.amount,
                    currency=plan.currency,
                    due_date=due_date,
                    period_start=due_date,
                    period_end=period_end
                )
                
                if i == 0:
                    first_invoice = invoice
            
            checkout_session = await provider.initiate_checkout(
                amount_minor=plan.amount,
                currency=plan.currency,
                customer_email=customer.email,
                order_reference=str(first_invoice.id),
                customer_id=str(customer.id),
                tokenize_card=True,
            )
            
            checkout_link = checkout_session.checkout_link
            order_reference = checkout_session.order_reference
            
            await schedule_subscription_expiry(subscription.id, delay_hours=23)

        return SubscriptionCreateResponse(
            subscription=SubscriptionRead.model_validate(subscription),
            checkout_link=checkout_link,
            order_reference=order_reference,
        )

    async def cancel_subscription_flow(self, subscription_id: uuid.UUID) -> Subscription:
        subscription = await self.get(subscription_id)
        if not subscription:
            raise EntityNotFoundError("Subscription", str(subscription_id))

        return await transition_subscription(
            self.session, subscription, SubscriptionStatus.canceled, reason="customer_request"
        )

    async def pause_subscription_flow(self, subscription_id: uuid.UUID) -> Subscription:
        subscription = await self.get(subscription_id)
        if not subscription:
            raise EntityNotFoundError("Subscription", str(subscription_id))

        return await transition_subscription(
            self.session, subscription, SubscriptionStatus.paused, reason="customer_request"
        )

    async def resume_subscription_flow(self, subscription_id: uuid.UUID) -> Subscription:
        subscription = await self.get(subscription_id)
        if not subscription:
            raise EntityNotFoundError("Subscription", str(subscription_id))

        return await transition_subscription(
            self.session, subscription, SubscriptionStatus.active, reason="customer_request"
        )

    async def change_plan_flow(
        self,
        subscription_id: uuid.UUID,
        new_plan_id: uuid.UUID,
        provider: PaymentProviderAdapter,
    ) -> ChangePlanResponse:
        """Switch an active subscription to a new plan with explicit proration.

        Credits the unused portion of the old plan and charges the remaining
        portion of the new plan as two line items on a fresh invoice, swaps the
        plan reference atomically, then collects any positive net immediately —
        a failed charge falls into the same dunning flow as a renewal.
        """
        subscription = await self.get(subscription_id)
        if not subscription:
            raise EntityNotFoundError("Subscription", str(subscription_id))
        if subscription.status != SubscriptionStatus.active:
            raise ValueError("Plan can only be changed on an active subscription")

        plan_svc = PlanService(self.session)
        new_plan = await plan_svc.get(new_plan_id)
        if not new_plan:
            raise EntityNotFoundError("Plan", str(new_plan_id))
        if new_plan.id == subscription.plan_id:
            raise ValueError("Subscription is already on this plan")

        old_plan = await plan_svc.get(subscription.plan_id)
        if not old_plan:
            raise EntityNotFoundError("Plan", str(subscription.plan_id))
        if old_plan.currency != new_plan.currency:
            raise ValueError("Cannot change to a plan in a different currency")

        # Pro-rate over the actual remaining days of the current billing cycle.
        now = datetime.now(timezone.utc)
        start = subscription.current_period_start
        end = subscription.current_period_end
        if start and end:
            total_days = (_as_utc(end) - _as_utc(start)).days
            days_remaining = (_as_utc(end) - now).days
        else:
            total_days = 0
            days_remaining = 0
        proration = calculate_proration(old_plan, new_plan, days_remaining, total_days)

        customer = await CustomerService(self.session).get(subscription.customer_id)

        # Create the proration invoice + explicit line items and swap the plan
        # reference in a single commit.
        invoice_svc = InvoiceService(self.session)
        invoice = await invoice_svc.create(
            customer_id=subscription.customer_id,
            subscription_id=subscription.id,
            status=InvoiceStatus.open,
            amount_due=proration.net_minor,
            currency=proration.currency,
            period_start=now,
            period_end=end,
        )
        for item in proration.line_items:
            self.session.add(
                InvoiceLineItem(
                    tenant_id=subscription.tenant_id,
                    project_id=subscription.project_id,
                    invoice_id=invoice.id,
                    description=item.description,
                    amount_minor=item.amount_minor,
                )
            )
        subscription.plan_id = new_plan.id
        await self.session.commit()
        await self.session.refresh(invoice)
        await self.session.refresh(subscription)

        charged = False
        payment_status: str | None = None

        if proration.net_minor <= 0:
            # Downgrade / nothing owed this cycle: settle the invoice. Any credit
            # is recorded in the line items (carry-forward is out of V1 scope).
            await transition_invoice(self.session, invoice, InvoiceStatus.paid, actor="plan_change")
            payment_status = "no_charge"
        else:
            payment_method = None
            if subscription.payment_method_id:
                payment_method = await PaymentMethodService(self.session).get(subscription.payment_method_id)

            if not payment_method or not payment_method.provider_token:
                await open_or_advance_dunning(
                    self.session,
                    invoice=invoice,
                    subscription=subscription,
                    failure_reason=FailureReason.invalid_payment_method,
                    actor="plan_change",
                )
                payment_status = "no_payment_method"
            else:
                idempotency_key = f"{invoice.id}-{invoice.attempt_count}"
                charge_result = await provider.charge_tokenized_card(
                    token=payment_method.provider_token,
                    amount_minor=proration.net_minor,
                    currency=proration.currency,
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
                    is_retry=False,
                )
                self.session.add(attempt)
                invoice.attempt_count += 1
                await self.session.commit()
                await self.session.refresh(invoice)

                payment_status = charge_result.status.value
                if charge_result.succeeded:
                    await transition_invoice(self.session, invoice, InvoiceStatus.paid, actor="plan_change")
                    charged = True
                else:
                    # Same dunning path as a failed renewal: past_due + retries.
                    await open_or_advance_dunning(
                        self.session,
                        invoice=invoice,
                        subscription=subscription,
                        failure_reason=charge_result.failure_reason,
                        actor="plan_change",
                    )

        await self.session.refresh(subscription)
        await self.session.refresh(invoice)
        return ChangePlanResponse(
            subscription=SubscriptionRead.model_validate(subscription),
            invoice=InvoiceRead.model_validate(invoice),
            line_items=[
                ProrationLineItemRead(description=item.description, amount_minor=item.amount_minor)
                for item in proration.line_items
            ],
            charged=charged,
            payment_status=payment_status,
        )
