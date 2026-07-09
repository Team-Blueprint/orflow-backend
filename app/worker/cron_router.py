"""Internal cron trigger endpoints.

These endpoints are called by an external cron service (e.g. cron-job.org)
on a fixed schedule to drive background jobs that would otherwise require a
separate arq worker process.

All endpoints:
  - Require the X-Cron-Secret header to match settings.CRON_SECRET
  - Return 200 immediately (required by cron-job.org)
  - Run the actual job in a FastAPI BackgroundTask after the response
"""

import logging
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/cron", tags=["cron"])


def _auth(x_cron_secret: str | None) -> None:
    """Raise 401 if the secret header is missing or wrong."""
    if not settings.CRON_SECRET or x_cron_secret != settings.CRON_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/billing-cycle")
async def trigger_billing_cycle(
    background_tasks: BackgroundTasks,
    x_cron_secret: str | None = Header(default=None),
):
    _auth(x_cron_secret)
    from app.worker.billing_cycle import process_billing_cycle
    background_tasks.add_task(process_billing_cycle, {})
    logger.info("Cron: billing-cycle triggered")
    return {"status": "ok", "job": "billing-cycle"}


@router.post("/installments")
async def trigger_installments(
    background_tasks: BackgroundTasks,
    x_cron_secret: str | None = Header(default=None),
):
    _auth(x_cron_secret)
    from app.worker.installments import process_due_installment_invoices
    background_tasks.add_task(process_due_installment_invoices, {})
    logger.info("Cron: installments triggered")
    return {"status": "ok", "job": "installments"}


@router.post("/dunning-retries")
async def trigger_dunning_retries(
    background_tasks: BackgroundTasks,
    x_cron_secret: str | None = Header(default=None),
):
    _auth(x_cron_secret)
    from app.dunning.worker import process_dunning_retries
    background_tasks.add_task(process_dunning_retries, {})
    logger.info("Cron: dunning-retries triggered")
    return {"status": "ok", "job": "dunning-retries"}


@router.post("/unpaid-grace")
async def trigger_unpaid_grace(
    background_tasks: BackgroundTasks,
    x_cron_secret: str | None = Header(default=None),
):
    _auth(x_cron_secret)
    from app.dunning.worker import process_unpaid_grace
    background_tasks.add_task(process_unpaid_grace, {})
    logger.info("Cron: unpaid-grace triggered")
    return {"status": "ok", "job": "unpaid-grace"}


@router.post("/reconciliation")
async def trigger_reconciliation(
    background_tasks: BackgroundTasks,
    x_cron_secret: str | None = Header(default=None),
):
    _auth(x_cron_secret)
    from app.worker.reconciliation import process_reconciliation
    background_tasks.add_task(process_reconciliation, {})
    logger.info("Cron: reconciliation triggered")
    return {"status": "ok", "job": "reconciliation"}
