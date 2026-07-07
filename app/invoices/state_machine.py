"""Invoice state machine.

``transition_invoice`` is the only sanctioned way to change ``Invoice.status``:
it validates the move, applies it, and writes an ``AuditLog`` row in a single
atomic commit.

Allowed transitions (trigger in parentheses):

    draft -> open          (invoice finalized / issued to the customer)
    open  -> paid          (a charge for this invoice succeeded)
    open  -> void          (canceled before collection, e.g. first-invoice expiry)
    open  -> uncollectible (written off after collection attempts are exhausted)

Terminal states (no outgoing transitions): paid, void, uncollectible.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditEntityType
from app.audit.service import record_transition
from app.core.exceptions import InvalidStateTransition
from app.invoices.models import Invoice, InvoiceStatus

I = InvoiceStatus

INVOICE_TRANSITIONS: dict[InvoiceStatus, set[InvoiceStatus]] = {
    I.draft: {I.open},
    I.open: {I.paid, I.void, I.uncollectible},
    # Terminal states.
    I.paid: set(),
    I.void: set(),
    I.uncollectible: set(),
}


def is_allowed(old: InvoiceStatus, new: InvoiceStatus) -> bool:
    return new in INVOICE_TRANSITIONS.get(old, set())


async def transition_invoice(
    session: AsyncSession,
    invoice: Invoice,
    new_status: InvoiceStatus,
    *,
    reason: str | None = None,
    actor: str = "system",
) -> Invoice:
    """Validate, apply, and audit an invoice status change (atomic commit).

    Raises ``InvalidStateTransition`` if the move is not allowed — the status is
    left untouched and no audit row is written.
    """
    old_status = invoice.status
    if not is_allowed(old_status, new_status):
        raise InvalidStateTransition("invoice", old_status, new_status)

    invoice.status = new_status
    if new_status is I.paid and invoice.paid_at is None:
        invoice.paid_at = datetime.now(timezone.utc)

    record_transition(
        session,
        entity_type=AuditEntityType.invoice,
        entity_id=invoice.id,
        tenant_id=invoice.tenant_id,
        old_status=old_status.value,
        new_status=new_status.value,
        reason=reason,
        actor=actor,
    )

    await session.commit()
    await session.refresh(invoice)
    
    # Send invoice receipt email if it transitioned to paid
    if new_status is I.paid:
        from app.customers.models import Customer
        from app.worker.tasks import enqueue_email
        from app.core.email_templates import get_invoice_receipt_template
        
        customer = await session.get(Customer, invoice.customer_id)
        if customer:
            amount_formatted = f"{invoice.amount_due / 100:.2f}"
            subject = f"Receipt for Invoice {invoice.id}"
            paid_at_str = invoice.paid_at.strftime('%Y-%m-%d %H:%M:%S UTC') if invoice.paid_at else ''
            
            html_content = get_invoice_receipt_template(
                customer_name=customer.name,
                invoice_id=str(invoice.id),
                currency=invoice.currency.upper(),
                amount_formatted=amount_formatted,
                paid_at_str=paid_at_str
            )
            await enqueue_email(to=customer.email, subject=subject, html=html_content)
    
    # Check for installment completion
    if new_status is I.paid and invoice.subscription_id:
        from sqlalchemy import select
        from app.subscriptions.models import Subscription, SubscriptionStatus, SubscriptionType
        from app.subscriptions.state_machine import transition_subscription
        from app.webhooks.outbound import enqueue_webhook_event
        
        subscription = await session.get(Subscription, invoice.subscription_id)
        if subscription and subscription.type == SubscriptionType.installment and subscription.status != SubscriptionStatus.completed:
            # Check if there are any remaining open/draft/past_due invoices
            stmt = select(Invoice).where(
                Invoice.subscription_id == subscription.id,
                Invoice.status.in_([InvoiceStatus.draft, InvoiceStatus.open])
            )
            res = await session.execute(stmt)
            remaining = res.scalars().all()
            if not remaining:
                await transition_subscription(
                    session,
                    subscription,
                    SubscriptionStatus.completed,
                    reason="all_installments_paid",
                    actor=actor
                )
                await enqueue_webhook_event(
                    session,
                    tenant_id=subscription.tenant_id,
                    event_type="subscription.completed",
                    payload={"subscription_id": str(subscription.id), "reason": "all_installments_paid"}
                )
                
    return invoice
