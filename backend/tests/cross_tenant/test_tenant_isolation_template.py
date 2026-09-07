"""HTTP-level cross-tenant isolation template (PHASE_PLAN.md Phase 1 session 3).

This is the pattern future protected routes should copy: exercise the real FastAPI app
over ASGI, with its DB session pointed at the real `app_role` (not a superuser, not the
service layer directly) so the assertions below are only true if the whole chain works —
JWT -> `get_tenant_context` -> `scope_session_to_tenant` -> RLS policy.

`GET /me` is the only tenant-scoped route that exists yet (task 1.5 adds resource CRUD,
task 1.4 adds RBAC-gated routes) — it's just the vehicle for testing the dependency
chain itself.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

BACKEND_DIR = Path(__file__).resolve().parents[2]
APP_ROLE_PASSWORD = "test-app-role-password"


def _run_migrations(env: dict) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _dsn(pg: PostgresContainer, *, user: str, password: str, driver: str) -> str:
    return (
        f"{driver}://{user}:{password}@{pg.get_container_host_ip()}:"
        f"{pg.get_exposed_port(5432)}/{pg.dbname}"
    )


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="module")
def migrated_db(pg_container: PostgresContainer):
    env = os.environ.copy()
    env.update(
        POSTGRES_HOST=pg_container.get_container_host_ip(),
        POSTGRES_PORT=str(pg_container.get_exposed_port(5432)),
        POSTGRES_DB=pg_container.dbname,
        POSTGRES_USER=pg_container.username,
        POSTGRES_PASSWORD=pg_container.password,
        APP_DB_PASSWORD=APP_ROLE_PASSWORD,
    )
    _run_migrations(env)
    return pg_container


@pytest.fixture
async def client(
    migrated_db: PostgresContainer, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[httpx.AsyncClient, None]:
    from app.core.config import settings
    from app.core.db import get_session
    from app.main import app

    monkeypatch.setattr(settings, "cookie_secure", False)

    # Connect as app_role, exactly like app/core/db.py does at runtime — not the
    # migration/admin superuser other integration tests use for convenience.
    engine = create_async_engine(
        _dsn(migrated_db, user="app_role", password=APP_ROLE_PASSWORD, driver="postgresql+asyncpg"),
        pool_pre_ping=True,
    )
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


async def _make_tenant(migrated_db: PostgresContainer, name: str) -> uuid.UUID:
    conn = await asyncpg.connect(
        _dsn(
            migrated_db,
            user=migrated_db.username,
            password=migrated_db.password,
            driver="postgresql",
        )
    )
    try:
        tid = uuid.uuid4()
        await conn.execute(
            "INSERT INTO tenants (id, name, plan_tier) VALUES ($1, $2, $3)", tid, name, "standard"
        )
        return tid
    finally:
        await conn.close()


async def _register_and_login(
    client: httpx.AsyncClient, *, tenant_id: uuid.UUID, email: str
) -> str:
    await client.post(
        "/register",
        json={"tenant_id": str(tenant_id), "email": email, "password": "correct-horse-1"},
    )
    resp = await client.post(
        "/login",
        json={"tenant_id": str(tenant_id), "email": email, "password": "correct-horse-1"},
    )
    assert resp.status_code == 200, resp.text
    access_token: str = resp.json()["access_token"]
    return access_token


@pytest.fixture
async def tenant_a(migrated_db: PostgresContainer) -> AsyncGenerator[uuid.UUID, None]:
    tid = await _make_tenant(migrated_db, "Tenant A")
    yield tid


@pytest.fixture
async def tenant_b(migrated_db: PostgresContainer) -> AsyncGenerator[uuid.UUID, None]:
    tid = await _make_tenant(migrated_db, "Tenant B")
    yield tid


async def test_me_returns_only_the_callers_own_tenant_scoped_record(
    client: httpx.AsyncClient, tenant_a: uuid.UUID
) -> None:
    token = await _register_and_login(client, tenant_id=tenant_a, email="alice@example.com")

    resp = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert body["tenant_id"] == str(tenant_a)


async def test_concurrent_requests_from_different_tenants_do_not_leak_via_pooled_connection(
    client: httpx.AsyncClient, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    token_a = await _register_and_login(client, tenant_id=tenant_a, email="a@example.com")
    token_b = await _register_and_login(client, tenant_id=tenant_b, email="b@example.com")

    async def _get_me(token: str) -> dict:
        resp = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        result: dict = resp.json()
        return result

    # Interleave many calls from both tenants so requests are likely to share pooled
    # connections; if the GUC ever bled from one request to the next, one of these
    # would come back with the wrong tenant's data.
    tokens = [token_a, token_b] * 20
    results = await asyncio.gather(*(_get_me(t) for t in tokens))

    for token, result in zip(tokens, results, strict=True):
        expected_email = "a@example.com" if token == token_a else "b@example.com"
        expected_tenant = str(tenant_a) if token == token_a else str(tenant_b)
        assert result["email"] == expected_email
        assert result["tenant_id"] == expected_tenant


async def test_missing_authorization_header_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.get("/me")
    assert resp.status_code == 401


async def test_malformed_bearer_token_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.get("/me", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert resp.status_code == 401


async def test_refresh_token_rejected_as_bearer_token(
    client: httpx.AsyncClient, tenant_a: uuid.UUID
) -> None:
    credentials = {
        "tenant_id": str(tenant_a),
        "email": "carol@example.com",
        "password": "correct-horse-1",
    }
    await client.post("/register", json=credentials)
    login_resp = await client.post("/login", json=credentials)
    refresh_token = login_resp.cookies["refresh_token"]

    resp = await client.get("/me", headers={"Authorization": f"Bearer {refresh_token}"})
    assert resp.status_code == 401


async def test_tenant_id_cannot_be_overridden_by_request_data(
    client: httpx.AsyncClient, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    token = await _register_and_login(client, tenant_id=tenant_a, email="dave@example.com")

    resp = await client.get(
        "/me",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": str(tenant_b)},
        params={"tenant_id": str(tenant_b)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == str(tenant_a)
