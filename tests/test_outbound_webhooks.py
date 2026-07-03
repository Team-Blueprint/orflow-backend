import pytest
from httpx import AsyncClient
from uuid import UUID

from httpx._transports.asgi import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession
import pytest_asyncio

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
    transport = ASGITransport(app=app)
    from app.core.config import settings
    original_secure = settings.COOKIE_SECURE
    original_samesite = settings.COOKIE_SAMESITE
    settings.COOKIE_SECURE = False
    settings.COOKIE_SAMESITE = "lax"

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    settings.COOKIE_SECURE = original_secure
    settings.COOKIE_SAMESITE = original_samesite
    app.dependency_overrides.clear()
    mw.AsyncSessionLocal = original

async def _signup(client: AsyncClient, email: str):
    signup = await client.post(
        "/v1/auth/signup",
        json={"name": "Test", "email": email, "password": "pass1234"},
    )
    signup.raise_for_status()
    return client.cookies.get("access_token")

async def _signup_and_create_project(client: AsyncClient, email: str):
    """Sign up and create a project, returning access token and project ID."""
    token = await _signup(client, email)
    proj_resp = await client.post(
        "/v1/projects/create",
        json={"name": "Test Project"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert proj_resp.status_code in (200, 201)
    return token, proj_resp.json()["id"]

@pytest.fixture
async def auth_sk_headers(auth_client: AsyncClient):
    """Sign up, create project and an sk_test key to use for endpoint management."""
    token, project_id = await _signup_and_create_project(auth_client, 'sk@test.com')
    resp = await auth_client.post(
        "/v1/auth/keys/create",
        json={"key_type": "sk_test"},
        headers={"Authorization": f"Bearer {token}"}
    )
    sk_test = resp.json()["value"]
    return {"X-API-Key": sk_test, "X-Project-ID": project_id}

@pytest.fixture
async def auth_pk_headers(auth_client: AsyncClient):
    """Sign up, create project and a pk_test key."""
    token, project_id = await _signup_and_create_project(auth_client, 'pk@test.com')
    resp = await auth_client.post(
        "/v1/auth/keys/create",
        json={"key_type": "pk_test"},
        headers={"Authorization": f"Bearer {token}"}
    )
    pk_test = resp.json()["value"]
    return {"X-API-Key": pk_test, "X-Project-ID": project_id}

@pytest.mark.asyncio
async def test_create_endpoint_requires_sk_key(auth_client: AsyncClient, auth_pk_headers: dict):
    resp = await auth_client.post(
        "/v1/webhooks/endpoints/add",
        json={"url": "https://example.com/webhook"},
        headers=auth_pk_headers
    )
    assert resp.status_code == 403
    assert "sk_*" in resp.json()["error"]["message"]

@pytest.mark.asyncio
async def test_create_and_list_endpoints(auth_client: AsyncClient, auth_sk_headers: dict):
    # Create
    create_resp = await auth_client.post(
        "/v1/webhooks/endpoints/add",
        json={"url": "https://example.com/webhook", "description": "Primary"},
        headers=auth_sk_headers
    )
    assert create_resp.status_code == 201
    data = create_resp.json()
    assert data["url"] == "https://example.com/webhook"
    assert data["description"] == "Primary"
    assert "secret" in data
    endpoint_id = data["id"]
    
    # List
    list_resp = await auth_client.get("/v1/webhooks/endpoints/all", headers=auth_sk_headers)
    assert list_resp.status_code == 200
    endpoints = list_resp.json()
    assert len(endpoints) >= 1
    assert any(e["id"] == endpoint_id for e in endpoints)
    
@pytest.mark.asyncio
async def test_delete_endpoint(auth_client: AsyncClient, auth_sk_headers: dict):
    create_resp = await auth_client.post(
        "/v1/webhooks/endpoints/add",
        json={"url": "https://example.com/webhook2"},
        headers=auth_sk_headers
    )
    endpoint_id = create_resp.json()["id"]
    
    # Delete
    del_resp = await auth_client.delete(f"/v1/webhooks/endpoints/{endpoint_id}", headers=auth_sk_headers)
    assert del_resp.status_code == 204
    
    # Verify deleted
    list_resp = await auth_client.get("/v1/webhooks/endpoints/all", headers=auth_sk_headers)
    endpoints = list_resp.json()
    assert not any(e["id"] == endpoint_id for e in endpoints)
