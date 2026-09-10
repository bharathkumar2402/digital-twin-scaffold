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
import json
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
from fakeredis import aioredis as fakeredis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.services.alert_publish_service import alert_channel

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
def fake_redis_server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


@pytest.fixture
async def client(
    migrated_main_db: PostgresContainer,
    migrated_timescale_db: PostgresContainer,
    fake_redis_server: fakeredis.FakeServer,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    from app.core.config import settings
    from app.core.db import get_session, get_timescale_session
    from app.core.redis_client import get_redis
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

    async def _get_redis() -> AsyncGenerator:
        redis = fakeredis.FakeRedis(server=fake_redis_server, decode_responses=True)
        try:
            yield redis
        finally:
            await redis.aclose()

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[get_timescale_session] = _get_timescale_session
    app.dependency_overrides[get_redis] = _get_redis
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
    body = resp.json()
    assert body["accepted"] == 2
    # No prior history for either reading, so neither can be flagged (issue 3.4).
    assert len(body["anomalies"]) == 2
    assert all(a["is_anomaly"] is False for a in body["anomalies"])
    assert all(a["sample_count"] == 0 for a in body["anomalies"])


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


async def test_get_asset_telemetry_returns_readings_most_recent_first(
    client: httpx.AsyncClient, tenant_a: uuid.UUID
) -> None:
    token = await _register_and_login(client, tenant_id=tenant_a, email="f@example.com")
    asset_id = str(uuid.uuid4())
    older = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
    newer = datetime(2026, 1, 2, tzinfo=UTC).isoformat()

    await client.post(
        "/telemetry",
        json={
            "readings": [
                _reading(asset_id=asset_id, sensor_type="temperature", timestamp=older),
                _reading(asset_id=asset_id, sensor_type="temperature", timestamp=newer),
            ]
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.get(
        f"/facilities/{uuid.uuid4()}/assets/{asset_id}/telemetry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [datetime.fromisoformat(r["timestamp"]) for r in body] == [
        datetime.fromisoformat(newer),
        datetime.fromisoformat(older),
    ]


async def test_get_asset_telemetry_filters_by_sensor_type(
    client: httpx.AsyncClient, tenant_a: uuid.UUID
) -> None:
    token = await _register_and_login(client, tenant_id=tenant_a, email="g@example.com")
    asset_id = str(uuid.uuid4())

    await client.post(
        "/telemetry",
        json={
            "readings": [
                _reading(asset_id=asset_id, sensor_type="temperature"),
                _reading(asset_id=asset_id, sensor_type="vibration"),
            ]
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.get(
        f"/facilities/{uuid.uuid4()}/assets/{asset_id}/telemetry",
        params={"sensor_type": "vibration"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["sensor_type"] == "vibration"


async def test_get_asset_telemetry_caps_limit_at_max(
    client: httpx.AsyncClient, tenant_a: uuid.UUID
) -> None:
    token = await _register_and_login(client, tenant_id=tenant_a, email="h@example.com")
    asset_id = str(uuid.uuid4())

    resp = await client.get(
        f"/facilities/{uuid.uuid4()}/assets/{asset_id}/telemetry",
        params={"limit": 99999},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


async def test_a_tenant_cannot_read_another_tenants_asset_telemetry_via_get_route(
    client: httpx.AsyncClient, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    """New GET read path over the same RLS-protected table as the ingest-side isolation
    test above - confirms RLS blocks the read (empty list), not just that the write
    side is isolated."""
    same_asset = str(uuid.uuid4())
    token_a = await _register_and_login(client, tenant_id=tenant_a, email="i@example.com")
    token_b = await _register_and_login(client, tenant_id=tenant_b, email="j@example.com")

    await client.post(
        "/telemetry",
        json={"readings": [_reading(asset_id=same_asset, sensor_type="tenant-a-only")]},
        headers={"Authorization": f"Bearer {token_a}"},
    )

    resp_a = await client.get(
        f"/facilities/{uuid.uuid4()}/assets/{same_asset}/telemetry",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    resp_b = await client.get(
        f"/facilities/{uuid.uuid4()}/assets/{same_asset}/telemetry",
        headers={"Authorization": f"Bearer {token_b}"},
    )

    assert resp_a.status_code == 200, resp_a.text
    assert len(resp_a.json()) == 1
    assert resp_b.status_code == 200, resp_b.text
    assert resp_b.json() == []


async def test_get_asset_telemetry_requires_authorization(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"/facilities/{uuid.uuid4()}/assets/{uuid.uuid4()}/telemetry")
    assert resp.status_code == 401


async def test_outlier_reading_is_flagged_as_anomaly_once_baseline_exists(
    client: httpx.AsyncClient, tenant_a: uuid.UUID
) -> None:
    """Issue 3.4: a value far from the established rolling baseline must be flagged,
    once enough prior history exists to trust a stddev."""
    token = await _register_and_login(client, tenant_id=tenant_a, email="k@example.com")
    asset_id = str(uuid.uuid4())
    baseline_values = [20.0, 21.0, 19.0, 20.0, 21.0, 19.0, 20.0, 21.0, 19.0, 20.0]
    baseline_readings = [
        _reading(
            asset_id=asset_id,
            sensor_type="pressure",
            value=value,
            timestamp=datetime(2026, 1, 1, 0, i, tzinfo=UTC).isoformat(),
        )
        for i, value in enumerate(baseline_values)
    ]
    resp = await client.post(
        "/telemetry",
        json={"readings": baseline_readings},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text

    resp = await client.post(
        "/telemetry",
        json={
            "readings": [
                _reading(
                    asset_id=asset_id,
                    sensor_type="pressure",
                    value=500.0,
                    timestamp=datetime(2026, 1, 1, 1, 0, tzinfo=UTC).isoformat(),
                )
            ]
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    anomalies = resp.json()["anomalies"]
    assert len(anomalies) == 1
    assert anomalies[0]["sample_count"] == 10
    assert anomalies[0]["is_anomaly"] is True
    assert anomalies[0]["z_score"] is not None


async def test_normal_reading_within_baseline_is_not_flagged(
    client: httpx.AsyncClient, tenant_a: uuid.UUID
) -> None:
    token = await _register_and_login(client, tenant_id=tenant_a, email="l@example.com")
    asset_id = str(uuid.uuid4())
    baseline_values = [20.0, 21.0, 19.0, 20.0, 21.0, 19.0, 20.0, 21.0, 19.0, 20.0]
    baseline_readings = [
        _reading(
            asset_id=asset_id,
            sensor_type="pressure",
            value=value,
            timestamp=datetime(2026, 1, 2, 0, i, tzinfo=UTC).isoformat(),
        )
        for i, value in enumerate(baseline_values)
    ]
    await client.post(
        "/telemetry",
        json={"readings": baseline_readings},
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.post(
        "/telemetry",
        json={
            "readings": [
                _reading(
                    asset_id=asset_id,
                    sensor_type="pressure",
                    value=20.5,
                    timestamp=datetime(2026, 1, 2, 1, 0, tzinfo=UTC).isoformat(),
                )
            ]
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    anomalies = resp.json()["anomalies"]
    assert anomalies[0]["sample_count"] == 10
    assert anomalies[0]["is_anomaly"] is False


async def test_anomaly_baseline_does_not_leak_across_tenants(
    client: httpx.AsyncClient, tenant_a: uuid.UUID, tenant_b: uuid.UUID
) -> None:
    """A different tenant's readings for the *same* asset_id/sensor_type must never
    contribute to this tenant's rolling baseline - RLS must scope the anomaly
    detector's own query, not just the ingest/read paths already covered above."""
    same_asset = str(uuid.uuid4())
    token_a = await _register_and_login(client, tenant_id=tenant_a, email="m@example.com")
    token_b = await _register_and_login(client, tenant_id=tenant_b, email="n@example.com")

    baseline_values = [20.0, 21.0, 19.0, 20.0, 21.0, 19.0, 20.0, 21.0, 19.0, 20.0, 19.0]
    baseline_readings = [
        _reading(
            asset_id=same_asset,
            sensor_type="temperature",
            value=value,
            timestamp=datetime(2026, 1, 3, 0, i, tzinfo=UTC).isoformat(),
        )
        for i, value in enumerate(baseline_values)
    ]
    resp = await client.post(
        "/telemetry",
        json={"readings": baseline_readings},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert resp.status_code == 201, resp.text

    resp = await client.post(
        "/telemetry",
        json={
            "readings": [
                _reading(
                    asset_id=same_asset,
                    sensor_type="temperature",
                    value=20.0,
                    timestamp=datetime(2026, 1, 3, 2, 0, tzinfo=UTC).isoformat(),
                )
            ]
        },
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert resp.status_code == 201, resp.text
    anomalies = resp.json()["anomalies"]
    assert anomalies[0]["sample_count"] == 0
    assert anomalies[0]["rolling_mean"] is None
    assert anomalies[0]["is_anomaly"] is False


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
        assert main_version == "0009"
        assert timescale_version == "0002"
    finally:
        await main_conn.close()
        await timescale_conn.close()


async def _subscribe(fake_redis_server: fakeredis.FakeServer, tenant_id: uuid.UUID) -> tuple:
    redis = fakeredis.FakeRedis(server=fake_redis_server, decode_responses=True)
    pubsub = redis.pubsub()
    await pubsub.subscribe(alert_channel(tenant_id))
    await pubsub.get_message(timeout=1)  # discard the "subscribe" confirmation
    return redis, pubsub


async def test_a_debounced_anomaly_publishes_to_the_tenants_alert_channel(
    client: httpx.AsyncClient, tenant_a: uuid.UUID, fake_redis_server: fakeredis.FakeServer
) -> None:
    """Issue 3.6: the first anomaly for an asset must reach that tenant's Redis alert
    channel (`alert_publish_service.alert_channel`), wired through 3.5's debounce
    decision engine at the route level (`app/api/telemetry.py`)."""
    token = await _register_and_login(client, tenant_id=tenant_a, email="o@example.com")
    asset_id = str(uuid.uuid4())
    redis, pubsub = await _subscribe(fake_redis_server, tenant_a)
    try:
        baseline_values = [20.0, 21.0, 19.0, 20.0, 21.0, 19.0, 20.0, 21.0, 19.0, 20.0]
        baseline_readings = [
            _reading(
                asset_id=asset_id,
                sensor_type="pressure",
                value=value,
                timestamp=datetime(2026, 1, 4, 0, i, tzinfo=UTC).isoformat(),
            )
            for i, value in enumerate(baseline_values)
        ]
        await client.post(
            "/telemetry",
            json={"readings": baseline_readings},
            headers={"Authorization": f"Bearer {token}"},
        )

        resp = await client.post(
            "/telemetry",
            json={
                "readings": [
                    _reading(
                        asset_id=asset_id,
                        sensor_type="pressure",
                        value=500.0,
                        timestamp=datetime(2026, 1, 4, 1, 0, tzinfo=UTC).isoformat(),
                    )
                ]
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["anomalies"][0]["is_anomaly"] is True

        message = await pubsub.get_message(timeout=1)
        assert message is not None
        assert message["type"] == "message"
        payload = json.loads(message["data"])
        assert payload["asset_id"] == asset_id
        assert payload["sensor_type"] == "pressure"
    finally:
        await pubsub.aclose()
        await redis.aclose()


async def test_repeated_anomalies_on_the_same_asset_do_not_flood_the_alert_channel(
    client: httpx.AsyncClient, tenant_a: uuid.UUID, fake_redis_server: fakeredis.FakeServer
) -> None:
    """The phase plan's DoD wording again, now end-to-end through the live HTTP route:
    'rapid repeated anomalies on one asset do NOT produce a flood of triggers' - here,
    a flood of published alert messages."""
    token = await _register_and_login(client, tenant_id=tenant_a, email="p@example.com")
    asset_id = str(uuid.uuid4())
    redis, pubsub = await _subscribe(fake_redis_server, tenant_a)
    try:
        baseline_values = [20.0, 21.0, 19.0, 20.0, 21.0, 19.0, 20.0, 21.0, 19.0, 20.0]
        baseline_readings = [
            _reading(
                asset_id=asset_id,
                sensor_type="pressure",
                value=value,
                timestamp=datetime(2026, 1, 5, 0, i, tzinfo=UTC).isoformat(),
            )
            for i, value in enumerate(baseline_values)
        ]
        await client.post(
            "/telemetry",
            json={"readings": baseline_readings},
            headers={"Authorization": f"Bearer {token}"},
        )

        for minute in range(1, 6):
            resp = await client.post(
                "/telemetry",
                json={
                    "readings": [
                        _reading(
                            asset_id=asset_id,
                            sensor_type="pressure",
                            value=500.0,
                            timestamp=datetime(2026, 1, 5, 1, minute, tzinfo=UTC).isoformat(),
                        )
                    ]
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 201, resp.text

        messages = []
        while True:
            message = await pubsub.get_message(timeout=0.2)
            if message is None:
                break
            messages.append(message)

        assert len(messages) == 1
    finally:
        await pubsub.aclose()
        await redis.aclose()


async def test_an_anomaly_for_one_tenant_is_not_published_on_another_tenants_channel(
    client: httpx.AsyncClient,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
    fake_redis_server: fakeredis.FakeServer,
) -> None:
    token_a = await _register_and_login(client, tenant_id=tenant_a, email="q@example.com")
    asset_id = str(uuid.uuid4())
    redis_b, pubsub_b = await _subscribe(fake_redis_server, tenant_b)
    try:
        baseline_values = [20.0, 21.0, 19.0, 20.0, 21.0, 19.0, 20.0, 21.0, 19.0, 20.0]
        baseline_readings = [
            _reading(
                asset_id=asset_id,
                sensor_type="pressure",
                value=value,
                timestamp=datetime(2026, 1, 6, 0, i, tzinfo=UTC).isoformat(),
            )
            for i, value in enumerate(baseline_values)
        ]
        await client.post(
            "/telemetry",
            json={"readings": baseline_readings},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        resp = await client.post(
            "/telemetry",
            json={
                "readings": [
                    _reading(
                        asset_id=asset_id,
                        sensor_type="pressure",
                        value=500.0,
                        timestamp=datetime(2026, 1, 6, 1, 0, tzinfo=UTC).isoformat(),
                    )
                ]
            },
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["anomalies"][0]["is_anomaly"] is True

        message = await pubsub_b.get_message(timeout=0.2)
        assert message is None
    finally:
        await pubsub_b.aclose()
        await redis_b.aclose()
