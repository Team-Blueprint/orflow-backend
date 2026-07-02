import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.database import AsyncSessionLocal
from app.providers.deps import get_payment_provider
from app.reconciliation.service import ReconciliationService

logger = logging.getLogger(__name__)


async def process_reconciliation(ctx: dict[str, Any]) -> None:
    """Scheduled daily reconciliation job.

    Runs at 3:00 AM UTC. Reconciles the previous UTC day's transactions
    between Nomba and the engine's PaymentAttempt records.
    """
    logger.info("Starting daily reconciliation run")
    provider = get_payment_provider()
    now = datetime.now(timezone.utc)
    date_to = now.replace(hour=0, minute=0, second=0, microsecond=0)
    date_from = date_to - timedelta(days=1)

    async with AsyncSessionLocal() as session:
        service = ReconciliationService()
        summary = await service.run_reconciliation(
            session, provider, date_from, date_to,
            max_transactions=1000,
        )

    logger.info(
        "Reconciliation complete: run=%s nomba=%d ours=%d matched=%d discrepancies=%d",
        summary.run_id, summary.total_nomba, summary.total_ours,
        summary.matched, summary.discrepancies,
    )
