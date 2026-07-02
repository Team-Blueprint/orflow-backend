"""Unit tests for the Nomba payment provider adapter (no network access)."""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.providers.base import (
    FailureReason,
    PaymentStatus,
    ProviderAuthError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.providers.nomba import (
    NombaProvider,
    _major_to_minor,
    _map_failure_reason,
    _minor_to_major_str,
)


# --------------------------------------------------------------- pure helpers

def test_minor_to_major_str():
    assert _minor_to_major_str(1_000_000) == "10000.00"
    assert _minor_to_major_str(150) == "1.50"
    assert _minor_to_major_str(0) == "0.00"


def test_major_to_minor_roundtrip():
    assert _major_to_minor("10000.00") == 1_000_000
    assert _major_to_minor(1.5) == 150
    assert _major_to_minor(None) is None
    assert _major_to_minor("not-a-number") is None


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Insufficient funds", FailureReason.insufficient_funds),
        ("Card has expired", FailureReason.expired_card),
        ("Do not honor", FailureReason.do_not_honor),
        ("Please complete OTP authentication", FailureReason.requires_action),
        ("Invalid card token", FailureReason.invalid_payment_method),
        ("Transaction declined by issuer", FailureReason.card_declined),
        ("Something weird happened", FailureReason.generic_decline),
        (None, FailureReason.generic_decline),
    ],
)
def test_map_failure_reason(message, expected):
    assert _map_failure_reason(None, message) is expected


# ------------------------------------------------------------------ auth flow

async def test_token_is_fetched_once_and_cached(provider, router):
    router.set("POST", "/v1/checkout/order", {
        "code": "00", "description": "ok",
        "data": {"checkoutLink": "https://pay/x", "orderReference": "ord"},
    })
    for ref in ("ord-1", "ord-2"):
        await provider.initiate_checkout(
            amount_minor=500000, currency="NGN",
            customer_email="a@b.com", order_reference=ref,
        )
    # Two business calls, but the access token is fetched only once.
    assert router.auth_calls() == 1


async def test_missing_credentials_raises():
    settings = Settings(NOMBA_CLIENT_ID="", NOMBA_CLIENT_SECRET="", NOMBA_ACCOUNT_ID="")
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    provider = NombaProvider(client, settings)
    with pytest.raises(ProviderAuthError):
        await provider.verify_transaction(reference="sess-1")
    await client.aclose()


async def test_401_triggers_one_refresh_then_succeeds(provider, router):
    calls = {"n": 0}

    def charge_handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(401, json={"code": "401", "description": "expired"})
        return httpx.Response(200, json={
            "code": "00", "description": "Success",
            "data": {"status": True, "message": "Approved"},
        })

    router.set_handler("POST", "/v1/checkout/tokenized-card-payment", charge_handler)
    result = await provider.charge_tokenized_card(
        token="tok", amount_minor=1000, currency="NGN",
        idempotency_key="inv-1:1", customer_email="a@b.com",
    )
    assert result.succeeded
    assert calls["n"] == 2
    assert router.auth_calls() == 2  # initial + refresh


# ------------------------------------------------------------ initiate_checkout

async def test_initiate_checkout_builds_request_and_parses(provider, router):
    router.set("POST", "/v1/checkout/order", {
        "code": "00", "description": "ok",
        "data": {"checkoutLink": "https://pay/abc", "orderReference": "ord-1"},
    })
    session = await provider.initiate_checkout(
        amount_minor=1_000_000, currency="NGN",
        customer_email="cust@x.com", order_reference="ord-1",
        customer_id="cust-123", tokenize_card=True,
    )
    assert session.checkout_link == "https://pay/abc"
    assert session.order_reference == "ord-1"

    body = router.last_body()
    assert body["tokenizeCard"] is True
    assert body["order"]["amount"] == "10000.00"
    assert body["order"]["currency"] == "NGN"
    assert body["order"]["customerId"] == "cust-123"
    assert body["order"]["callbackUrl"] == "https://merchant.test/callback"


async def test_initiate_checkout_missing_link_raises(provider, router):
    router.set("POST", "/v1/checkout/order", {"code": "99", "description": "nope", "data": {}})
    with pytest.raises(Exception):
        await provider.initiate_checkout(
            amount_minor=1000, currency="NGN",
            customer_email="a@b.com", order_reference="ord-1",
        )


# --------------------------------------------------------- charge_tokenized_card

