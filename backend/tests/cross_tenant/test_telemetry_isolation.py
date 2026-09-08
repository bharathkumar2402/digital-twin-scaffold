"""Cross-tenant isolation for POST /telemetry (issues 1.6 / 1.7-fix).

`sensor_readings` lives on a physically separate TimescaleDB instance from
tenants/users/facilities (Supabase doesn't support the `timescaledb` extension, and
Postgres has no cross-database foreign keys — see `migrations_timescale/versions/0001`
and `app/core/config.py`). This test spins up two containers standing in for those two
real managed instances: one migrated with the main `alembic.ini` chain (tenants/users,
for register/login), one migrated with `alembic_timescale.ini` (sensor_readings only).
Tenancy on sensor_readings is enforced purely by RLS, with no FK to back it up, so the
adversarial cases here (fail-closed with no GUC, cross-tenant read, cross-tenant WRITE)
matter more than they would with a foreign-key safety net.
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
APP_TIMESCALE_ROLE_PASSWORD = "test-app-timescale-role-password"


def _run_migrations(config_file: str, env: dict) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", config_file, "upgrade", "head"],
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
def main_pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="module")
def timescale_pg_container():
    with PostgresContainer("timescale/timescaledb:latest-pg16") as pg:
        yield pg


@pytest.fixture(scope="module")
def migrated_main_db(main_pg_container: PostgresContainer):
    env = os.environ.copy()
    env.update(
        POSTGRES_HOST=main_pg_container.get_container_host_ip(),
        POSTGRES_PORT=str(main_pg_container.get_exposed_port(5432)),
        POSTGRES_DB=main_pg_container.dbname,
        POSTGRES_USER=main_pg_container.username,
        POSTGRES_PASSWORD=main_pg_container.password,
        APP_DB_PASSWORD=APP_ROLE_PASSWORD,
    )
    _run_migrations("alembic.ini", env)
    return main_pg_container


@pytest.fixture(scope="module")
def migrated_timescale_db(timescale_pg_container: PostgresContainer):
    env = os.environ.copy()
    env.update(
        TIMESCALE_HOST=timescale_pg_container.get_container_host_ip(),
        TIMESCALE_PORT=str(timescale_pg_container.get_exposed_port(5432)),
        TIMESCALE_DB=timescale_pg_container.dbname,
        TIMESCALE_USER=timescale_pg_container.username,
        TIMESCALE_PASSWORD=timescale_pg_container.password,
        APP_TIMESCALE_PASSWORD=APP_TIMESCALE_ROLE_PASSWORD,
    )
    _run_migrations("alembic_timescale.ini", env)
    return timescale_pg_container


@pytest.fixture
async def client(
    migrated_main_db: PostgresContainer,
    migrated_timescale_db: PostgresContainer,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    from app.core.config import settings
    from app.core.db import get_session, get_timescale_session
    from app.main import app

    monkeypatch.setattr(settings, "cookie_secure", False)

    main_engine = create_async_engine(
        _dsn(
            migrated_main_db,
            user="app_role",
            password=APP_ROLE_PASSWORD,
            driver="postgresql+asyncpg",
        ),
        pool_pre_ping=True,
    )
    main_session_factory = async_sessionmaker(main_engine, expire_on_commit=False)

    timescale_engine = create_async_engine(
        _dsn(
            migrated_timescale_db,
            user="app_role",
            password=APP_TIMESCALE_ROLE_PASSWORD,
            driver="postgresql+asyncpg",
        ),
        pool_pre_ping=True,
    )
    timescale_session_factory = async_sessionmaker(timescale_engine, expire_on_commit=False)

    async def _get_session() -> AsyncGenerator:
        async with main_session_factory() as session:
            yield session

    async def _get_timescale_session() -> AsyncGenerator:
        async with timescale_session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[get_timescale_session] = _get_timescale_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
        await main_engine.dispose()
        await timescale_engine.dispose()


async def _make_tenant(migrated_main_db: PostgresContainer, name: str) -> uuid.UUID:
    conn = await asyncpg.connect(
        _dsn(
            migrated_main_db,
            user=migrated_main_db.username,
            password=migrated_main_db.password,
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
async def tenant_a(migrated_main_db: PostgresContainer) -> AsyncGenerator[uuid.UUID, None]:
    yield await _make_tenant(migrated_main_db, "Tenant A")


@pytest.fixture
async def tenant_b(migrated_main_db: PostgresContainer) -> AsyncGenerator[uuid.UUID, None]:
    yield await _make_tenant(migrated_main_db, "Tenant B")


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


async def _rows_visible_to_tenant(
    migrated_timescale_db: PostgresContainer, tenant_id: uuid.UUID
) -> list:
    conn = await asyncpg.connect(
        _dsn(
            migrated_timescale_db,
            user="app_role",
            password=APP_TIMESCALE_ROLE_PASSWORD,
            driver="postgresql",
        )
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
    client: httpx.AsyncClient,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
    migrated_timescale_db: PostgresContainer,
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

    rows_a = await _rows_visible_to_tenant(migrated_timescale_db, tenant_a)
    rows_b = await _rows_visible_to_tenant(migrated_timescale_db, tenant_b)

    assert {r["sensor_type"] for r in rows_a} == {"tenant-a-reading"}
    assert {r["sensor_type"] for r in rows_b} == {"tenant-b-reading"}


async def test_app_role_with_no_guc_sees_zero_rows_on_sensor_readings(
    client: httpx.AsyncClient, tenant_a: uuid.UUID, migrated_timescale_db: PostgresContainer
) -> None:
    """Fail-closed check: with no FK to `tenants` backing this table, RLS is the *only*
    thing standing between a misconfigured connection and every tenant's readings."""
    token = await _register_and_login(client, tenant_id=tenant_a, email="noguc@example.com")
    await client.post(
        "/telemetry",
        json={"readings": [_reading()]},
        headers={"Authorization": f"Bearer {token}"},
    )

    conn = await asyncpg.connect(
        _dsn(
            migrated_timescale_db,
            user="app_role",
            password=APP_TIMESCALE_ROLE_PASSWORD,
            driver="postgresql",
        )
    )
    try:
        rows = await conn.fetch("SELECT * FROM sensor_readings")
        assert rows == []
    finally:
        await conn.close()


