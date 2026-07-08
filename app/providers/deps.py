"""Payment provider wiring — two singleton NombaProvider instances.

One instance is configured for live credentials (used when the request's API key
is pk_live / sk_live) and one for sandbox credentials (used when the request's
API key is pk_test / sk_test).

Both providers share the same NombaProvider class; they simply point at different
base URLs and authenticate with different credentials. Provider selection is
performed at request time via ``get_payment_provider_for_mode(is_test)``.
"""

from __future__ import annotations

import httpx

from app.core.config import Settings, settings as default_settings
from app.providers.base import PaymentProviderAdapter
from app.providers.nomba import NombaProvider

# --- Singletons ---------------------------------------------------------------

_live_client: httpx.AsyncClient | None = None
_live_provider: NombaProvider | None = None

_sandbox_client: httpx.AsyncClient | None = None
_sandbox_provider: NombaProvider | None = None


# --- Lifecycle ----------------------------------------------------------------

async def init_payment_providers() -> None:
    """Initialize both live and sandbox Nomba provider instances.

    Idempotent — safe to call once at application startup.
    """
    global _live_client, _live_provider, _sandbox_client, _sandbox_provider

    # Live provider (uses NOMBA_* credentials from .env)
    _live_client = httpx.AsyncClient()
    _live_provider = NombaProvider(_live_client, settings=default_settings)

    # Sandbox provider (uses NOMBA_SANDBOX_* credentials from .env)
    sandbox_settings = Settings(
        NOMBA_BASE_URL=default_settings.NOMBA_SANDBOX_BASE_URL,
        NOMBA_CLIENT_ID=default_settings.NOMBA_SANDBOX_CLIENT_ID,
        NOMBA_CLIENT_SECRET=default_settings.NOMBA_SANDBOX_CLIENT_SECRET,
        NOMBA_ACCOUNT_ID=default_settings.NOMBA_ACCOUNT_ID,
        NOMBA_SUB_ACCOUNT_ID=default_settings.NOMBA_SUB_ACCOUNT_ID,
        NOMBA_CALLBACK_URL=default_settings.NOMBA_SANDBOX_CALLBACK_URL,
        # Inherit all non-Nomba settings (timeouts, JWT, DB, etc.)
        NOMBA_HTTP_TIMEOUT=default_settings.NOMBA_HTTP_TIMEOUT,
        NOMBA_TOKEN_LEEWAY_SECONDS=default_settings.NOMBA_TOKEN_LEEWAY_SECONDS,
        NOMBA_WEBHOOK_SECRET=default_settings.NOMBA_WEBHOOK_SECRET,
    )
    _sandbox_client = httpx.AsyncClient()
    _sandbox_provider = NombaProvider(_sandbox_client, settings=sandbox_settings)


async def close_payment_providers() -> None:
    """Close both HTTP clients on application shutdown."""
    global _live_client, _live_provider, _sandbox_client, _sandbox_provider

    if _live_client is not None:
        await _live_client.aclose()
    if _sandbox_client is not None:
        await _sandbox_client.aclose()

    _live_client = None
    _live_provider = None
    _sandbox_client = None
    _sandbox_provider = None


# --- Request-time accessor ----------------------------------------------------

def get_payment_provider_for_mode(is_test: bool) -> PaymentProviderAdapter:
    """Return the correct Nomba provider for the current request environment.

    - is_test=True  → sandbox provider (pk_test / sk_test keys)
    - is_test=False → live provider    (pk_live / sk_live keys)

    Raises RuntimeError if providers have not been initialized at startup.
    """
    if is_test:
        if _sandbox_provider is None:
            raise RuntimeError(
                "Sandbox payment provider not initialized. "
                "Ensure init_payment_providers() is called at startup."
            )
        return _sandbox_provider

    if _live_provider is None:
        raise RuntimeError(
            "Live payment provider not initialized. "
            "Ensure init_payment_providers() is called at startup."
        )
    return _live_provider


def get_payment_provider() -> PaymentProviderAdapter:
    """Return the live provider. Kept for backward compatibility.

    Prefer ``get_payment_provider_for_mode(is_test)`` in new code.
    """
    return get_payment_provider_for_mode(is_test=False)
