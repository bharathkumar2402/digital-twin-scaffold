"""Integration tests for GET /facilities/{id}/assets/{id}/features (issue 3.1).

Combines the main DB (assets/asset_dependencies) and the separate TimescaleDB
(sensor_readings) - same dual-container/app_role harness as
tests/cross_tenant/test_telemetry_isolation.py, since this route is the first one to
read from both databases in a single request. Runs against real Postgres containers
with the real Alembic migrations and RLS policies (`app_role`, not an admin/BYPASSRLS
connection), not mocks.
"""

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
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


def _token(*, tenant_id: uuid.UUID, role: str = "tenant_admin") -> str:
    # Fabricates a JWT directly rather than going through /register (which always
    # assigns Role.VIEWER - too low for the asset/dependency writes these tests need)
    # - same approach test_assets_routes.py uses. get_tenant_context only decodes the
    # token, it never looks the user up in the DB, so a random user_id is fine.
    from app.core.security import create_access_token

    return create_access_token(user_id=uuid.uuid4(), tenant_id=tenant_id, role=role)


@pytest.fixture
async def tenant_a(migrated_main_db: PostgresContainer) -> AsyncGenerator[uuid.UUID, None]:
    yield await _make_tenant(migrated_main_db, "Tenant A")


@pytest.fixture
async def tenant_b(migrated_main_db: PostgresContainer) -> AsyncGenerator[uuid.UUID, None]:
    yield await _make_tenant(migrated_main_db, "Tenant B")


async def _insert_facility(
    migrated_main_db: PostgresContainer, *, tenant_id: uuid.UUID, name: str
) -> uuid.UUID:
    conn = await asyncpg.connect(
        _dsn(
            migrated_main_db,
            user=migrated_main_db.username,
            password=migrated_main_db.password,
            driver="postgresql",
        )
    )
    try:
        facility_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO facilities (id, tenant_id, name) VALUES ($1, $2, $3)",
            facility_id,
            tenant_id,
            name,
        )
        return facility_id
    finally:
        await conn.close()


