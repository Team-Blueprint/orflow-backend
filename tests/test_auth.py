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

from unittest.mock import MagicMock, patch, AsyncMock

from app.db.database import get_async_db
from app.tenants.service import TenantService
from main import app


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_token(client: AsyncClient, name: str) -> str:
    """Extract a cookie value from the client's cookie jar."""
    val = client.cookies.get(name)
    if val is None:
        raise AssertionError(f"Cookie '{name}' not found in client cookie jar")
    return val


async def _create_project(client: AsyncClient, access_token: str) -> str:
    """Create a test project and return its ID."""
    resp = await client.post(
        "/v1/projects/create",
        json={"name": "Test Project"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code in (200, 201), f"Project creation failed: {resp.text}"
    return resp.json()["id"]


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

    from app.core.config import settings
    original_secure = settings.COOKIE_SECURE
    original_samesite = settings.COOKIE_SAMESITE
    settings.COOKIE_SECURE = False
    settings.COOKIE_SAMESITE = "lax"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    settings.COOKIE_SECURE = original_secure
    settings.COOKIE_SAMESITE = original_samesite
    app.dependency_overrides.clear()
    mw.AsyncSessionLocal = original


# ── Signup ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_signup_returns_tenant_and_sets_cookies(auth_client):
    resp = await auth_client.post(
        "/v1/auth/signup",
        json={"name": "Acme Corp", "email": "acme@example.com", "password": "supersecret"},
    )
    assert resp.status_code == 201
    body = resp.json()

    assert body["tenant"]["email"] == "acme@example.com"
    assert body["tenant"]["name"] == "Acme Corp"

    # Keys are NOT in signup response — they are created explicitly later
    assert "keys" not in body

    # Access token returned in body for Swagger/Postman convenience
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "bearer"

    # Tokens ALSO set as cookies
    assert _get_token(auth_client, "access_token") is not None
    assert _get_token(auth_client, "refresh_token") is not None
    assert _get_token(auth_client, "csrf_token") is not None


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
    await auth_client.post(
        "/v1/auth/signup",
        json={"name": "NullKeys", "email": "nullkeys@example.com", "password": "pass1234"},
    )
    access_token = _get_token(auth_client, "access_token")

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
    assert "tenant" in body
    assert body["tenant"]["email"] == "test@example.com"

    # Access token returned in body for Swagger/Postman convenience
    assert "access_token" in body
    assert "refresh_token" in body

    # Tokens ALSO set as cookies
    assert _get_token(auth_client, "access_token") is not None
    assert _get_token(auth_client, "refresh_token") is not None


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
    await auth_client.post(
        "/v1/auth/signup",
        json={"name": "RefreshTest", "email": "refresh@example.com", "password": "pass1234"},
    )

    # refresh_token comes from the cookie set during signup
    resp = await auth_client.post("/v1/auth/refresh")
    assert resp.status_code == 200
    assert resp.json()["message"] == "Tokens refreshed"

    # New tokens set as cookies
    assert _get_token(auth_client, "access_token") is not None
    assert _get_token(auth_client, "refresh_token") is not None


@pytest.mark.asyncio
async def test_refresh_with_access_token_returns_401(auth_client):
    """Passing an access token as refresh token must be rejected."""
    await auth_client.post(
        "/v1/auth/signup",
        json={"name": "T", "email": "t@example.com", "password": "pass1234"},
    )
    access_token = _get_token(auth_client, "access_token")

    # Use a fresh client to avoid cookie jar conflicts
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as standalone:
        standalone.cookies.set("refresh_token", access_token)
        resp = await standalone.post("/v1/auth/refresh")
    assert resp.status_code == 401


# ── /auth/me ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_me_with_valid_jwt(auth_client):
    await auth_client.post(
        "/v1/auth/signup",
        json={"name": "MeTest", "email": "me@example.com", "password": "pass1234"},
    )
    access_token = _get_token(auth_client, "access_token")
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


# ── Logout ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_logout_clears_cookies(auth_client):
    await auth_client.post(
        "/v1/auth/signup",
        json={"name": "LogoutTest", "email": "logout@example.com", "password": "pass1234"},
    )
    assert _get_token(auth_client, "access_token") is not None

    resp = await auth_client.post(
        "/v1/auth/logout",
        headers={"X-CSRF-Token": _get_token(auth_client, "csrf_token")},
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == "Logged out"


# ── POST /auth/keys/create ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_key_generates_value(auth_client):
    await auth_client.post(
        "/v1/auth/signup",
        json={"name": "Create", "email": "create@example.com", "password": "pass1234"},
    )
    access_token = _get_token(auth_client, "access_token")

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
    await auth_client.post(
        "/v1/auth/signup",
        json={"name": "Dup", "email": "dupkey@example.com", "password": "pass1234"},
    )
    access_token = _get_token(auth_client, "access_token")

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
    await auth_client.post(
        "/v1/auth/signup",
        json={"name": "View", "email": "view@example.com", "password": "pass1234"},
    )
    access_token = _get_token(auth_client, "access_token")

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
    await auth_client.post(
        "/v1/auth/signup",
        json={"name": "APIKeyTest", "email": "api@example.com", "password": "pass1234"},
    )
    access_token = _get_token(auth_client, "access_token")

    project_id = await _create_project(auth_client, access_token)

    create = await auth_client.post(
        "/v1/auth/keys/create",
        json={"key_type": "sk_test"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    sk_test = create.json()["value"]

    resp = await auth_client.get(
        "/v1/customers/all",
        headers={"X-API-Key": sk_test, "X-Project-ID": project_id},
    )
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
    await auth_client.post(
        "/v1/auth/signup",
        json={"name": "Regen", "email": "regen@example.com", "password": "pass1234"},
    )
    access_token = _get_token(auth_client, "access_token")

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
    await auth_client.post(
        "/v1/auth/signup",
        json={"name": "OldKey", "email": "oldkey@example.com", "password": "pass1234"},
    )
    access_token = _get_token(auth_client, "access_token")

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
    await auth_client.post(
        "/v1/auth/signup",
        json={"name": "Revoke", "email": "revoke@example.com", "password": "pass1234"},
    )
    access_token = _get_token(auth_client, "access_token")

    project_id = await _create_project(auth_client, access_token)

    create = await auth_client.post(
        "/v1/auth/keys/create",
        json={"key_type": "pk_test"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    pk_test = create.json()["value"]

    # Key works before revocation
    pre = await auth_client.get(
        "/v1/customers/all",
        headers={"X-API-Key": pk_test, "X-Project-ID": project_id},
    )
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
    await auth_client.post(
        "/v1/auth/signup",
        json={"name": "RevokeView", "email": "revokeview@example.com", "password": "pass1234"},
    )
    access_token = _get_token(auth_client, "access_token")

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


# ── Google OAuth (service layer) ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_google_auth_creates_new_tenant(db_session: AsyncSession):
    """google_auth creates a new tenant when no match exists."""
    service = TenantService(db_session)
    tenant, tokens = await service.google_auth(
        google_sub="google123",
        email="new@example.com",
        name="New Google User",
    )
    assert tenant.email == "new@example.com"
    assert tenant.name == "New Google User"
    assert tenant.google_sub == "google123"
    assert tenant.is_active is True
    assert "access_token" in tokens
    assert "refresh_token" in tokens


@pytest.mark.asyncio
async def test_google_auth_links_existing_email(db_session: AsyncSession):
    """google_auth links an existing tenant by email if google_sub is new."""
    service = TenantService(db_session)
    existing, _ = await service.signup(
        name="Existing", email="existing@example.com", password="password123"
    )
    assert existing.google_sub is None

    tenant, tokens = await service.google_auth(
        google_sub="google456",
        email="existing@example.com",
        name="Existing Linked",
    )
    assert tenant.id == existing.id
    assert tenant.google_sub == "google456"
    assert "access_token" in tokens


@pytest.mark.asyncio
async def test_google_auth_reuses_google_sub(db_session: AsyncSession):
    """google_auth returns the same tenant when google_sub already exists."""
    service = TenantService(db_session)
    tenant1, _ = await service.google_auth(
        google_sub="google789",
        email="first@example.com",
        name="First",
    )
    tenant2, tokens = await service.google_auth(
        google_sub="google789",
        email="different@example.com",
        name="Second",
    )
    assert tenant2.id == tenant1.id
    assert tenant2.email == "first@example.com"  # original email preserved
    assert "access_token" in tokens


@pytest.mark.asyncio
async def test_google_auth_raises_for_inactive_tenant(db_session: AsyncSession):
    """google_auth raises ValueError for inactive tenants."""
    service = TenantService(db_session)
    tenant, _ = await service.signup(
        name="Inactive", email="inactive@example.com", password="password123"
    )
    tenant.is_active = False
    await db_session.commit()

    with pytest.raises(ValueError, match="inactive"):
        await service.google_auth(
            google_sub="google_inactive",
            email="inactive@example.com",
            name="Inactive",
        )


# ── Google OAuth (endpoints) ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_google_login_redirect(auth_client):
    """GET /auth/google/login returns a 307 redirect to Google."""
    from app.core.config import settings
    # Ensure settings are configured for the test
    original_id = settings.GOOGLE_CLIENT_ID
    settings.GOOGLE_CLIENT_ID = "test-client-id"
    try:
        resp = await auth_client.get("/v1/auth/google/login", follow_redirects=False)
        assert resp.status_code == 307
        location = resp.headers.get("location", "")
        assert location.startswith("https://accounts.google.com/o/oauth2/v2/auth")
        assert "client_id=test-client-id" in location
        assert "response_type=code" in location
        # State cookie should be set
        assert auth_client.cookies.get("google_oauth_state") is not None
    finally:
        settings.GOOGLE_CLIENT_ID = original_id


@pytest.mark.asyncio
async def test_google_callback_missing_code_returns_400(auth_client):
    """GET /auth/google/callback without code returns 400."""
    # Set a matching state cookie so we pass the CSRF check
    auth_client.cookies.set("google_oauth_state", "valid_state", path="/v1/auth/google/callback")
    resp = await auth_client.get("/v1/auth/google/callback?state=valid_state")
    assert resp.status_code == 400
    assert "code" in resp.text.lower()


@pytest.mark.asyncio
async def test_google_callback_invalid_state_returns_403(auth_client):
    """GET /auth/google/callback with mismatched state returns 403."""
    resp = await auth_client.get(
        "/v1/auth/google/callback?code=testcode&state=bogus"
    )
    assert resp.status_code == 403
    assert "state" in resp.text.lower()


@pytest.mark.asyncio
async def test_google_callback_success(db_session: AsyncSession, auth_client):
    """Full callback flow with mocked authlib succeeds and sets cookies."""
    from app.core.config import settings

    original_id = settings.GOOGLE_CLIENT_ID
    original_secret = settings.GOOGLE_CLIENT_SECRET
    settings.GOOGLE_CLIENT_ID = "test-client-id"
    settings.GOOGLE_CLIENT_SECRET = "test-client-secret"

    try:
        login_resp = await auth_client.get(
            "/v1/auth/google/login", follow_redirects=False
        )
        assert login_resp.status_code == 307

        state = auth_client.cookies.get("google_oauth_state")
        assert state is not None

        mock_userinfo = MagicMock()
        mock_userinfo.is_success = True
        mock_userinfo.json.return_value = {
            "sub": "google_test_sub_1",
            "email": "oauth_test@example.com",
            "name": "OAuth Test User",
        }

        with patch("app.tenants.google_service._oauth_client") as mock_oauth_fac:
            mock_client = MagicMock()
            mock_oauth_fac.return_value = mock_client
            mock_client.fetch_token.return_value = {
                "access_token": "fake_access_token",
            }

            with patch("app.tenants.google_service.httpx.AsyncClient") as mock_httpx:
                mock_instance = AsyncMock()
                mock_httpx.return_value.__aenter__.return_value = mock_instance
                mock_instance.get = AsyncMock(return_value=mock_userinfo)

                resp = await auth_client.get(
                    f"/v1/auth/google/callback?code=fakecode&state={state}",
                    follow_redirects=False,
                )

        assert resp.status_code == 307
        assert resp.headers["location"] == "http://localhost:5173/auth/google/callback"
        assert resp.cookies.get("access_token") is not None
        assert resp.cookies.get("refresh_token") is not None
    finally:
        settings.GOOGLE_CLIENT_ID = original_id
        settings.GOOGLE_CLIENT_SECRET = original_secret
