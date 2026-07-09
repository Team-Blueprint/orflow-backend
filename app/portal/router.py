"""Portal router — public auth + session-scoped subscription management."""
import uuid
import logging

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import current_tenant_id
from app.db.database import get_async_db
from app.customers.models import Customer
from app.subscriptions.models import Subscription, SubscriptionStatus
from app.subscriptions.state_machine import transition_subscription
from app.invoices.state_machine import transition_invoice
from app.invoices.models import Invoice, InvoiceStatus
from app.providers.deps import get_payment_provider_for_mode
from app.providers.base import PaymentStatus, ProviderError
from app.payment_methods.models import PaymentMethod, PaymentMethodType
from app.payment_methods.service import PaymentMethodService
from app.plans.models import Plan
from app.webhooks.models import PaymentAttempt

from app.portal.schemas import (
    PortalAccessRead,
    PortalVerifyRequest,
    PortalVerifyResponse,
    PortalUpdatePinRequest,
    PortalSubscriptionRead,
    PortalPaymentRead,
    PortalUpdateCardRequest,
    CreateUpdateCardCheckoutResponse,
    ConfirmUpdateCardRequest,
    ConfirmUpdateCardResponse,
    VerifyCheckoutResponse,
)
from app.portal import service as portal_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/portal", tags=["Portal"])

_bearer = HTTPBearer(auto_error=False)


# ── Session token dependency ──────────────────────────────────────────────────