async def test_app_role_cannot_insert_reading_for_a_different_tenant(
    migrated_timescale_db: PostgresContainer, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    """WITH CHECK enforcement: a session scoped to tenant A cannot write a row stamped
    with tenant B's id. Without the (impossible, cross-database) FK to `tenants`, this
    is the only thing preventing a bug from mislabeling a write's tenant_id."""
    conn = await asyncpg.connect(
        _dsn(
            migrated_timescale_db,
            user="app_role",
            password=APP_TIMESCALE_ROLE_PASSWORD,
            driver="postgresql",
        )
    )
    try:
        await conn.execute(
            "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_a)
        )
        with pytest.raises(asyncpg.exceptions.PostgresError):
            await conn.execute(
                "INSERT INTO sensor_readings (tenant_id, asset_id, sensor_type, value, "
                "unit, timestamp) VALUES ($1, $2, $3, $4, $5, now())",
                tenant_b,
                uuid.uuid4(),
                "sneaky",
                1.0,
                "celsius",
            )
    finally:
        await conn.close()


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


async def test_main_and_timescale_migration_chains_are_independent(
    migrated_main_db: PostgresContainer, migrated_timescale_db: PostgresContainer
) -> None:
    """The two chains must not share alembic_version state or tables: the main chain
    should never see sensor_readings, and the timescale chain should never see
    tenants/users/facilities."""
    main_conn = await asyncpg.connect(
        _dsn(
            migrated_main_db,
            user=migrated_main_db.username,
            password=migrated_main_db.password,
            driver="postgresql",
        )
    )
    timescale_conn = await asyncpg.connect(
        _dsn(
            migrated_timescale_db,
            user=migrated_timescale_db.username,
            password=migrated_timescale_db.password,
            driver="postgresql",
        )
    )
    try:
        main_tables = {
            r["tablename"]
            for r in await main_conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
        timescale_tables = {
            r["tablename"]
            for r in await timescale_conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
        assert "sensor_readings" not in main_tables
        assert {"tenants", "users", "facilities"}.isdisjoint(timescale_tables)

        main_version = await main_conn.fetchval("SELECT version_num FROM alembic_version")
        timescale_version = await timescale_conn.fetchval(
            "SELECT version_num FROM alembic_version"
        )
        assert main_version == "0008"
        assert timescale_version == "0002"
    finally:
        await main_conn.close()
        await timescale_conn.close()
