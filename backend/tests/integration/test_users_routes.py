"""Integration tests for GET /users, the RBAC working example.

Reuses the real-Postgres/real-migrations harness from test_auth_routes.py so RLS
policies and the RBAC dependency are both exercised together, over ASGI.
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


async def test_list_users_rejects_a_viewer(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> None:
    token = _access_token(tenant_id=tenant_id, role="viewer")

    resp = await client.get("/users", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 403


async def test_list_users_rejects_a_technician(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> None:
    token = _access_token(tenant_id=tenant_id, role="technician")

    resp = await client.get("/users", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 403


async def test_list_users_allows_a_tenant_admin_and_scopes_to_their_tenant(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> None:
    await client.post(
        "/register",
        json={
            "tenant_id": str(tenant_id),
            "email": "grace@example.com",
            "password": "correct-horse-1",
        },
    )
    token = _access_token(tenant_id=tenant_id, role="tenant_admin")

    resp = await client.get("/users", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["email"] == "grace@example.com"
    assert body[0]["tenant_id"] == str(tenant_id)


async def test_list_users_allows_a_superadmin(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> None:
    token = _access_token(tenant_id=tenant_id, role="superadmin")

    resp = await client.get("/users", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200


async def test_list_users_without_token_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.get("/users")
    assert resp.status_code == 401
