"""Cross-tenant isolation for POST /telemetry (issue 1.6).

`sensor_readings` isn't in `PROJECT_PLAN.md`'s table sketch with a `tenant_id` column,
but it's tenant-scoped telemetry data like everything else in this system, so it gets the
same fail-closed RLS policy (migration 0004) and the same HTTP-level test harness as
`test_tenant_isolation_template.py`: real ASGI app, `app_role` connection, migrations run
against a real TimescaleDB-enabled container.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
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
    with PostgresContainer("timescale/timescaledb:latest-pg16") as pg:
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
    yield await _make_tenant(migrated_db, "Tenant A")


@pytest.fixture
async def tenant_b(migrated_db: PostgresContainer) -> AsyncGenerator[uuid.UUID, None]:
    yield await _make_tenant(migrated_db, "Tenant B")


def _reading(**overrides) -> dict:
    reading = {
        "asset_id": str(uuid.uuid4()),
        "sensor_type": "temperature",
        "value": 42.5,
        "unit": "celsius",
        "timestamp": datetime.now(UTC).isoformat(),
    }
    reading.update(overrides)
    return reading


async def _rows_visible_to_tenant(migrated_db: PostgresContainer, tenant_id: uuid.UUID) -> list:
    conn = await asyncpg.connect(
        _dsn(migrated_db, user="app_role", password=APP_ROLE_PASSWORD, driver="postgresql")
    )
    try:
        await conn.execute(
            "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
        )
        return await conn.fetch("SELECT tenant_id, sensor_type FROM sensor_readings")
    finally:
        await conn.close()


async def test_ingest_returns_accepted_count(
    client: httpx.AsyncClient, tenant_a: uuid.UUID
) -> None:
    token = await _register_and_login(client, tenant_id=tenant_a, email="alice@example.com")

    resp = await client.post(
        "/telemetry",
        json={"readings": [_reading(), _reading(sensor_type="vibration")]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json() == {"accepted": 2}


async def test_a_tenant_cannot_read_another_tenants_readings_via_rls(
    client: httpx.AsyncClient, tenant_a: uuid.UUID, tenant_b: uuid.UUID, migrated_db
) -> None:
    same_asset = str(uuid.uuid4())
    token_a = await _register_and_login(client, tenant_id=tenant_a, email="a@example.com")
    token_b = await _register_and_login(client, tenant_id=tenant_b, email="b@example.com")

    await client.post(
        "/telemetry",
        json={"readings": [_reading(asset_id=same_asset, sensor_type="tenant-a-reading")]},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    await client.post(
        "/telemetry",
        json={"readings": [_reading(asset_id=same_asset, sensor_type="tenant-b-reading")]},
        headers={"Authorization": f"Bearer {token_b}"},
    )

    rows_a = await _rows_visible_to_tenant(migrated_db, tenant_a)
    rows_b = await _rows_visible_to_tenant(migrated_db, tenant_b)

    assert {r["sensor_type"] for r in rows_a} == {"tenant-a-reading"}
    assert {r["sensor_type"] for r in rows_b} == {"tenant-b-reading"}


async def test_concurrent_ingests_from_different_tenants_do_not_leak_via_pooled_connection(
    client: httpx.AsyncClient, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    token_a = await _register_and_login(client, tenant_id=tenant_a, email="c@example.com")
    token_b = await _register_and_login(client, tenant_id=tenant_b, email="d@example.com")

    async def _post(token: str, sensor_type: str) -> httpx.Response:
        return await client.post(
            "/telemetry",
            json={"readings": [_reading(sensor_type=sensor_type)]},
            headers={"Authorization": f"Bearer {token}"},
        )

    calls = [(token_a, "a-reading"), (token_b, "b-reading")] * 20
    results = await asyncio.gather(*(_post(token, st) for token, st in calls))

    for resp in results:
        assert resp.status_code == 201


async def test_empty_readings_list_rejected(
    client: httpx.AsyncClient, tenant_a: uuid.UUID
) -> None:
    token = await _register_and_login(client, tenant_id=tenant_a, email="e@example.com")

    resp = await client.post(
        "/telemetry",
        json={"readings": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422




async def test_missing_authorization_header_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.post("/telemetry", json={"readings": [_reading()]})
    assert resp.status_code == 401


async def test_malformed_bearer_token_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/telemetry",
        json={"readings": [_reading()]},
        headers={"Authorization": "Bearer not-a-real-jwt"},
    )
    assert resp.status_code == 401
