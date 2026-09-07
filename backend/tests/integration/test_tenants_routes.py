"""Integration tests for tenant CRUD (superadmin-only) and admin-initiated user
creation, over ASGI, against a real Postgres with migrations applied.

Reuses the harness from test_auth_routes.py / test_users_routes.py.
"""

import uuid
from collections.abc import AsyncGenerator

import asyncpg
import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.test_auth_routes import _dsn, _run_migrations


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("timescale/timescaledb:latest-pg16") as pg:
        yield pg


@pytest.fixture(scope="module")
def migrated_db(pg_container: PostgresContainer):
    import os

    env = os.environ.copy()
    env.update(
        POSTGRES_HOST=pg_container.get_container_host_ip(),
        POSTGRES_PORT=str(pg_container.get_exposed_port(5432)),
        POSTGRES_DB=pg_container.dbname,
        POSTGRES_USER=pg_container.username,
        POSTGRES_PASSWORD=pg_container.password,
    )
    _run_migrations(env)
    return pg_container


@pytest.fixture
async def tenant_id(migrated_db: PostgresContainer) -> AsyncGenerator[uuid.UUID, None]:
    conn = await asyncpg.connect(_dsn(migrated_db, driver="postgresql"))
    try:
        tid = uuid.uuid4()
        await conn.execute(
            "INSERT INTO tenants (id, name, plan_tier) VALUES ($1, $2, $3)",
            tid,
            "Test Tenant",
            "standard",
        )
        yield tid
    finally:
        await conn.execute("DELETE FROM users WHERE tenant_id = $1", tid)
        await conn.execute("DELETE FROM tenants WHERE id = $1", tid)
        await conn.close()


@pytest.fixture
async def client(
    migrated_db: PostgresContainer, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[httpx.AsyncClient, None]:
    from app.core.config import settings
    from app.core.db import get_session
    from app.main import app

    monkeypatch.setattr(settings, "cookie_secure", False)

    engine = create_async_engine(_dsn(migrated_db, driver="postgresql+asyncpg"), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_session() -> AsyncGenerator:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _get_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


def _access_token(*, tenant_id: uuid.UUID, role: str) -> str:
    from app.core.security import create_access_token

    return create_access_token(user_id=uuid.uuid4(), tenant_id=tenant_id, role=role)


# --- RBAC: only superadmin may touch tenant CRUD ---------------------------------


@pytest.mark.parametrize("role", ["tenant_admin", "facility_manager", "technician", "viewer"])
async def test_non_superadmin_cannot_create_tenant(
    client: httpx.AsyncClient, tenant_id: uuid.UUID, role: str
) -> None:
    token = _access_token(tenant_id=tenant_id, role=role)

    resp = await client.post(
        "/tenants",
        json={"name": "Sneaky Co", "plan_tier": "standard"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403


@pytest.mark.parametrize("role", ["tenant_admin", "facility_manager", "technician", "viewer"])
async def test_non_superadmin_cannot_list_tenants(
    client: httpx.AsyncClient, tenant_id: uuid.UUID, role: str
) -> None:
    token = _access_token(tenant_id=tenant_id, role=role)

    resp = await client.get("/tenants", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 403


async def test_non_superadmin_cannot_update_tenant(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> None:
    token = _access_token(tenant_id=tenant_id, role="tenant_admin")

    resp = await client.patch(
        f"/tenants/{tenant_id}",
        json={"name": "Renamed"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403


async def test_non_superadmin_cannot_create_user_in_arbitrary_tenant(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> None:
    token = _access_token(tenant_id=tenant_id, role="tenant_admin")
    other_tenant = uuid.uuid4()

    resp = await client.post(
        f"/tenants/{other_tenant}/users",
        json={"email": "sneaky@example.com", "password": "correct-horse-1", "role": "viewer"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403


# --- Happy path: superadmin tenant CRUD -------------------------------------------


async def test_superadmin_can_create_list_and_update_a_tenant(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> None:
    token = _access_token(tenant_id=tenant_id, role="superadmin")

    create_resp = await client.post(
        "/tenants",
        json={"name": "New Co", "plan_tier": "standard"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create_resp.status_code == 201
    new_tenant_id = create_resp.json()["id"]

    list_resp = await client.get("/tenants", headers={"Authorization": f"Bearer {token}"})
    assert list_resp.status_code == 200
    assert new_tenant_id in {t["id"] for t in list_resp.json()}

    update_resp = await client.patch(
        f"/tenants/{new_tenant_id}",
        json={"plan_tier": "enterprise"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["plan_tier"] == "enterprise"
    assert update_resp.json()["name"] == "New Co"


async def test_update_nonexistent_tenant_is_404(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> None:
    token = _access_token(tenant_id=tenant_id, role="superadmin")

    resp = await client.patch(
        f"/tenants/{uuid.uuid4()}",
        json={"name": "Ghost"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404


# --- Admin-initiated user creation + privilege escalation guard ------------------


async def test_tenant_admin_can_create_a_user_in_their_own_tenant(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> None:
    token = _access_token(tenant_id=tenant_id, role="tenant_admin")

    resp = await client.post(
        "/users",
        json={"email": "new-tech@example.com", "password": "correct-horse-1", "role": "technician"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["tenant_id"] == str(tenant_id)
    assert body["role"] == "technician"


async def test_tenant_admin_cannot_grant_superadmin_role(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> None:
    token = _access_token(tenant_id=tenant_id, role="tenant_admin")

    resp = await client.post(
        "/users",
        json={"email": "escalate@example.com", "password": "correct-horse-1", "role": "superadmin"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403


async def test_superadmin_can_grant_superadmin_role(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> None:
    token = _access_token(tenant_id=tenant_id, role="superadmin")

    resp = await client.post(
        "/users",
        json={
            "email": "new-super@example.com",
            "password": "correct-horse-1",
            "role": "superadmin",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 201
    assert resp.json()["role"] == "superadmin"


async def test_superadmin_can_bootstrap_a_user_in_a_brand_new_tenant(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> None:
    token = _access_token(tenant_id=tenant_id, role="superadmin")

    create_resp = await client.post(
        "/tenants",
        json={"name": "Bootstrap Co", "plan_tier": "standard"},
        headers={"Authorization": f"Bearer {token}"},
    )
    new_tenant_id = create_resp.json()["id"]

    user_resp = await client.post(
        f"/tenants/{new_tenant_id}/users",
        json={
            "email": "first-admin@example.com",
            "password": "correct-horse-1",
            "role": "tenant_admin",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert user_resp.status_code == 201
    assert user_resp.json()["tenant_id"] == new_tenant_id