async def test_charge_success(provider, router):
    router.set("POST", "/v1/checkout/tokenized-card-payment", {
        "code": "00", "description": "Success",
        "data": {"status": True, "message": "Approved by Financial Institution"},
    })
    result = await provider.charge_tokenized_card(
        token="tok-1", amount_minor=250000, currency="NGN",
        idempotency_key="inv-9:1", customer_email="a@b.com",
    )
    assert result.status is PaymentStatus.success
    assert result.succeeded
    assert result.provider_reference == "inv-9:1"  # reconciliation reference
    assert router.last_body()["tokenKey"] == "tok-1"
    assert router.last_body()["order"]["orderReference"] == "inv-9:1"


async def test_charge_decline_maps_failure_reason(provider, router):
    router.set("POST", "/v1/checkout/tokenized-card-payment", {
        "code": "51", "description": "Declined",
        "data": {"status": False, "message": "Insufficient funds"},
    })
    result = await provider.charge_tokenized_card(
        token="tok", amount_minor=1000, currency="NGN",
        idempotency_key="inv-2:1", customer_email="a@b.com",
    )
    assert result.status is PaymentStatus.failed
    assert result.failure_reason is FailureReason.insufficient_funds
    assert not result.succeeded


async def test_charge_requires_action(provider, router):
    router.set("POST", "/v1/checkout/tokenized-card-payment", {
        "code": "0A", "description": "Auth required",
        "data": {"status": False, "message": "OTP authentication required"},
    })
    result = await provider.charge_tokenized_card(
        token="tok", amount_minor=1000, currency="NGN",
        idempotency_key="inv-3:1", customer_email="a@b.com",
    )
    assert result.status is PaymentStatus.requires_action
    assert result.failure_reason is FailureReason.requires_action


# ------------------------------------------------------------- verify_transaction

@pytest.mark.parametrize(
    "nomba_status,expected_status,expected_reason",
    [
        ("SUCCESS", PaymentStatus.success, None),
        ("REFUND", PaymentStatus.refunded, FailureReason.generic_decline),
        ("PAYMENT_FAILED", PaymentStatus.failed, FailureReason.generic_decline),
        ("PENDING_BILLING", PaymentStatus.pending, None),
        ("CANCELLED", PaymentStatus.pending, None),
    ],
)
async def test_verify_transaction_status_mapping(
    provider, router, nomba_status, expected_status, expected_reason
):
    router.set("GET", "/v1/transactions/requery/sess-1", {
        "code": "00", "description": "ok",
        "data": {"id": "txn-1", "status": nomba_status, "amount": 5000.00,
                 "gatewayMessage": "msg"},
    })
    result = await provider.verify_transaction(reference="sess-1")
    assert result.status is expected_status
    assert result.failure_reason is expected_reason
    assert result.provider_reference == "txn-1"
    assert result.amount_minor == 500000


# -------------------------------------------------------------------- transfer

async def test_transfer_success(provider, router):
    router.set("POST", "/v2/transfers/bank", {
        "code": "00", "description": "SUCCESS",
        "data": {"id": "tr-1", "status": "SUCCESS", "amount": "100.00", "fee": 10,
                 "timeCreated": "2026-01-01T00:00:00Z", "type": "transfer"},
    })
    result = await provider.transfer(
        amount_minor=10000, account_number="0123456789",
        account_name="Jane Doe", bank_code="011",
        idempotency_key="payout-1", narration="refund",
    )
    assert result.status is PaymentStatus.success
    assert result.provider_reference == "tr-1"
    assert router.last_body()["merchantTxRef"] == "payout-1"


async def test_transfer_insufficient_balance(provider, router):
    router.set("POST", "/v2/transfers/bank", {
        "code": "E1", "description": "INSUFFICIENT_BALANCE", "data": {},
    })
    result = await provider.transfer(
        amount_minor=10000, account_number="0123456789",
        account_name="Jane Doe", bank_code="011", idempotency_key="payout-2",
    )
    assert result.status is PaymentStatus.failed
    assert result.failure_reason is FailureReason.insufficient_funds


# ----------------------------------------------------------- transport errors

async def test_timeout_raises_provider_timeout(provider, router):
    def boom(request):
        raise httpx.TimeoutException("timed out", request=request)

    router.set_handler("GET", "/v1/transactions/requery/sess-x", boom)
    with pytest.raises(ProviderTimeoutError):
        await provider.verify_transaction(reference="sess-x")


async def test_5xx_raises_provider_unavailable(provider, router):
    router.set("POST", "/v1/checkout/tokenized-card-payment",
               {"code": "500", "description": "boom"}, status_code=503)
    with pytest.raises(ProviderUnavailableError):
        await provider.charge_tokenized_card(
            token="tok", amount_minor=1000, currency="NGN",
            idempotency_key="inv-x:1", customer_email="a@b.com",
        )
