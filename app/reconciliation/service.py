import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.reconciliation.models import (
    DiscrepancyType,
    ReconciliationDiscrepancy,
)
from app.reconciliation.schemas import ReconciliationRunSummary
from app.providers.base import PaymentProviderAdapter, PaymentStatus, ProviderError
from app.webhooks.models import PaymentAttempt

logger = logging.getLogger(__name__)


class ReconciliationService:

    async def run_reconciliation(
        self,
        session: AsyncSession,
        provider: PaymentProviderAdapter,
        date_from: datetime,
        date_to: datetime,
        tenant_ids: list[uuid.UUID] | None = None,
        max_transactions: int = 1000,
    ) -> ReconciliationRunSummary:
        run_id = self._run_id_for_range(date_from, date_to)

        # 1. Delete previous discrepancies for this date range (idempotency)
        await session.execute(
            delete(ReconciliationDiscrepancy).where(
                ReconciliationDiscrepancy.run_id == run_id,
            )
        )

        date_from_str = date_from.strftime("%Y-%m-%dT00:00:00Z")
        date_to_str = date_to.strftime("%Y-%m-%dT23:59:59Z")

        # 2. Fetch Nomba transactions
        nomba_txns = await self._fetch_all_nomba_transactions(
            provider, date_from_str, date_to_str, max_transactions,
        )
        total_nomba = len(nomba_txns)

        # 3. Fetch local PaymentAttempt records in the same window
        ours_stmt = select(PaymentAttempt).where(
            PaymentAttempt.created_at >= date_from,
            PaymentAttempt.created_at < date_to,
        )
        if tenant_ids:
            ours_stmt = ours_stmt.where(PaymentAttempt.tenant_id.in_(tenant_ids))
        our_result = await session.execute(ours_stmt)
        our_attempts: list[PaymentAttempt] = list(
            r[0] if isinstance(r, tuple) else r
            for r in our_result.unique().scalars().all()
        )
        total_ours = len(our_attempts)

        # 4. Build lookup by provider_reference
        ours_by_ref: dict[str, PaymentAttempt] = {}
        for attempt in our_attempts:
            if attempt.provider_reference:
                ours_by_ref[attempt.provider_reference] = attempt

        discrepancies: list[ReconciliationDiscrepancy] = []
        matched_refs: set[str] = set()

        # 5. Match Nomba → ours
        for txn in nomba_txns:
            ref = (
                txn.get("merchantTxRef")
                or txn.get("orderReference")
                or txn.get("paymentVendorReference")
                or txn.get("id")
            )
            if not ref:
                continue

            attempt = ours_by_ref.get(ref)
            if attempt is None:
                discrepancies.append(self._build_discrepancy(
                    run_id=run_id, txn=txn,
                    discrepancy_type=DiscrepancyType.missing_in_ours,
                    details=f"Nomba has transaction {txn.get('id')} but no matching PaymentAttempt found.",
                ))
                continue

            matched_refs.add(ref)
            issues = self._compare(txn, attempt)

            if issues:
                dtype = (
                    DiscrepancyType.amount_mismatch
                    if any("Amount" in i for i in issues)
                    else DiscrepancyType.status_mismatch
                )
                discrepancies.append(self._build_discrepancy(
                    run_id=run_id, txn=txn, attempt=attempt,
                    discrepancy_type=dtype,
                    details="; ".join(issues),
                ))

        # 6. Reverse check: ours → Nomba
        for attempt in our_attempts:
            if attempt.provider_reference and attempt.provider_reference not in matched_refs:
                discrepancies.append(self._build_discrepancy(
                    run_id=run_id, txn=None, attempt=attempt,
                    discrepancy_type=DiscrepancyType.missing_in_nomba,
                    details=f"PaymentAttempt {attempt.id} has no matching Nomba transaction.",
                ))

        # 7. Write discrepancies
        if discrepancies:
            session.add_all(discrepancies)

        return ReconciliationRunSummary(
            run_id=run_id,
            date_from=date_from,
            date_to=date_to,
            total_nomba=total_nomba,
            total_ours=total_ours,
            matched=len(matched_refs),
            discrepancies=len(discrepancies),
        )

    @staticmethod
    def _run_id_for_range(date_from: datetime, date_to: datetime) -> uuid.UUID:
        return uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"reconciliation://{date_from.isoformat()}/{date_to.isoformat()}",
        )

    async def _fetch_all_nomba_transactions(
        self,
        provider: PaymentProviderAdapter,
        date_from: str,
        date_to: str,
        max_transactions: int,
    ) -> list[dict]:
        all_txns: list[dict] = []
        cursor: str | None = None

        try:
            while len(all_txns) < max_transactions:
                page = await provider.fetch_transactions(
                    date_from=date_from, date_to=date_to, cursor=cursor,
                )
                if not page:
                    break
                all_txns.extend(page)

                if len(page) < 100:
                    break
                cursor = page[-1].get("id") if page else None
        except ProviderError:
            logger.warning("Nomba API unreachable during reconciliation; skipping run.")
            return []

        return all_txns[:max_transactions]

    def _compare(self, txn: dict, attempt: PaymentAttempt) -> list[str]:
        issues: list[str] = []
        nomba_status = str(txn.get("status", "")).upper()
        our_status_str = attempt.status.value if attempt.status else ""

        if not self._statuses_match(nomba_status, our_status_str):
            issues.append(
                f"Status mismatch: Nomba={nomba_status}, Ours={our_status_str}"
            )

        nomba_amount = self._nomba_amount_to_minor(txn.get("amount"))
        our_amount = self._get_attempt_amount(attempt)
        if nomba_amount is not None and our_amount is not None and nomba_amount != our_amount:
            issues.append(
                f"Amount mismatch: Nomba={nomba_amount}, Ours={our_amount}"
            )

        return issues

    def _build_discrepancy(
        self,
        run_id: uuid.UUID,
        txn: dict | None,
        discrepancy_type: DiscrepancyType,
        details: str,
        attempt: PaymentAttempt | None = None,
    ) -> ReconciliationDiscrepancy:
        return ReconciliationDiscrepancy(
            run_id=run_id,
            tenant_id=attempt.tenant_id if attempt else None,
            nomba_transaction_id=txn.get("id") if txn else None,
            nomba_status=str(txn.get("status", "")).upper() if txn else None,
            nomba_amount=self._nomba_amount_to_minor(txn.get("amount")) if txn else None,
            nomba_created_at=self._parse_nomba_time(txn.get("timeCreated")) if txn else None,
            merchant_tx_ref=(
                txn.get("merchantTxRef")
                or txn.get("orderReference")
                or txn.get("paymentVendorReference")
            ) if txn else None,
            payment_attempt_id=attempt.id if attempt else None,
            invoice_id=attempt.invoice_id if attempt else None,
            our_status=attempt.status.value if attempt else None,
            our_amount=self._get_attempt_amount(attempt) if attempt else None,
            discrepancy_type=discrepancy_type,
            details=details,
        )

    @staticmethod
    def _statuses_match(nomba_status: str, our_status: str) -> bool:
        mapping = {
            "SUCCESS": PaymentStatus.success.value,
            "REFUND": PaymentStatus.refunded.value,
            "PAYMENT_FAILED": PaymentStatus.failed.value,
            "FAILED": PaymentStatus.failed.value,
            "PENDING_BILLING": PaymentStatus.pending.value,
            "CANCELLED": PaymentStatus.pending.value,
            "REVERSED_BY_VENDOR": PaymentStatus.pending.value,
        }
        expected = mapping.get(nomba_status)
        if expected is None:
            return True
        return expected == our_status

    @staticmethod
    def _nomba_amount_to_minor(amount: object) -> int | None:
        if amount is None:
            return None
        try:
            return int(Decimal(str(amount)) * 100)
        except (ValueError, ArithmeticError):
            return None

    @staticmethod
    def _get_attempt_amount(attempt: PaymentAttempt) -> int | None:
        if attempt.invoice and attempt.invoice.amount_due is not None:
            return attempt.invoice.amount_due
        return None

    @staticmethod
    def _parse_nomba_time(ts: str | None) -> datetime | None:
        if not ts:
            return None
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return None
