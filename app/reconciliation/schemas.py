import uuid
from datetime import datetime
from pydantic import BaseModel

from app.reconciliation.models import DiscrepancyType


class ReconciliationDiscrepancyRead(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    run_id: uuid.UUID

    nomba_transaction_id: str | None
    nomba_status: str | None
    nomba_amount: int | None
    nomba_created_at: datetime | None
    merchant_tx_ref: str | None

    payment_attempt_id: uuid.UUID | None
    invoice_id: uuid.UUID | None
    our_status: str | None
    our_amount: int | None

    discrepancy_type: DiscrepancyType
    details: str | None

    resolved: bool
    resolved_at: datetime | None
    resolution_note: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReconciliationDiscrepancyPage(BaseModel):
    items: list[ReconciliationDiscrepancyRead]
    total: int
    page: int
    per_page: int


class ResolveDiscrepancyRequest(BaseModel):
    resolution_note: str | None = None


class ReconciliationRunSummary(BaseModel):
    run_id: uuid.UUID
    date_from: datetime
    date_to: datetime
    total_nomba: int = 0
    total_ours: int = 0
    matched: int = 0
    discrepancies: int = 0
