"""Tests for tenant signup, signin, refresh, and API key management.

Uses an in-memory SQLite DB via the db_session fixture from conftest.
The FastAPI app is driven through httpx.AsyncClient with ASGITransport.

IMPORTANT: TenantAuthMiddleware opens its own DB session via AsyncSessionLocal,
bypassing the FastAPI dependency override. We patch it the same way as
test_subscription_router.py so all DB access goes through the test session.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_async_db
from main import app


# ── Fixtures ──────────────────────────────────────────────────────────────────

class _SessionProxy:
    """Makes the test DB session work as `async with AsyncSessionLocal() as s:`."""
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
    def __call__(self):
        return self  # middleware calls AsyncSessionLocal() then uses as ctx mgr
    async def __aenter__(self) -> AsyncSession:
        return self._s
    async def __aexit__(self, *_) -> None:
        pass


@pytest_asyncio.fixture
async def auth_client(db_session: AsyncSession):
    """Async test client with both the DB dependency and middleware session patched."""
    app.dependency_overrides[get_async_db] = lambda: db_session

    from app.core import middleware as mw
    original = mw.AsyncSessionLocal
    mw.AsyncSessionLocal = _SessionProxy(db_session)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    mw.AsyncSessionLocal = original


# ── Signup ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_signup_returns_tenant_and_tokens(auth_client):
    resp = await auth_client.post(
        "/v1/auth/signup",
        json={"name": "Acme Corp", "email": "acme@example.com", "password": "supersecret"},
    )
    assert resp.status_code == 201
    body = resp.json()

    assert body["tenant"]["email"] == "acme@example.com"
    assert body["tenant"]["name"] == "Acme Corp"

    # Token pair returned immediately
    assert "access_token" in body["tokens"]
    assert "refresh_token" in body["tokens"]
    assert body["tokens"]["token_type"] == "bearer"

    # Keys are NOT in signup response — they are created explicitly later
    assert "keys" not in body


@pytest.mark.asyncio
async def test_signup_duplicate_email_returns_409(auth_client):
    await auth_client.post(
        "/v1/auth/signup",
        json={"name": "Acme", "email": "dup@example.com", "password": "password1"},
    )
    resp = await auth_client.post(
        "/v1/auth/signup",
        json={"name": "Acme 2", "email": "dup@example.com", "password": "password2"},
    )
    assert resp.status_code == 409


# ── Keys are null at signup ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_keys_are_null_after_signup(auth_client):
    """All key slots must be null immediately after signup."""
    signup = await auth_client.post(
        "/v1/auth/signup",
        json={"name": "NullKeys", "email": "nullkeys@example.com", "password": "pass1234"},
    )
    access_token = signup.json()["tokens"]["access_token"]

    resp = await auth_client.get(
        "/v1/auth/keys/new",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200
    keys = resp.json()
    for slot in ("pk_test", "sk_test", "pk_live", "sk_live"):
        assert keys[slot] is None


# ── Signin ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_signin_valid_credentials(auth_client):
    await auth_client.post(
        "/v1/auth/signup",
        json={"name": "Test", "email": "test@example.com", "password": "mypassword"},
    )
    resp = await auth_client.post(
        "/v1/auth/signin",
        json={"email": "test@example.com", "password": "mypassword"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert "refresh_token" in body


@pytest.mark.asyncio
async def test_signin_wrong_password_returns_401(auth_client):
    await auth_client.post(
        "/v1/auth/signup",
        json={"name": "Test", "email": "pw@example.com", "password": "correct"},
    )
    resp = await auth_client.post(
        "/v1/auth/signin",
        json={"email": "pw@example.com", "password": "wrong"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_signin_unknown_email_returns_401(auth_client):
    resp = await auth_client.post(
        "/v1/auth/signin",
        json={"email": "nobody@example.com", "password": "anything"},
    )
    assert resp.status_code == 401


# ── Refresh token ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_issues_new_token_pair(auth_client):
    signup = await auth_client.post(
        "/v1/auth/signup",
        json={"name": "RefreshTest", "email": "refresh@example.com", "password": "pass1234"},
    )
    refresh_token = signup.json()["tokens"]["refresh_token"]

    resp = await auth_client.post("/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert "refresh_token" in body


@pytest.mark.asyncio
async def test_refresh_with_access_token_returns_401(auth_client):
    """Passing an access token to /auth/refresh must be rejected."""
    signup = await auth_client.post(
        "/v1/auth/signup",
        json={"name": "T", "email": "t@example.com", "password": "pass1234"},
    )
    access_token = signup.json()["tokens"]["access_token"]
    resp = await auth_client.post("/v1/auth/refresh", json={"refresh_token": access_token})
    assert resp.status_code == 401


# ── /auth/me ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_me_with_valid_jwt(auth_client):
    signup = await auth_client.post(
        "/v1/auth/signup",
        json={"name": "MeTest", "email": "me@example.com", "password": "pass1234"},
    )
    access_token = signup.json()["tokens"]["access_token"]
    resp = await auth_client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@example.com"


@pytest.mark.asyncio
async def test_get_me_without_token_returns_401(auth_client):
    resp = await auth_client.get("/v1/auth/me")
    assert resp.status_code == 401


# ── POST /auth/keys/create ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_key_generates_value(auth_client):
    signup = await auth_client.post(
        "/v1/auth/signup",
        json={"name": "Create", "email": "create@example.com", "password": "pass1234"},
    )
    access_token = signup.json()["tokens"]["access_token"]

    resp = await auth_client.post(
        "/v1/auth/keys/create",
        json={"key_type": "sk_test"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["key_type"] == "sk_test"
    assert body["value"].startswith("sk_test_")
    assert body["active"] is True


@pytest.mark.asyncio
async def test_create_key_twice_returns_409(auth_client):
    """Creating the same key slot twice must fail with 409."""
    signup = await auth_client.post(
        "/v1/auth/signup",
        json={"name": "Dup", "email": "dupkey@example.com", "password": "pass1234"},
    )
    access_token = signup.json()["tokens"]["access_token"]

    await auth_client.post(
        "/v1/auth/keys/create",
        json={"key_type": "pk_test"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    resp = await auth_client.post(
        "/v1/auth/keys/create",
        json={"key_type": "pk_test"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 409


# ── GET /auth/keys/new (always viewable) ─────────────────────────────────────

@pytest.mark.asyncio
async def test_get_keys_always_viewable(auth_client):
    """Keys can be retrieved at any time — not just once."""
    signup = await auth_client.post(
        "/v1/auth/signup",
        json={"name": "View", "email": "view@example.com", "password": "pass1234"},
    )
    access_token = signup.json()["tokens"]["access_token"]

    # Create sk_test
    create = await auth_client.post(
        "/v1/auth/keys/create",
        json={"key_type": "sk_test"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    created_value = create.json()["value"]

    # Call /auth/keys/new multiple times — same value returned each time
    for _ in range(3):
        resp = await auth_client.get(
            "/v1/auth/keys/new",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["sk_test"] == created_value


# ── API key → middleware resolution ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_valid_api_key_resolves_tenant(auth_client):
    """A created sk_test key should let a request through to a protected endpoint."""
    signup = await auth_client.post(
        "/v1/auth/signup",
        json={"name": "APIKeyTest", "email": "api@example.com", "password": "pass1234"},
    )
    access_token = signup.json()["tokens"]["access_token"]

    create = await auth_client.post(
        "/v1/auth/keys/create",
        json={"key_type": "sk_test"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    sk_test = create.json()["value"]

    resp = await auth_client.get("/v1/customers/all", headers={"X-API-Key": sk_test})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_invalid_api_key_returns_401(auth_client):
    resp = await auth_client.get("/v1/customers/all", headers={"X-API-Key": "sk_test_notreal"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_null_key_slot_cannot_be_used(auth_client):
    """A key slot that was never created must not allow access."""
    # Signup but don't create any keys
    await auth_client.post(
        "/v1/auth/signup",
        json={"name": "NoKey", "email": "nokey@example.com", "password": "pass1234"},
    )
    # Any string that starts with sk_test_ but isn't in the DB must be rejected
    resp = await auth_client.get("/v1/customers/all", headers={"X-API-Key": "sk_test_doesnotexist"})
    assert resp.status_code == 401


# ── Key regeneration ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_regenerate_key_returns_new_value(auth_client):
    signup = await auth_client.post(
        "/v1/auth/signup",
        json={"name": "Regen", "email": "regen@example.com", "password": "pass1234"},
    )
    access_token = signup.json()["tokens"]["access_token"]

    # First create the key
    create = await auth_client.post(
        "/v1/auth/keys/create",
        json={"key_type": "sk_test"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    old_value = create.json()["value"]

    # Regenerate
    resp = await auth_client.post(
        "/v1/auth/keys/regenerate",
        json={"key_type": "sk_test"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["key_type"] == "sk_test"
    assert body["value"].startswith("sk_test_")
    assert body["value"] != old_value


@pytest.mark.asyncio
async def test_old_key_invalid_after_regeneration(auth_client):
    signup = await auth_client.post(
        "/v1/auth/signup",
        json={"name": "OldKey", "email": "oldkey@example.com", "password": "pass1234"},
    )
    access_token = signup.json()["tokens"]["access_token"]

    create = await auth_client.post(
        "/v1/auth/keys/create",
        json={"key_type": "sk_test"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    old_key = create.json()["value"]

    # Regenerate
    await auth_client.post(
        "/v1/auth/keys/regenerate",
        json={"key_type": "sk_test"},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    # Old key must be rejected
    resp = await auth_client.get("/v1/customers/all", headers={"X-API-Key": old_key})
    assert resp.status_code == 401


# ── Key revocation ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_revoke_key_blocks_requests(auth_client):
    signup = await auth_client.post(
        "/v1/auth/signup",
        json={"name": "Revoke", "email": "revoke@example.com", "password": "pass1234"},
    )
    access_token = signup.json()["tokens"]["access_token"]

    create = await auth_client.post(
        "/v1/auth/keys/create",
        json={"key_type": "pk_test"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    pk_test = create.json()["value"]

    # Key works before revocation
    pre = await auth_client.get("/v1/customers/all", headers={"X-API-Key": pk_test})
    assert pre.status_code == 200

    # Revoke
    rev = await auth_client.post(
        "/v1/auth/keys/revoke",
        json={"key_type": "pk_test"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert rev.status_code == 200
    assert rev.json()["revoked"] is True

    # Key must now be rejected
    post = await auth_client.get("/v1/customers/all", headers={"X-API-Key": pk_test})
    assert post.status_code == 401


@pytest.mark.asyncio
async def test_keys_still_viewable_after_revocation(auth_client):
    """Revoked key value stays visible in /auth/keys/new for reference."""
    signup = await auth_client.post(
        "/v1/auth/signup",
        json={"name": "RevokeView", "email": "revokeview@example.com", "password": "pass1234"},
    )
    access_token = signup.json()["tokens"]["access_token"]

    create = await auth_client.post(
        "/v1/auth/keys/create",
        json={"key_type": "sk_live"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    sk_live_value = create.json()["value"]

    await auth_client.post(
        "/v1/auth/keys/revoke",
        json={"key_type": "sk_live"},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    keys = await auth_client.get(
        "/v1/auth/keys/new",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert keys.json()["sk_live"] == sk_live_value  # value retained
    assert keys.json()["sk_live_active"] is False    # but flagged as inactive
