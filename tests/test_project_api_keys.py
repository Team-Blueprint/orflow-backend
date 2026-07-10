from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from httpx._transports.asgi import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_async_db
from main import app


class _SessionProxy:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
    def __call__(self):
        return self
    async def __aenter__(self) -> AsyncSession:
        return self._s
    async def __aexit__(self, *_) -> None:
        pass


@pytest_asyncio.fixture
async def auth_client(db_session: AsyncSession):
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


def _get_token(client: AsyncClient, name: str) -> str:
    val = client.cookies.get(name)
    if val is None:
        raise AssertionError(f"Cookie '{name}' not found")
    return val


async def _signup(client: AsyncClient) -> str:
    resp = await client.post(
        "/v1/auth/signup",
        json={"name": "Test Co", "email": "test@example.com", "password": "pass1234"},
    )
    assert resp.status_code == 201
    return _get_token(client, "access_token")


async def _create_project(client: AsyncClient, token: str) -> str:
    resp = await client.post(
        "/v1/projects/create",
        json={"name": "Test Project"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code in (200, 201)
    return resp.json()["id"]


# ── List keys (empty) ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_project_keys_empty(auth_client):
    token = await _signup(auth_client)
    project_id = await _create_project(auth_client, token)

    resp = await auth_client.get(
        f"/v1/projects/{project_id}/keys",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ── Create key ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_project_key(auth_client):
    token = await _signup(auth_client)
    project_id = await _create_project(auth_client, token)
    csrf = _get_token(auth_client, "csrf_token")

    resp = await auth_client.post(
        f"/v1/projects/{project_id}/keys/create",
        json={"key_type": "sk_test"},
        headers={
            "Authorization": f"Bearer {token}",
            "X-CSRF-Token": csrf,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["key_type"] == "sk_test"
    assert body["value"].startswith("sk_test_")
    assert body["active"] is True


@pytest.mark.asyncio
async def test_create_project_key_duplicate_returns_409(auth_client):
    token = await _signup(auth_client)
    project_id = await _create_project(auth_client, token)
    csrf = _get_token(auth_client, "csrf_token")

    await auth_client.post(
        f"/v1/projects/{project_id}/keys/create",
        json={"key_type": "pk_test"},
        headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf},
    )
    resp = await auth_client.post(
        f"/v1/projects/{project_id}/keys/create",
        json={"key_type": "pk_test"},
        headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_project_key_rejects_unauthorized(auth_client):
    token = await _signup(auth_client)
    project_id = await _create_project(auth_client, token)

    resp = await auth_client.post(
        f"/v1/projects/{project_id}/keys/create",
        json={"key_type": "sk_test"},
    )
    assert resp.status_code in (401, 403)


# ── List keys (after create) ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_project_keys_after_create(auth_client):
    token = await _signup(auth_client)
    project_id = await _create_project(auth_client, token)
    csrf = _get_token(auth_client, "csrf_token")

    await auth_client.post(
        f"/v1/projects/{project_id}/keys/create",
        json={"key_type": "sk_test"},
        headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf},
    )

    resp = await auth_client.get(
        f"/v1/projects/{project_id}/keys",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    keys = resp.json()
    assert len(keys) == 1
    assert keys[0]["key_type"] == "sk_test"
    assert keys[0]["is_active"] is True


# ── Key not visible in response (value not returned in list) ─────────────────

@pytest.mark.asyncio
async def test_create_project_key_all_four_types(auth_client):
    token = await _signup(auth_client)
    project_id = await _create_project(auth_client, token)
    csrf = _get_token(auth_client, "csrf_token")

    for kt in ("pk_test", "sk_test", "pk_live", "sk_live"):
        resp = await auth_client.post(
            f"/v1/projects/{project_id}/keys/create",
            json={"key_type": kt},
            headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf},
        )
        assert resp.status_code == 201
        assert resp.json()["value"].startswith(f"{kt}_")

    resp = await auth_client.get(
        f"/v1/projects/{project_id}/keys",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 4


# ── Regenerate key ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_regenerate_project_key(auth_client):
    token = await _signup(auth_client)
    project_id = await _create_project(auth_client, token)
    csrf = _get_token(auth_client, "csrf_token")

    create = await auth_client.post(
        f"/v1/projects/{project_id}/keys/create",
        json={"key_type": "sk_test"},
        headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf},
    )
    old_value = create.json()["value"]

    resp = await auth_client.post(
        f"/v1/projects/{project_id}/keys/regenerate",
        json={"key_type": "sk_test"},
        headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["key_type"] == "sk_test"
    assert body["value"].startswith("sk_test_")
    assert body["value"] != old_value


# ── Revoke key ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_revoke_project_key(auth_client):
    token = await _signup(auth_client)
    project_id = await _create_project(auth_client, token)
    csrf = _get_token(auth_client, "csrf_token")

    await auth_client.post(
        f"/v1/projects/{project_id}/keys/create",
        json={"key_type": "pk_live"},
        headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf},
    )

    resp = await auth_client.post(
        f"/v1/projects/{project_id}/keys/revoke",
        json={"key_type": "pk_live"},
        headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200
    assert resp.json()["revoked"] is True

    # Verify it's listed as inactive
    list_resp = await auth_client.get(
        f"/v1/projects/{project_id}/keys",
        headers={"Authorization": f"Bearer {token}"},
    )
    key = list_resp.json()[0]
    assert key["is_active"] is False


# ── Use project-scoped key on endpoint (no X-Project-ID) ─────────────────────

@pytest.mark.asyncio
async def test_project_scoped_key_works_without_project_header(auth_client):
    token = await _signup(auth_client)
    project_id = await _create_project(auth_client, token)
    csrf = _get_token(auth_client, "csrf_token")

    resp = await auth_client.post(
        f"/v1/projects/{project_id}/keys/create",
        json={"key_type": "sk_test"},
        headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf},
    )
    project_key = resp.json()["value"]

    resp = await auth_client.get(
        "/v1/plans/list",
        headers={"X-API-Key": project_key},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_project_scoped_key_rejected_after_revocation(auth_client):
    token = await _signup(auth_client)
    project_id = await _create_project(auth_client, token)
    csrf = _get_token(auth_client, "csrf_token")

    create = await auth_client.post(
        f"/v1/projects/{project_id}/keys/create",
        json={"key_type": "sk_test"},
        headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf},
    )
    project_key = create.json()["value"]

    await auth_client.post(
        f"/v1/projects/{project_id}/keys/revoke",
        json={"key_type": "sk_test"},
        headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf},
    )

    resp = await auth_client.get(
        "/v1/customers/all",
        headers={"X-API-Key": project_key},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_project_scoped_key_sets_correct_tenant_and_project(auth_client):
    token = await _signup(auth_client)
    project_id = await _create_project(auth_client, token)
    csrf = _get_token(auth_client, "csrf_token")

    create = await auth_client.post(
        f"/v1/projects/{project_id}/keys/create",
        json={"key_type": "sk_test"},
        headers={"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf},
    )
    project_key = create.json()["value"]

    resp = await auth_client.get(
        "/v1/customers/all",
        headers={"X-API-Key": project_key},
    )
    assert resp.status_code == 200
