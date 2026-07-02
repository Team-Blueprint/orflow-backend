"""Provider wiring: a single shared ``httpx.AsyncClient`` and provider instance.

It builds one NombaProvider, keeps it alive for the application's lifetime, 
and gives every part of the system access to that same instance. 
This centralizes resource management, avoids repeatedly creating expensive HTTP clients, 
enables connection pooling, and integrates cleanly with FastAPI's dependency injection and 
any background workers that also need to communicate with Nomba.
"""

from __future__ import annotations

import httpx

from app.providers.base import PaymentProviderAdapter
from app.providers.nomba import NombaProvider

_client: httpx.AsyncClient | None = None
_provider: PaymentProviderAdapter | None = None


def _build() -> tuple[httpx.AsyncClient, PaymentProviderAdapter]:
    client = httpx.AsyncClient()
    return client, NombaProvider(client)


async def init_payment_provider() -> PaymentProviderAdapter:
    """Create the shared client + provider. Idempotent — safe to call once at
    startup."""
    global _client, _provider
    if _provider is None:
        _client, _provider = _build()
    return _provider


async def close_payment_provider() -> None:
    """Close the shared HTTP client on shutdown."""
    global _client, _provider
    if _client is not None:
        await _client.aclose()
    _client = None
    _provider = None


def get_payment_provider() -> PaymentProviderAdapter:
    """Return the shared provider instance. Raises if not initialized."""
    
    global _client, _provider
    if _provider is None:
        _client, _provider = _build()
    return _provider
