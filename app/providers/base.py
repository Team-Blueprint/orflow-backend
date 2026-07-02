"""Payment provider adapter boundary.

This file defines a clean interface between your subscription engine and any payment provider, 
ensuring that the rest of your application is independent of Nomba, 
uses standardized request and response objects, avoids provider-specific logic, 
and leaves business logic such as database persistence to higher layers.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class FailureReason(str, enum.Enum):
    """Internal, provider-agnostic reason a charge or transfer did not succeed.

    Concrete adapters map provider-specific error/response codes onto these
    values so the dunning logic  can reason about retries
    without knowing anything about Nomba.
    """

    insufficient_funds = "insufficient_funds"
    expired_card = "expired_card"
    do_not_honor = "do_not_honor"
    card_declined = "card_declined"
    invalid_payment_method = "invalid_payment_method"
    requires_action = "requires_action"  # step-up auth needed — not a billing failure
    generic_decline = "generic_decline"
    processing_error = "processing_error"  # provider/transport-level problem
    unknown = "unknown"


class PaymentStatus(str, enum.Enum):
    """Provider-agnostic outcome of a charge/transfer/verify call."""

    success = "success"
    pending = "pending"  # accepted, final outcome arrives via webhook/requery
    requires_action = "requires_action"  # customer must complete step-up auth
    failed = "failed"
    refunded = "refunded"


class CheckoutSession(BaseModel):
    """Result of initiating a hosted checkout for collecting + tokenizing a card."""

    checkout_link: str
    order_reference: str
    raw: dict = Field(default_factory=dict)


class ChargeResult(BaseModel):
    """Result of charging a tokenized card."""

    status: PaymentStatus
    provider_reference: str | None = None  # store on PaymentAttempt for reconciliation
    failure_reason: FailureReason | None = None
    message: str | None = None
    raw: dict = Field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status is PaymentStatus.success


class TransferResult(BaseModel):
    """Result of an outbound bank transfer (e.g. refunds/payouts)."""

    status: PaymentStatus
    provider_reference: str | None = None
    failure_reason: FailureReason | None = None
    message: str | None = None
    raw: dict = Field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status is PaymentStatus.success


class TransactionStatus(BaseModel):
    """Result of verifying/requerying a previously initiated transaction."""

    status: PaymentStatus
    provider_reference: str | None = None
    amount_minor: int | None = None  # smallest currency unit, matching the engine
    failure_reason: FailureReason | None = None
    message: str | None = None
    raw: dict = Field(default_factory=dict)


class PaymentProviderAdapter(ABC):
    """Contract every payment provider integration must implement.

    Amounts are always expressed in the smallest currency unit (e.g. kobo for
    NGN), consistent with how the engine stores ``Plan.amount`` and invoice
    totals. Adapters convert to whatever representation the provider expects.
    """

    @abstractmethod
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
        """Create a hosted checkout session.

        On completion the provider notifies us asynchronously (webhook) with a
        card token, which the webhook handler stores on the ``PaymentMethod``.
        Raw card data is never seen or stored by this engine.
        """

    @abstractmethod
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
        """Charge a previously tokenized card.

        ``idempotency_key`` must be derived from ``invoice_id + attempt_number``
        by the caller so a retried call cannot double-charge.
        """

    @abstractmethod
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
        """Send funds to an external bank account (refunds/payouts)."""

    @abstractmethod
    async def verify_transaction(self, *, reference: str) -> TransactionStatus:
        """Confirm the authoritative status of a transaction before giving value."""

    async def fetch_transactions(
        self,
        *,
        date_from: str,
        date_to: str,
        cursor: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Fetch a page of transactions from the provider for reconciliation.

        Returns a list of raw transaction dicts. The provider adapter is
        responsible for pagination — callers iterate until an empty list or
        a ``cursor`` signal is returned.

        Default implementation returns an empty list so that test stubs
        that don't override this method don't break.
        """
        return []


class ProviderError(Exception):
    """Base class for transport/provider-level failures.

    Raised for ambiguous problems (timeouts, connection errors, 5xx, malformed
    responses, auth failures) where we cannot conclude the charge was declined.
    Callers should treat these as *retryable / pending*, never as a hard
    decline. Clean business declines are returned as result DTOs instead.
    """


class ProviderAuthError(ProviderError):
    """Authentication with the provider failed (bad credentials / token)."""


class ProviderTimeoutError(ProviderError):
    """The provider did not respond within the configured timeout."""


class ProviderUnavailableError(ProviderError):
    """The provider returned a 5xx or was otherwise unreachable."""