async def _portal_session(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing portal session token")
    try:
        return portal_svc.decode_portal_session_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Portal session expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid portal token: {exc}")


# ── Public slug lookup ────────────────────────────────────────────────────────

@router.get(
    "/access/{token_slug}",
    response_model=PortalAccessRead,
    summary="Look up customer by portal token slug",
    description="Public endpoint — no auth required. Returns the customer's name for the given portal slug.",
)
async def get_portal_access(
    token_slug: str,
    db: AsyncSession = Depends(get_async_db),
):
    customer = await portal_svc.get_customer_by_slug(db, token_slug)
    if not customer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return PortalAccessRead(name=customer.name)


# ── Auth ──────────────────────────────────────────────────────────────────────

@router.post(
    "/verify-access",
    response_model=PortalVerifyResponse,
    summary="Verify portal token slug + PIN",
    description="Authenticates a customer by their portal_token_slug and PIN. Returns a short-lived portal_session_token (JWT).",
)
async def verify_access(
    payload: PortalVerifyRequest,
    db: AsyncSession = Depends(get_async_db),
):
    customer = await portal_svc.get_customer_by_slug(db, payload.token_slug)
    if not customer or not customer.portal_pin_hash:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not portal_svc.verify_pin(payload.pin, customer.portal_pin_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # Find the customer's most recent active subscription
    sub_result = await db.execute(
        select(Subscription)
        .where(Subscription.customer_id == customer.id)
        .order_by(Subscription.created_at.desc())
        .limit(1)
    )
    subscription = sub_result.scalar_one_or_none()
    if not subscription:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No subscription found")

    token = portal_svc.create_portal_session_token(
        customer_id=str(customer.id),
        subscription_id=str(subscription.id),
    )
    return PortalVerifyResponse(portal_session_token=token)


@router.post(
    "/update-pin",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change portal PIN",
    description="Validates the current PIN then replaces it with the new one.",
)
async def update_pin(
    payload: PortalUpdatePinRequest,
    session_claims: dict = Depends(_portal_session),
    db: AsyncSession = Depends(get_async_db),
):
    customer_id = uuid.UUID(session_claims["sub"])
    result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = result.scalar_one_or_none()
    if not customer or not customer.portal_pin_hash:
        raise HTTPException(status_code=404, detail="Customer not found")

    if not portal_svc.verify_pin(payload.current_pin, customer.portal_pin_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current PIN is incorrect")

    customer.portal_pin_hash = portal_svc.hash_pin(payload.new_pin)
    await db.commit()


# ── Verify checkout (callback from Nomba) ─────────────────────────────────────

@router.get(
    "/verify-checkout",
    response_model=VerifyCheckoutResponse,
    summary="Verify checkout payment after callback redirect",
    description=(
        "Called by the portal frontend after Nomba redirects the customer back to "
        "the callback URL. Verifies the transaction with Nomba and, if successful, "
        "updates the invoice and subscription status. This is a safety net when the "
        "webhook hasn't arrived yet."
    ),
)
async def verify_checkout(
    orderReference: str,
    db: AsyncSession = Depends(get_async_db),
):
    # Find the invoice by order reference
    try:
        order_uuid = uuid.UUID(orderReference)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid orderReference format")

    inv_result = await db.execute(
        select(Invoice).where(Invoice.id == order_uuid)
    )
    invoice = inv_result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Fetch customer for portal fields
    cust_result = await db.execute(select(Customer).where(Customer.id == invoice.customer_id))
    customer = cust_result.scalar_one_or_none()
    portal_token_slug = customer.portal_token_slug if customer else None
    customer_name = customer.name if customer else None

    # Already paid — nothing to do
    if invoice.status == InvoiceStatus.paid:
        return VerifyCheckoutResponse(
            success=True,
            status="paid",
            subscription_id=invoice.subscription_id,
            portal_token_slug=portal_token_slug,
            customer_name=customer_name,
        )

    # Get provider for the right environment (sandbox vs live)
    provider = get_payment_provider_for_mode(invoice.is_test)

    # Verify with Nomba
    result = await provider.verify_checkout_transaction(
        order_reference=str(invoice.id),
    )

    if result.status != PaymentStatus.success:
        logger.info("Checkout verify: payment not successful (%s)", result.status.value)
        return VerifyCheckoutResponse(
            success=False,
            status="failed",
            subscription_id=invoice.subscription_id,
            portal_token_slug=portal_token_slug,
            customer_name=customer_name,
        )

    # Payment confirmed — update invoice and subscription
    tenant_token = current_tenant_id.set(invoice.tenant_id)
    try:
        await transition_invoice(db, invoice, InvoiceStatus.paid, actor="verify_checkout")

        # Find subscription
        sub_result = await db.execute(
            select(Subscription).where(Subscription.id == invoice.subscription_id)
        )
        subscription = sub_result.scalar_one_or_none()
        if subscription and subscription.status in (
            SubscriptionStatus.incomplete,
            SubscriptionStatus.past_due,
        ):
            await transition_subscription(
                db, subscription, SubscriptionStatus.active,
                reason="payment_verified", actor="verify_checkout",
            )

        await db.commit()

        # Refresh customer in case portal_token_slug was just generated by transition_invoice
        await db.refresh(customer)

        logger.info(
            "Checkout verify: invoice %s marked paid, subscription %s activated",
            invoice.id, invoice.subscription_id,
        )

        return VerifyCheckoutResponse(
            success=True,
            status="paid",
            subscription_id=invoice.subscription_id,
            portal_token_slug=customer.portal_token_slug if customer else None,
            customer_name=customer.name if customer else None,
        )
    finally:
        current_tenant_id.reset(tenant_token)


# ── Subscription view ─────────────────────────────────────────────────────────

@router.get(
    "/subscriptions/me",
    response_model=PortalSubscriptionRead,
    summary="Get my subscription",
    description="Returns the subscription linked to the current portal session.",
)
async def get_my_subscription(
    session_claims: dict = Depends(_portal_session),
    db: AsyncSession = Depends(get_async_db),
):
    subscription_id = uuid.UUID(session_claims["subscription_id"])
    customer_id = uuid.UUID(session_claims["sub"])

    sub_result = await db.execute(select(Subscription).where(Subscription.id == subscription_id))
    subscription = sub_result.scalar_one_or_none()
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    plan_result = await db.execute(select(Plan).where(Plan.id == subscription.plan_id))
    plan = plan_result.scalar_one_or_none()

    cust_result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = cust_result.scalar_one_or_none()

    return PortalSubscriptionRead(
        subscription_id=subscription.id,
        plan_name=plan.name if plan else "Unknown",
        status=subscription.status.value,
        amount=plan.amount if plan else 0,
        currency=plan.currency if plan else "NGN",
        next_charge_date=subscription.current_period_end,
        card_last4=customer.card_last4 if customer else None,
        card_brand=customer.card_brand if customer else None,
    )


@router.get(
    "/subscriptions/me/payments",
    response_model=list[PortalPaymentRead],
    summary="Get my payment history",
    description="Returns a ledger of payment attempts for the portal session's subscription.",
)
async def get_my_payments(
    session_claims: dict = Depends(_portal_session),
    db: AsyncSession = Depends(get_async_db),
):
    subscription_id = uuid.UUID(session_claims["subscription_id"])

    # Get all invoices for this subscription
    inv_result = await db.execute(
        select(Invoice).where(Invoice.subscription_id == subscription_id)
    )
    invoices = inv_result.scalars().all()
    invoice_ids = [inv.id for inv in invoices]
    invoice_map = {inv.id: inv for inv in invoices}

    if not invoice_ids:
        return []

    # Get payment attempts for those invoices
    attempt_result = await db.execute(
        select(PaymentAttempt)
        .where(PaymentAttempt.invoice_id.in_(invoice_ids))
        .order_by(PaymentAttempt.created_at.desc())
    )
    attempts = attempt_result.scalars().all()

    return [
        PortalPaymentRead(
            date=attempt.created_at,
            amount=invoice_map[attempt.invoice_id].amount_due,
            currency=invoice_map[attempt.invoice_id].currency,
            status=attempt.status.value,
        )
        for attempt in attempts
        if attempt.invoice_id in invoice_map
    ]


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@router.post(
    "/subscriptions/pause",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Pause subscription",
)
async def pause_subscription(
    session_claims: dict = Depends(_portal_session),
    db: AsyncSession = Depends(get_async_db),
):
    subscription_id = uuid.UUID(session_claims["subscription_id"])
    sub_result = await db.execute(select(Subscription).where(Subscription.id == subscription_id))
    subscription = sub_result.scalar_one_or_none()
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    token = current_tenant_id.set(subscription.tenant_id)
    try:
        await transition_subscription(db, subscription, SubscriptionStatus.paused, reason="customer_portal")
    finally:
        current_tenant_id.reset(token)
    await db.commit()


@router.post(
    "/subscriptions/resume",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Resume subscription",
)
async def resume_subscription(
    session_claims: dict = Depends(_portal_session),
    db: AsyncSession = Depends(get_async_db),
):
    subscription_id = uuid.UUID(session_claims["subscription_id"])
    sub_result = await db.execute(select(Subscription).where(Subscription.id == subscription_id))
    subscription = sub_result.scalar_one_or_none()
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    token = current_tenant_id.set(subscription.tenant_id)
    try:
        await transition_subscription(db, subscription, SubscriptionStatus.active, reason="customer_portal")
    finally:
        current_tenant_id.reset(token)
    await db.commit()


@router.post(
    "/subscriptions/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel subscription",
)
async def cancel_subscription(
    session_claims: dict = Depends(_portal_session),
    db: AsyncSession = Depends(get_async_db),
):
    subscription_id = uuid.UUID(session_claims["subscription_id"])
    sub_result = await db.execute(select(Subscription).where(Subscription.id == subscription_id))
    subscription = sub_result.scalar_one_or_none()
    if not subscription:
        raise HTTPException(status_code=404, detail="Subscription not found")

    token = current_tenant_id.set(subscription.tenant_id)
    try:
        await transition_subscription(db, subscription, SubscriptionStatus.canceled, reason="customer_portal")
    finally:
        current_tenant_id.reset(token)
    await db.commit()


# ── Update card ───────────────────────────────────────────────────────────────

@router.post(
    "/subscriptions/update-card",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Update payment card",
    description="Registers the new card token as a payment method, sets it as default, and updates the display card metadata on the customer record.",
)
async def update_card(
    payload: PortalUpdateCardRequest,
    session_claims: dict = Depends(_portal_session),
    db: AsyncSession = Depends(get_async_db),
):
    customer_id = uuid.UUID(session_claims["sub"])
    subscription_id = uuid.UUID(session_claims["subscription_id"])

    cust_result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = cust_result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    sub_result = await db.execute(select(Subscription).where(Subscription.id == subscription_id))
    subscription = sub_result.scalar_one_or_none()

    token = current_tenant_id.set(customer.tenant_id)
    try:
        pm_svc = PaymentMethodService(db)

        # Create the new payment method from the token.
        # is_test must match the subscription's environment so the card is
        # usable for the correct mode (sandbox vs live).
        is_test = subscription.is_test if subscription else False
        pm = await pm_svc.create(
            customer_id=customer.id,
            type=PaymentMethodType.card,
            provider_token=payload.payment_token,
            is_default=False,
            is_test=is_test,
        )

        # Set it as the default for this customer
        await pm_svc.set_default(pm.id)

        # Link it to the subscription
        if subscription:
            subscription.payment_method_id = pm.id

        await db.commit()
        await db.refresh(pm)

        # Update display metadata on the customer row
        if pm.last_four:
            customer.card_last4 = pm.last_four
        if pm.card_brand:  # type: ignore[attr-defined]
            customer.card_brand = pm.card_brand  # type: ignore[attr-defined]
        await db.commit()

    finally:
        current_tenant_id.reset(token)


# ── Card update via fresh checkout ────────────────────────────────────────────

@router.post(
    "/subscriptions/create-update-card-checkout",
    response_model=CreateUpdateCardCheckoutResponse,
    summary="Create a $0 checkout to capture a fresh card token",
    description=(
        "Initiates a Nomba hosted checkout with amount=0 and tokenizeCard=true. "
        "The customer completes the checkout to provide a new card, then calls "
        "confirm-update-card with the returned order_reference."
    ),
)
async def create_update_card_checkout(
    session_claims: dict = Depends(_portal_session),
    db: AsyncSession = Depends(get_async_db),
):
    customer_id = uuid.UUID(session_claims["sub"])
    subscription_id = uuid.UUID(session_claims["subscription_id"])

    cust_result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = cust_result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    sub_result = await db.execute(select(Subscription).where(Subscription.id == subscription_id))
    subscription = sub_result.scalar_one_or_none()

    is_test = subscription.is_test if subscription else False
    provider = get_payment_provider_for_mode(is_test)

    from app.core.config import settings as app_settings
    callback_url = f"{app_settings.FRONTEND_URL}/portal/card-update-callback"

    # Use a unique order reference so this checkout can be looked up later.
    order_reference = f"card-update-{uuid.uuid4()}"

    try:
        checkout = await provider.initiate_checkout(
            amount_minor=100,
            currency="NGN",
            customer_email=customer.email,
            order_reference=order_reference,
            customer_id=str(customer.id),
            tokenize_card=True,
            callback_url=callback_url,
        )
    except ProviderError as exc:
        logger.error("create-update-card-checkout: provider error: %s", exc)
        raise HTTPException(status_code=502, detail="Payment provider error. Please try again.")

    return CreateUpdateCardCheckoutResponse(
        checkout_link=checkout.checkout_link,
        order_reference=checkout.order_reference,
    )


@router.post(
    "/subscriptions/confirm-update-card",
    response_model=ConfirmUpdateCardResponse,
    summary="Confirm card update after Nomba checkout callback",
    description=(
        "After Nomba redirects back from the card-update checkout, call this with "
        "the order_reference to extract the new token and save it as the default "
        "payment method."
    ),
)
async def confirm_update_card(
    payload: ConfirmUpdateCardRequest,
    session_claims: dict = Depends(_portal_session),
    db: AsyncSession = Depends(get_async_db),
):
    customer_id = uuid.UUID(session_claims["sub"])
    subscription_id = uuid.UUID(session_claims["subscription_id"])

    cust_result = await db.execute(select(Customer).where(Customer.id == customer_id))
    customer = cust_result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    sub_result = await db.execute(select(Subscription).where(Subscription.id == subscription_id))
    subscription = sub_result.scalar_one_or_none()

    is_test = subscription.is_test if subscription else False
    provider = get_payment_provider_for_mode(is_test)

    # Query Nomba for the checkout transaction — tokenizedCardData is in the raw response.
    tx = await provider.verify_checkout_transaction(order_reference=payload.order_reference)
    raw_data = (tx.raw or {}).get("data") or {}
    tokenized = raw_data.get("tokenizedCardData") or {}
    token_key = tokenized.get("tokenKey", "").strip()

    if not token_key or token_key == "N/A":
        logger.warning(
            "confirm-update-card: no tokenKey in Nomba response for order %s",
            payload.order_reference,
        )
        return ConfirmUpdateCardResponse(success=False)

    import re
    card_pan = tokenized.get("cardPan", "")
    last_four = tokenized.get("cardLast4Digits") or (
        re.sub(r"[^\d]", "", card_pan)[-4:] if card_pan else None
    )
    expiry_month = None
    expiry_year = None
    raw_month = tokenized.get("tokenExpiryMonth", "")
    raw_year = tokenized.get("tokenExpiryYear", "")
    if raw_month and raw_month not in ("N/A", ""):
        try:
            expiry_month = int(raw_month)
        except ValueError:
            pass
    if raw_year and raw_year not in ("N/A", ""):
        try:
            expiry_year = int(raw_year)
        except ValueError:
            pass

    tenant_token = current_tenant_id.set(customer.tenant_id)
    try:
        pm_svc = PaymentMethodService(db)

        # Avoid saving a duplicate token.
        from sqlalchemy import select as sa_select
        dup = (await db.execute(
            sa_select(PaymentMethod).where(
                PaymentMethod.tenant_id == customer.tenant_id,
                PaymentMethod.customer_id == customer.id,
                PaymentMethod.provider_token == token_key,
            )
        )).scalar_one_or_none()

        if dup:
            pm = dup
        else:
            pm = await pm_svc.create(
                customer_id=customer.id,
                type=PaymentMethodType.card,
                provider_token=token_key,
                last_four=last_four,
                expiry_month=expiry_month,
                expiry_year=expiry_year,
                is_default=False,
                is_test=is_test,
            )

        await pm_svc.set_default(pm.id)

        if subscription:
            subscription.payment_method_id = pm.id

        # Update display metadata on the customer row.
        if last_four:
            customer.card_last4 = last_four
        card_type = tokenized.get("cardType")
        if card_type:
            customer.card_brand = card_type

        await db.commit()

    finally:
        current_tenant_id.reset(tenant_token)

    return ConfirmUpdateCardResponse(success=True)
