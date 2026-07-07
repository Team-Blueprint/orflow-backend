import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import current_is_test, current_project_id, current_tenant_id
from app.providers.deps import get_payment_provider_for_mode
from app.core.exceptions import EntityNotFoundError
from app.customers.service import CustomerService
from app.db.repository import BaseRepository
from app.invoices.models import InvoiceStatus
from app.invoices.service import InvoiceService
from app.plans.models import Plan, PlanInterval
from app.projects.models import Project
from app.subscription_pages.models import SubscriptionPage
from app.subscriptions.models import SubscriptionStatus, SubscriptionType
from app.subscriptions.service import SubscriptionService
from app.worker.tasks import schedule_subscription_expiry
from dateutil.relativedelta import relativedelta


class SubscriptionPageService(BaseRepository[SubscriptionPage]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(SubscriptionPage, session)

    async def get_by_code(self, code: str) -> SubscriptionPage | None:
        result = await self.session.execute(
            self._base_query().where(SubscriptionPage.code == code)
        )
        return result.scalar_one_or_none()

    async def list_with_plan(self, offset: int = 0, limit: int = 100) -> list[tuple[SubscriptionPage, Plan, str | None]]:
        stmt = (
            select(SubscriptionPage, Plan, Project.name)
            .join(Plan, SubscriptionPage.plan_id == Plan.id)
            .outerjoin(Project, SubscriptionPage.project_id == Project.id)
            .where(SubscriptionPage.tenant_id == self._tenant_id())
        )
        project_id = self._project_id()
        if project_id is not None:
            stmt = stmt.where(SubscriptionPage.project_id == project_id)
        stmt = stmt.offset(offset).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.all())

    async def get_with_plan(self, page_id: uuid.UUID) -> tuple[SubscriptionPage, Plan, str | None] | None:
        stmt = (
            select(SubscriptionPage, Plan, Project.name)
            .join(Plan, SubscriptionPage.plan_id == Plan.id)
            .outerjoin(Project, SubscriptionPage.project_id == Project.id)
            .where(
                SubscriptionPage.id == page_id,
                SubscriptionPage.tenant_id == self._tenant_id(),
            )
        )
        project_id = self._project_id()
        if project_id is not None:
            stmt = stmt.where(SubscriptionPage.project_id == project_id)
        result = await self.session.execute(stmt)
        row = result.one_or_none()
        return row if row else None

    async def create_page(self, plan_id: uuid.UUID) -> SubscriptionPage:
        code = SubscriptionPage.generate_code()
        while await self.get_by_code(code):
            code = SubscriptionPage.generate_code()
        return await self.create(plan_id=plan_id, code=code)


async def public_checkout_flow(
    code: str,
    name: str,
    email: str,
    session: AsyncSession,
) -> dict:
    result = await session.execute(
        select(SubscriptionPage).where(
            SubscriptionPage.code == code,
            SubscriptionPage.is_active.is_(True),
        )
    )
    page = result.scalar_one_or_none()
    if not page:
        raise EntityNotFoundError("SubscriptionPage", code)

    plan_result = await session.execute(
        select(Plan).where(Plan.id == page.plan_id)
    )
    plan = plan_result.scalar_one_or_none()
    if not plan:
        raise EntityNotFoundError("Plan", str(page.plan_id))

    tenant_token = current_tenant_id.set(page.tenant_id)
    project_token = None
    if page.project_id:
        project_token = current_project_id.set(page.project_id)
    is_test_token = current_is_test.set(page.is_test)
    try:
        provider = get_payment_provider_for_mode(page.is_test)
        customer_svc = CustomerService(session)
        customer = await customer_svc.get_by_email(email)
        if not customer:
            customer = await customer_svc.create(
                email=email,
                name=name,
                project_id=page.project_id,
            )

        sub_svc = SubscriptionService(session)
        subscription = await sub_svc.create(
            customer_id=customer.id,
            plan_id=plan.id,
            status=SubscriptionStatus.incomplete,
            type=SubscriptionType.installment if plan.installments_count else SubscriptionType.recurring,
        )

        invoice_svc = InvoiceService(session)
        now = datetime.now(timezone.utc)
        delta_kwargs = {}
        if plan.interval == PlanInterval.daily:
            delta_kwargs = {"days": plan.interval_count}
        elif plan.interval == PlanInterval.weekly:
            delta_kwargs = {"weeks": plan.interval_count}
        elif plan.interval == PlanInterval.monthly:
            delta_kwargs = {"months": plan.interval_count}
        elif plan.interval == PlanInterval.quarterly:
            delta_kwargs = {"months": 3 * plan.interval_count}
        elif plan.interval in (PlanInterval.yearly, PlanInterval.annually):
            delta_kwargs = {"years": plan.interval_count}
        elif plan.interval == PlanInterval.biannually:
            delta_kwargs = {"months": 6 * plan.interval_count}
        delta = relativedelta(**delta_kwargs)
        due_date = now + delta
        period_end = now + delta

        invoice = await invoice_svc.create(
            customer_id=customer.id,
            subscription_id=subscription.id,
            status=InvoiceStatus.open,
            amount_due=plan.amount,
            currency=plan.currency,
            due_date=due_date,
            period_start=now,
            period_end=period_end,
        )

        checkout_session = await provider.initiate_checkout(
            amount_minor=plan.amount,
            currency=plan.currency,
            customer_email=customer.email,
            order_reference=str(invoice.id),
            customer_id=str(customer.id),
            tokenize_card=True,
        )

        await schedule_subscription_expiry(subscription.id, delay_hours=23)

        return {
            "checkout_link": checkout_session.checkout_link,
            "order_reference": checkout_session.order_reference,
        }
    finally:
        current_tenant_id.reset(tenant_token)
        if project_token:
            current_project_id.reset(project_token)
        current_is_test.reset(is_test_token)