async def _create_asset(
    client: httpx.AsyncClient,
    *,
    token: str,
    facility_id: uuid.UUID,
    name: str,
    status: str = "operational",
    installed_date: str | None = None,
) -> uuid.UUID:
    resp = await client.post(
        f"/facilities/{facility_id}/assets",
        json={
            "name": name,
            "type": "pump",
            "x": 1.0,
            "y": 1.0,
            "status": status,
            "installed_date": installed_date,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["id"])


async def _link(
    client: httpx.AsyncClient,
    *,
    token: str,
    facility_id: uuid.UUID,
    parent_asset_id: uuid.UUID,
    child_asset_id: uuid.UUID,
) -> None:
    resp = await client.post(
        f"/facilities/{facility_id}/asset-dependencies",
        json={"parent_asset_id": str(parent_asset_id), "child_asset_id": str(child_asset_id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text


async def _post_reading(
    client: httpx.AsyncClient,
    *,
    token: str,
    asset_id: uuid.UUID,
    days_ago: float,
    sensor_type: str = "temperature",
) -> None:
    timestamp = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    resp = await client.post(
        "/telemetry",
        json={
            "readings": [
                {
                    "asset_id": str(asset_id),
                    "sensor_type": sensor_type,
                    "value": 50.0,
                    "unit": "celsius",
                    "timestamp": timestamp,
                }
            ]
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text


async def test_window_counts_respect_window_boundaries(
    client: httpx.AsyncClient, tenant_a: uuid.UUID, migrated_main_db: PostgresContainer
) -> None:
    token = _token(tenant_id=tenant_a)
    facility_id = await _insert_facility(migrated_main_db, tenant_id=tenant_a, name="Plant 1")
    asset_id = await _create_asset(client, token=token, facility_id=facility_id, name="Pump 1")

    await _post_reading(client, token=token, asset_id=asset_id, days_ago=1)
    await _post_reading(client, token=token, asset_id=asset_id, days_ago=40)
    await _post_reading(client, token=token, asset_id=asset_id, days_ago=100)
    await _post_reading(client, token=token, asset_id=asset_id, days_ago=400)

    resp = await client.get(
        f"/facilities/{facility_id}/assets/{asset_id}/features",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    windows = resp.json()["windows"]

    assert windows["30"]["temperature"]["count"] == 1
    assert windows["90"]["temperature"]["count"] == 2
    assert windows["365"]["temperature"]["count"] == 3


async def test_no_readings_in_window_returns_empty_window_map(
    client: httpx.AsyncClient, tenant_a: uuid.UUID, migrated_main_db: PostgresContainer
) -> None:
    token = _token(tenant_id=tenant_a)
    facility_id = await _insert_facility(migrated_main_db, tenant_id=tenant_a, name="Plant 2")
    asset_id = await _create_asset(client, token=token, facility_id=facility_id, name="Pump 2")

    resp = await client.get(
        f"/facilities/{facility_id}/assets/{asset_id}/features",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["windows"]["30"] == {}
    assert body["dependency_neighbor_count"] == 0


async def test_asset_age_computed_from_installed_date(
    client: httpx.AsyncClient, tenant_a: uuid.UUID, migrated_main_db: PostgresContainer
) -> None:
    token = _token(tenant_id=tenant_a)
    facility_id = await _insert_facility(migrated_main_db, tenant_id=tenant_a, name="Plant 3")
    installed = (datetime.now(UTC) - timedelta(days=500)).date().isoformat()
    asset_id = await _create_asset(
        client, token=token, facility_id=facility_id, name="Pump 3", installed_date=installed
    )

    resp = await client.get(
        f"/facilities/{facility_id}/assets/{asset_id}/features",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["asset_age_days"] in (499, 500, 501)  # tolerate test-run clock drift


async def test_asset_with_no_installed_date_has_null_age(
    client: httpx.AsyncClient, tenant_a: uuid.UUID, migrated_main_db: PostgresContainer
) -> None:
    token = _token(tenant_id=tenant_a)
    facility_id = await _insert_facility(migrated_main_db, tenant_id=tenant_a, name="Plant 4")
    asset_id = await _create_asset(client, token=token, facility_id=facility_id, name="Pump 4")

    resp = await client.get(
        f"/facilities/{facility_id}/assets/{asset_id}/features",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["asset_age_days"] is None


async def test_dependency_neighbor_counts_both_directions(
    client: httpx.AsyncClient, tenant_a: uuid.UUID, migrated_main_db: PostgresContainer
) -> None:
    token = _token(tenant_id=tenant_a)
    facility_id = await _insert_facility(migrated_main_db, tenant_id=tenant_a, name="Plant 5")
    target = await _create_asset(client, token=token, facility_id=facility_id, name="Target")
    offline_child = await _create_asset(
        client, token=token, facility_id=facility_id, name="Offline child", status="offline"
    )
    maintenance_parent = await _create_asset(
        client,
        token=token,
        facility_id=facility_id,
        name="Maintenance parent",
        status="maintenance",
    )

    # target depends on offline_child (target is parent, offline_child is child)
    await _link(
        client,
        token=token,
        facility_id=facility_id,
        parent_asset_id=target,
        child_asset_id=offline_child,
    )
    # maintenance_parent depends on target (maintenance_parent is parent, target is child)
    await _link(
        client,
        token=token,
        facility_id=facility_id,
        parent_asset_id=maintenance_parent,
        child_asset_id=target,
    )

    resp = await client.get(
        f"/facilities/{facility_id}/assets/{target}/features",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dependency_neighbor_count"] == 2
    assert body["dependency_neighbor_offline_count"] == 1
    assert body["dependency_neighbor_maintenance_count"] == 1


async def test_a_tenant_cannot_read_another_tenants_asset_features(
    client: httpx.AsyncClient,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
    migrated_main_db: PostgresContainer,
) -> None:
    """New route combining two RLS-protected databases in one request - confirm the
    explicit tenant_id filter on the asset lookup blocks a cross-tenant read (404, not
    another tenant's feature vector), same due-diligence 2.8 applied to its new
    telemetry read path."""
    token_a = _token(tenant_id=tenant_a)
    token_b = _token(tenant_id=tenant_b)
    facility_id = await _insert_facility(migrated_main_db, tenant_id=tenant_a, name="Plant 6")
    asset_id = await _create_asset(client, token=token_a, facility_id=facility_id, name="Pump 6")

    resp = await client.get(
        f"/facilities/{facility_id}/assets/{asset_id}/features",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 404


async def test_get_features_requires_authorization(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"/facilities/{uuid.uuid4()}/assets/{uuid.uuid4()}/features")
    assert resp.status_code == 401
