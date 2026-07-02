import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_async_db
from app.reconciliation.models import DiscrepancyType, ReconciliationDiscrepancy
from app.reconciliation.schemas import (
    ReconciliationDiscrepancyPage,
    ReconciliationDiscrepancyRead,
    ResolveDiscrepancyRequest,
)
from app.core.context import current_key_type, current_tenant_id
from app.core.exceptions import EntityNotFoundError

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


@router.get(
    "/discrepancies",
    response_model=ReconciliationDiscrepancyPage,
    summary="List reconciliation discrepancies",
    description=(
        "Returns paginated reconciliation discrepancies. "
        "Tenants see only their own discrepancies; platform API keys see all."
    ),
)
async def list_discrepancies(
    run_id: uuid.UUID | None = Query(None),
    tenant_id: uuid.UUID | None = Query(None),
    discrepancy_type: DiscrepancyType | None = Query(None),
    resolved: bool | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_async_db),
):
    tid = current_tenant_id.get(None)

    query = select(ReconciliationDiscrepancy)
    count_query = select(func.count(ReconciliationDiscrepancy.id))

    if tid is not None:
        query = query.where(ReconciliationDiscrepancy.tenant_id == tid)
        count_query = count_query.where(ReconciliationDiscrepancy.tenant_id == tid)
    elif tenant_id is not None:
        query = query.where(ReconciliationDiscrepancy.tenant_id == tenant_id)
        count_query = count_query.where(ReconciliationDiscrepancy.tenant_id == tenant_id)

    if run_id is not None:
        query = query.where(ReconciliationDiscrepancy.run_id == run_id)
        count_query = count_query.where(ReconciliationDiscrepancy.run_id == run_id)
    if discrepancy_type is not None:
        query = query.where(ReconciliationDiscrepancy.discrepancy_type == discrepancy_type)
        count_query = count_query.where(ReconciliationDiscrepancy.discrepancy_type == discrepancy_type)
    if resolved is not None:
        query = query.where(ReconciliationDiscrepancy.resolved == resolved)
        count_query = count_query.where(ReconciliationDiscrepancy.resolved == resolved)
    if date_from is not None:
        query = query.where(ReconciliationDiscrepancy.created_at >= date_from)
        count_query = count_query.where(ReconciliationDiscrepancy.created_at >= date_from)
    if date_to is not None:
        query = query.where(ReconciliationDiscrepancy.created_at <= date_to)
        count_query = count_query.where(ReconciliationDiscrepancy.created_at <= date_to)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = (
        query.order_by(ReconciliationDiscrepancy.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    result = await db.execute(query)
    items = list(result.scalars().all())

    return ReconciliationDiscrepancyPage(
        items=[ReconciliationDiscrepancyRead.model_validate(d) for d in items],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.patch(
    "/discrepancies/{discrepancy_id}/resolve",
    response_model=ReconciliationDiscrepancyRead,
    summary="Resolve a discrepancy",
    description="Mark a discrepancy as resolved. Requires a platform-level (sk) API key.",
)
async def resolve_discrepancy(
    discrepancy_id: uuid.UUID,
    body: ResolveDiscrepancyRequest,
    db: AsyncSession = Depends(get_async_db),
):
    key_type = current_key_type.get(None)
    if key_type is None or not key_type.startswith("sk_"):
        raise HTTPException(status_code=403, detail="Only platform (sk) API keys can resolve discrepancies.")

    stmt = select(ReconciliationDiscrepancy).where(
        ReconciliationDiscrepancy.id == discrepancy_id,
    )
    result = await db.execute(stmt)
    disc = result.scalar_one_or_none()

    if disc is None:
        raise EntityNotFoundError("ReconciliationDiscrepancy", str(discrepancy_id))

    disc.resolved = True
    disc.resolved_at = datetime.now(timezone.utc)
    disc.resolution_note = body.resolution_note
    await db.commit()
    await db.refresh(disc)

    return ReconciliationDiscrepancyRead.model_validate(disc)
