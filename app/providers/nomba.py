"""Nomba payment provider adapter.

Concrete implementation of :class:`PaymentProviderAdapter` backed by the Nomba
API (https://developer.nomba.com). All HTTP traffic goes through a shared
``httpx.AsyncClient`` with per-request timeouts. Nomba's response/error codes
are translated into the engine's internal :class:`FailureReason` /
:class:`PaymentStatus` here, so no Nomba-specific concept leaks outward.

Authentication uses the OAuth ``client_credentials`` grant. The access token is
cached in-process and refreshed shortly before expiry.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from app.core.config import Settings, settings as default_settings
from app.providers.base import (
    ChargeResult,
    CheckoutSession,
    DirectDebitMandateResult,
    FailureReason,
    MandateDebitResult,
    MandateStatusResult,
    PaymentProviderAdapter,
    PaymentStatus,
    ProviderAuthError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    TokenizedCard,
    TransactionStatus,
    TransferResult,
)

logger = logging.getLogger(__name__)

# Nomba's "everything is fine" response code.
_SUCCESS_CODE = "00"
_TWO_DP = Decimal("0.01")


def _minor_to_major_str(amount_minor: int) -> str:
    """Convert an integer minor-unit amount (e.g. kobo) to Nomba's major-unit
    decimal string (e.g. ``"10000.00"``). Assumes 2-decimal currencies (NGN,
    USD, CDF)."""
    return str((Decimal(amount_minor) / Decimal(100)).quantize(_TWO_DP))


def _major_to_minor(amount: object) -> int | None:
    """Convert a Nomba major-unit amount back to integer minor units."""
    if amount is None:
        return None
    try:
        return int((Decimal(str(amount)) * 100).quantize(Decimal("1")))
    except (ArithmeticError, ValueError, TypeError):
        return None


def _map_failure_reason(code: str | None, message: str | None) -> FailureReason:
    """Best-effort mapping of a Nomba decline (code + gateway message) onto the
    engine's internal :class:`FailureReason`.

    Nomba does not publish an exhaustive decline-code table, so we key off the
    gateway message text where available and fall back to ``generic_decline``.
    """
    text = (message or "").lower()

    if any(kw in text for kw in ("insufficient", "not enough", "low balance")):
        return FailureReason.insufficient_funds
    if "expire" in text:
        return FailureReason.expired_card
    if "do not honor" in text or "do not honour" in text or "dnh" in text:
        return FailureReason.do_not_honor
    if any(kw in text for kw in ("otp", "authenticat", "3ds", "3-d secure", "step up", "step-up")):
        return FailureReason.requires_action
    if any(kw in text for kw in ("invalid card", "invalid token", "card not", "no card")):
        return FailureReason.invalid_payment_method
    if "declin" in text or "not permitted" in text or "restricted" in text:
        return FailureReason.card_declined
    return FailureReason.generic_decline


class NombaProvider(PaymentProviderAdapter):
    """Nomba-backed payment provider adapter.

    The ``httpx.AsyncClient`` is injected so its lifecycle can be owned by the
    FastAPI app (and so tests can supply a ``MockTransport``-backed client).
    """

    def __init__(self, client: httpx.AsyncClient, settings: Settings | None = None) -> None:
        self._client = client
        self._settings = settings or default_settings
        self._token: str | None = None
        # UTC instant (seconds since epoch) at which the cached token expires.
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

    # ------------------------------------------------------------------ auth

    def _require_credentials(self) -> None:
        s = self._settings
        if not (s.NOMBA_CLIENT_ID and s.NOMBA_CLIENT_SECRET and s.NOMBA_ACCOUNT_ID):
            raise ProviderAuthError(
                "Nomba credentials are not configured "
                "(NOMBA_CLIENT_ID / NOMBA_CLIENT_SECRET / NOMBA_ACCOUNT_ID)."
            )

    def _token_is_fresh(self) -> bool:
        if self._token is None:
            return False
        now = datetime.now(timezone.utc).timestamp()
        return now < (self._token_expires_at - self._settings.NOMBA_TOKEN_LEEWAY_SECONDS)

    async def _ensure_token(self) -> str:
        if self._token_is_fresh():
            return self._token  # type: ignore[return-value]
        async with self._token_lock:
            # Re-check inside the lock — another coroutine may have refreshed it.
            if self._token_is_fresh():
                return self._token  # type: ignore[return-value]
            await self._fetch_token()
            return self._token  # type: ignore[return-value]

    async def _fetch_token(self) -> None:
        self._require_credentials()
        s = self._settings
        body = await self._raw_request(
            "POST",
            "/v1/auth/token/issue",
            json={
                "grant_type": "client_credentials",
                "client_id": s.NOMBA_CLIENT_ID,
                "client_secret": s.NOMBA_CLIENT_SECRET,
            },
            headers={"accountId": s.NOMBA_ACCOUNT_ID},
            expected_auth=False,
        )
        data = (body or {}).get("data") or {}
        token = data.get("access_token")
        if not token:
            raise ProviderAuthError("Nomba auth response did not contain an access_token.")
        self._token = token
        self._token_expires_at = self._parse_expiry(data.get("expiresAt"))

    @staticmethod
    def _parse_expiry(expires_at: str | None) -> float:
        """Parse Nomba's ISO-8601 ``expiresAt`` into an epoch timestamp.

        Falls back to a conservative short lifetime if absent/unparseable so we
        re-authenticate rather than reuse an unknown-age token.
        """
        if expires_at:
            try:
                dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.timestamp()
            except ValueError:
                logger.warning("Unparseable Nomba expiresAt: %r", expires_at)
        return datetime.now(timezone.utc).timestamp() + 300

    # --------------------------------------------------------------- transport

    async def _raw_request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        headers: dict | None = None,
        expected_auth: bool = True,
    ) -> dict:
        """Issue a single HTTP request and return the parsed JSON body.

        Raises a :class:`ProviderError` subclass for transport problems, auth
        failures, 5xx, and malformed/unsuccessful HTTP responses. Business-level
        outcomes (e.g. a card decline returned with a 2xx/4xx body) are NOT
        raised here — the calling method inspects the returned body.
        """
        url = self._settings.NOMBA_BASE_URL.rstrip("/") + path
        try:
            response = await self._client.request(
                method, url, json=json, params=params, headers=headers,
                timeout=self._settings.NOMBA_HTTP_TIMEOUT,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(f"Nomba request timed out: {method} {path}") from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(f"Nomba request failed: {method} {path}") from exc

        if response.status_code == 401:
            raise ProviderAuthError(f"Nomba returned 401 for {method} {path}.")
        if response.status_code >= 500:
            raise ProviderUnavailableError(
                f"Nomba returned {response.status_code} for {method} {path}."
            )

        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(
                f"Nomba returned a non-JSON response ({response.status_code}) for {method} {path}."
            ) from exc

    async def _authed_request(
        self, method: str, path: str, *, json: dict | None = None, params: dict | None = None
    ) -> dict:
        """Authenticated request with a one-shot token refresh on 401."""
        token = await self._ensure_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "accountId": self._settings.NOMBA_ACCOUNT_ID,
        }
        try:
            return await self._raw_request(method, path, json=json, params=params, headers=headers)
        except ProviderAuthError:
            # Token may have been revoked/expired early — refresh once and retry.
            self._token = None
            token = await self._ensure_token()
            headers["Authorization"] = f"Bearer {token}"
            return await self._raw_request(method, path, json=json, params=params, headers=headers)

    # ----------------------------------------------------------------- methods

    async def initiate_checkout(
        self,
        *,
        amount_minor: int,
        currency: str,
        customer_email: str,
        order_reference: str,
        customer_id: str | None = None,
        callback_url: str | None = None,
        tokenize_card: bool = True,
    ) -> CheckoutSession:
        order: dict = {
            "orderReference": order_reference,
            "customerEmail": customer_email,
            "callbackUrl": callback_url or self._settings.NOMBA_CALLBACK_URL,
            "amount": _minor_to_major_str(amount_minor),
            "currency": currency,
            "accountId": self._settings.NOMBA_SUBACCOUNT_ID or self._settings.NOMBA_ACCOUNT_ID,
            # When tokenizing a card only card-based payment is valid;
            # including "Transfer" causes Nomba to reject the request.
            "allowedPaymentMethods": ["Card"] if tokenize_card else ["Card", "Transfer"],
        }
        if customer_id:
            order["customerId"] = customer_id

        body = await self._authed_request(
            "POST", "/v1/checkout/order",
            json={"order": order, "tokenizeCard": tokenize_card},
        )
        data = (body or {}).get("data") or {}
        checkout_link = data.get("checkoutLink")
        logger.info(
            "Nomba checkout response: code=%s description=%s checkoutLink=%s orderReference=%s",
            (body or {}).get("code"),
            (body or {}).get("description"),
            checkout_link,
            data.get("orderReference"),
        )
        if (body or {}).get("code") != _SUCCESS_CODE or not checkout_link:
            raise ProviderError(
                f"Nomba did not return a checkout link "
                f"(code={body.get('code')!r}, description={body.get('description')!r})."
            )
        return CheckoutSession(
            checkout_link=checkout_link,
            order_reference=data.get("orderReference") or order_reference,
            raw=body,
        )

    async def charge_tokenized_card(
        self,
        *,
        token: str,
        amount_minor: int,
        currency: str,
        idempotency_key: str,
        customer_email: str,
        customer_id: str | None = None,
        callback_url: str | None = None,
    ) -> ChargeResult:
        # The idempotency key (invoice_id + attempt_number) becomes the order
        # reference, so a retried call carries the same reference and is safe to
        # reconcile/dedupe.
        order: dict = {
            "orderReference": idempotency_key,
            "customerEmail": customer_email,
            "callbackUrl": callback_url or self._settings.NOMBA_CALLBACK_URL,
            "amount": _minor_to_major_str(amount_minor),
            "currency": currency,
            "accountId": self._settings.NOMBA_SUBACCOUNT_ID or self._settings.NOMBA_ACCOUNT_ID,
        }
        if customer_id:
            order["customerId"] = customer_id

        body = await self._authed_request(
            "POST", "/v1/checkout/tokenized-card-payment",
            json={"tokenKey": token, "order": order},
        )
        code = (body or {}).get("code")
        data = (body or {}).get("data") or {}
        message = data.get("message") or (body or {}).get("description")

        if code == _SUCCESS_CODE and data.get("status") is True:
            return ChargeResult(
                status=PaymentStatus.success,
                provider_reference=idempotency_key,
                message=message,
                raw=body,
            )

        reason = _map_failure_reason(code, message)
        status = (
            PaymentStatus.requires_action
            if reason is FailureReason.requires_action
            else PaymentStatus.failed
        )
        return ChargeResult(
            status=status,
            provider_reference=idempotency_key,
            failure_reason=reason,
            message=message,
            raw=body,
        )

    async def list_tokenized_cards(
        self,
        *,
        customer_email: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int | None = None,
    ) -> list[TokenizedCard]:
        query: dict = {}
        if customer_email:
            query["customerEmail"] = customer_email
        if start_date:
            query["startDate"] = start_date
        if end_date:
            query["endDate"] = end_date
        if page is not None:
            query["page"] = str(page)

        body = await self._authed_request(
            "GET", "/v1/checkout/tokenized-card-data", params=query or None,
        )

        data = (body or {}).get("data") or {}
        raw_list: list[dict] = data.get("tokenizedCardDataList") or []
        return [
            TokenizedCard(
                token_key=item.get("tokenKey", ""),
                customer_email=item.get("customerEmail"),
                card_type=item.get("cardType"),
                card_pan=item.get("cardPan"),
                token_expiration_date=item.get("tokenExpirationDate"),
            )
            for item in raw_list
        ]

    async def transfer(
        self,
        *,
        amount_minor: int,
        account_number: str,
        account_name: str,
        bank_code: str,
        idempotency_key: str,
        narration: str | None = None,
    ) -> TransferResult:
        payload: dict = {
            "amount": _minor_to_major_str(amount_minor),
            "accountNumber": account_number,
            "accountName": account_name,
            "bankCode": bank_code,
            "merchantTxRef": idempotency_key,  # Nomba's idempotency key
        }
        if narration:
            payload["narration"] = narration

        body = await self._authed_request("POST", "/v2/transfers/bank", json=payload)
        data = (body or {}).get("data") or {}
        description = (body or {}).get("description")
        reference = data.get("id") or idempotency_key
        raw_status = str(data.get("status") or description or "").upper()

        if raw_status == "SUCCESS":
            status, reason = PaymentStatus.success, None
        elif raw_status in {"PENDING_BILLING", "PROCESSING"}:
            status, reason = PaymentStatus.pending, None
        elif raw_status == "INSUFFICIENT_BALANCE":
            status, reason = PaymentStatus.failed, FailureReason.insufficient_funds
        else:
            status, reason = PaymentStatus.failed, FailureReason.processing_error

        return TransferResult(
            status=status,
            provider_reference=reference,
            failure_reason=reason,
            message=description,
            raw=body,
        )

    async def verify_transaction(self, *, reference: str) -> TransactionStatus:
        body = await self._authed_request(
            "GET", f"/v1/transactions/requery/{reference}"
        )
        data = (body or {}).get("data") or {}
        raw_status = str(data.get("status") or "").upper()

        if raw_status == "SUCCESS":
            status, reason = PaymentStatus.success, None
        elif raw_status == "REFUND":
            status, reason = PaymentStatus.refunded, FailureReason.generic_decline
        elif raw_status == "PAYMENT_FAILED":
            status, reason = PaymentStatus.failed, FailureReason.generic_decline
        else:
            # PENDING_BILLING, CANCELLED, REVERSED_BY_VENDOR — not yet final.
            status, reason = PaymentStatus.pending, None

        return TransactionStatus(
            status=status,
            provider_reference=data.get("id") or reference,
            amount_minor=_major_to_minor(data.get("amount")),
            failure_reason=reason,
            message=data.get("gatewayMessage") or (body or {}).get("description"),
            raw=body,
        )

    async def verify_checkout_transaction(self, *, order_reference: str) -> TransactionStatus:
        body = await self._authed_request(
            "GET",
            f"/v1/checkout/transaction?idType=ORDER_REFERENCE&id={order_reference}",
        )
        data = (body or {}).get("data") or {}
        success = data.get("success") is True
        message = data.get("message") or (body or {}).get("description")
        order = data.get("order") or {}

        if success:
            status = PaymentStatus.success
            failure_reason = None
        else:
            status = PaymentStatus.failed
            failure_reason = FailureReason.generic_decline

        return TransactionStatus(
            status=status,
            provider_reference=order.get("orderReference") or order_reference,
            amount_minor=_major_to_minor(order.get("amount")),
            failure_reason=failure_reason,
            message=message,
            raw=body,
        )

    async def create_direct_debit_mandate(
        self,
        *,
        customer_account_number: str,
        bank_code: str,
        customer_name: str,
        customer_account_name: str,
        amount_minor: int,
        currency: str,
        frequency: str,
        merchant_reference: str,
        start_date: str,
        end_date: str,
        customer_email: str,
        customer_address: str | None = None,
        narration: str | None = None,
        customer_phone_number: str | None = None,
        start_immediately: bool | None = None,
    ) -> DirectDebitMandateResult:
        payload: dict = {
            "customerAccountNumber": customer_account_number,
            "bankCode": bank_code,
            "customerName": customer_name,
            "customerAccountName": customer_account_name,
            "amount": _minor_to_major_str(amount_minor),
            "frequency": frequency.upper(),
            "merchantReference": merchant_reference,
            "startDate": start_date,
            "endDate": end_date,
            "customerEmail": customer_email,
        }
        if customer_address:
            payload["customerAddress"] = customer_address
        if narration:
            payload["narration"] = narration
        if customer_phone_number:
            payload["customerPhoneNumber"] = customer_phone_number
        if start_immediately is not None:
            payload["startImmediately"] = start_immediately

        body = await self._authed_request("POST", "/v1/direct-debits", json=payload)
        data = (body or {}).get("data") or {}

        if (body or {}).get("code") == _SUCCESS_CODE:
            return DirectDebitMandateResult(
                mandate_id=data.get("mandateId", ""),
                merchant_reference=data.get("merchantReference"),
                phone_number=data.get("phoneNumber"),
                description=data.get("description"),
                raw=body,
            )

        raise ProviderError(
            f"Nomba mandate creation failed: "
            f"(code={body.get('code')!r}, message={body.get('description')!r})."
        )

    async def debit_mandate(
        self,
        *,
        mandate_id: str,
        amount_minor: int,
        currency: str,
    ) -> MandateDebitResult:
        body = await self._authed_request(
            "POST", "/v1/direct-debits/debit-mandate",
            json={
                "mandateId": mandate_id,
                "amount": _minor_to_major_str(amount_minor),
                "currency": currency,
            },
        )
        code = (body or {}).get("code")
        description = (body or {}).get("description")
        data = (body or {}).get("data") or {}
        status_bool = (body or {}).get("status") is True

        if code == _SUCCESS_CODE and status_bool:
            payment_status = PaymentStatus.success
        else:
            payment_status = PaymentStatus.failed

        return MandateDebitResult(
            mandate_id=data.get("mandateId") or mandate_id,
            status=payment_status,
            amount_minor=amount_minor,
            message=data.get("message") or description,
            raw=body,
        )

    async def get_mandate_status(
        self,
        *,
        mandate_id: str,
    ) -> MandateStatusResult:
        body = await self._authed_request(
            "GET", f"/v1/direct-debits/{mandate_id}",
        )
        data = (body or {}).get("data") or {}

        return MandateStatusResult(
            mandate_id=data.get("mandateId", ""),
            customer_account_name=data.get("customerAccountName"),
            customer_account_number=data.get("customerAccountNumber"),
            mandate_status=data.get("mandateStatus"),
            rejection_comment=data.get("rejectionComment"),
            mandate_advice_status=data.get("mandateAdviceStatus"),
            raw=body,
        )

    async def fetch_transactions(
        self,
        *,
        date_from: str,
        date_to: str,
        cursor: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Fetch a page of parent-account transactions for reconciliation.

        Calls ``GET /v1/transactions/accounts`` with date-range filtering and
        optional cursor-based pagination. Returns the raw ``results`` list;
        callers use the returned ``cursor`` field (from the last page's
        response) to fetch the next page.
        """
        params: dict = {
            "dateFrom": date_from,
            "dateTo": date_to,
            "limit": str(min(limit, 200)),
        }
        if cursor:
            params["cursor"] = cursor

        token = await self._ensure_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "accountId": self._settings.NOMBA_ACCOUNT_ID,
        }

        url = self._settings.NOMBA_BASE_URL.rstrip("/") + "/v1/transactions/accounts"
        try:
            response = await self._client.request(
                "GET", url, params=params, headers=headers,
                timeout=self._settings.NOMBA_HTTP_TIMEOUT,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Nomba transaction list request timed out.") from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError("Nomba transaction list request failed.") from exc

        if response.status_code >= 500:
            raise ProviderUnavailableError(
                f"Nomba returned {response.status_code} for transaction list."
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError(
                f"Nomba returned non-JSON for transaction list ({response.status_code})."
            ) from exc

        data = (body or {}).get("data") or {}
        results: list[dict] = data.get("results") or []

        return results
