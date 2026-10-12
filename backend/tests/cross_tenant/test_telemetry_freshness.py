"""GET /facilities/{id}/telemetry-freshness (Phase L task L.3): when each asset last reported.

The route joins two physically separate databases: the asset list comes from the main database
(tenant-checked), the readings from the TimescaleDB `sensor_readings` table (forced RLS). The
failures to guard against are leaking another tenant's activity into "fresh", reporting a dead
asset as fresh (or a live one as never reported), and the TimescaleDB SkipScan + RLS crash that
bit L.2b (the query is LATERAL, never DISTINCT).
"""

# ruff: noqa: F401, F811  (pytest fixtures are imported from test_telemetry_isolation)
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.core.security import create_access_token
from app.core.tenant_context import scope_session_to_tenant
from app.services.telemetry_service import get_last_reading_times
from tests.cross_tenant.test_telemetry_isolation import (
    APP_TIMESCALE_ROLE_PASSWORD,
    _dsn,
    _make_tenant,
    client,
    fake_redis_server,
    main_pg_container,
    migrated_main_db,
    migrated_timescale_db,
    tenant_a,
    tenant_b,
    timescale_pg_container,
)


def _token(tenant: uuid.UUID, role: str = "viewer") -> dict[str, str]:
    token = create_access_token(user_id=uuid.uuid4(), tenant_id=tenant, role=role)
    return {"Authorization": f"Bearer {token}"}


async def _admin(db: PostgresContainer) -> asyncpg.Connection:
    return await asyncpg.connect(
        _dsn(db, user=db.username, password=db.password, driver="postgresql")
    )


async def _facility_with_assets(
    db: PostgresContainer, tenant: uuid.UUID, n: int
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    conn = await _admin(db)
    try:
        facility = uuid.uuid4()
        await conn.execute(
            "INSERT INTO facilities (id, tenant_id, name) VALUES ($1, $2, $3)",
            facility,
            tenant,
            "F",
        )
        ids = [uuid.uuid4() for _ in range(n)]
        for i, asset in enumerate(ids):
            await conn.execute(
                "INSERT INTO assets (id, tenant_id, facility_id, name, type, x, y, status) "
                "VALUES ($1, $2, $3, $4, 'pump', 0, 0, 'operational')",
                asset,
                tenant,
                facility,
                f"A{i}",
            )
        return facility, ids
    finally:
        await conn.close()


async def _readings(
    db: PostgresContainer, tenant: uuid.UUID, asset: uuid.UUID, stamps: list[tuple[str, datetime]]
) -> None:
    conn = await _admin(db)
    try:
        await conn.copy_records_to_table(
            "sensor_readings",
            records=[(tenant, asset, sensor, 1.0, "x", ts) for sensor, ts in stamps],
            columns=["tenant_id", "asset_id", "sensor_type", "value", "unit", "timestamp"],
        )
    finally:
        await conn.close()


async def _get(client: httpx.AsyncClient, facility: uuid.UUID, headers: dict[str, str]):
    return await client.get(f"/facilities/{facility}/telemetry-freshness", headers=headers)


def _by_asset(body: dict) -> dict[str, str | None]:
    return {a["asset_id"]: a["last_reading_at"] for a in body["assets"]}


async def test_returns_the_newest_reading_across_all_sensors_and_null_for_a_silent_asset(
    client, migrated_main_db, migrated_timescale_db, tenant_a
) -> None:
    facility, (live, silent) = await _facility_with_assets(migrated_main_db, tenant_a, 2)
    now = datetime.now(UTC)
    newest = now - timedelta(seconds=20)
    await _readings(
        migrated_timescale_db,
        tenant_a,
        live,
        [
            ("torque", now - timedelta(minutes=9)),
            ("rpm", newest),
            ("temp", now - timedelta(hours=1)),
        ],
    )

    resp = await _get(client, facility, _token(tenant_a))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    got = _by_asset(body)
    assert datetime.fromisoformat(got[str(live)]) == newest  # newest of ANY sensor
    assert got[str(silent)] is None  # never reported: null, not "now" and not missing
    assert abs((datetime.fromisoformat(body["as_of"]) - now).total_seconds()) < 30


async def test_a_new_reading_moves_the_time_forward_so_a_stale_asset_recovers(
    client, migrated_main_db, migrated_timescale_db, tenant_a
) -> None:
    facility, (asset,) = await _facility_with_assets(migrated_main_db, tenant_a, 1)
    old = datetime.now(UTC) - timedelta(hours=2)
    await _readings(migrated_timescale_db, tenant_a, asset, [("rpm", old)])
    first = _by_asset((await _get(client, facility, _token(tenant_a))).json())[str(asset)]
    assert datetime.fromisoformat(first) == old

    fresh = datetime.now(UTC) - timedelta(seconds=5)
    await _readings(migrated_timescale_db, tenant_a, asset, [("rpm", fresh)])
    second = _by_asset((await _get(client, facility, _token(tenant_a))).json())[str(asset)]
    assert datetime.fromisoformat(second) == fresh


async def test_another_tenants_facility_is_a_404_not_an_empty_answer(
    client, migrated_main_db, tenant_a, tenant_b
) -> None:
    facility_b, _ = await _facility_with_assets(migrated_main_db, tenant_b, 1)

    resp = await _get(client, facility_b, _token(tenant_a))

    assert resp.status_code == 404
    assert (await _get(client, uuid.uuid4(), _token(tenant_a))).status_code == 404


async def test_another_tenants_readings_for_the_same_asset_id_never_count_as_fresh(
    client, migrated_main_db, migrated_timescale_db, tenant_a, tenant_b
) -> None:
    """Tenant B writes readings under tenant A's asset id. A's asset must still look silent."""
    facility, (asset,) = await _facility_with_assets(migrated_main_db, tenant_a, 1)
    await _readings(
        migrated_timescale_db, tenant_b, asset, [("rpm", datetime.now(UTC) - timedelta(seconds=1))]
    )

    body = (await _get(client, facility, _token(tenant_a))).json()

    assert _by_asset(body)[str(asset)] is None


async def test_rls_alone_hides_another_tenants_readings_even_if_the_filter_names_them(
    migrated_timescale_db, tenant_a, tenant_b
) -> None:
    """Service level: filter names B, session scoped to A (and to nobody). RLS must return none."""
    asset = uuid.uuid4()
    await _readings(migrated_timescale_db, tenant_b, asset, [("rpm", datetime.now(UTC))])
    engine = create_async_engine(
        _dsn(
            migrated_timescale_db,
            user="app_role",
            password=APP_TIMESCALE_ROLE_PASSWORD,
            driver="postgresql+asyncpg",
        )
    )
    try:
        for scope in (tenant_a, None):
            async with AsyncSession(engine) as session:
                if scope is not None:
                    await scope_session_to_tenant(session, scope)
                _, last = await get_last_reading_times(
                    session, tenant_id=tenant_b, asset_ids=[asset]
                )
            assert last == {asset: None}
    finally:
        await engine.dispose()


async def test_a_facility_with_no_assets_is_an_empty_list_with_a_clock(
    client, migrated_main_db, tenant_a
) -> None:
    facility, _ = await _facility_with_assets(migrated_main_db, tenant_a, 0)

    resp = await _get(client, facility, _token(tenant_a))

    assert resp.status_code == 200
    assert resp.json()["assets"] == [] and resp.json()["as_of"]


async def test_a_large_facility_works_under_rls_without_the_skipscan_crash(
    client, migrated_main_db, migrated_timescale_db, tenant_a
) -> None:
    """DISTINCT over sensor_readings crashes as app_role (TimescaleDB 2.29 SkipScan + RLS) once
    migration 0003's index exists. 60 assets, three sensors, readings spread over several chunks."""
    facility, assets = await _facility_with_assets(migrated_main_db, tenant_a, 60)
    now = datetime.now(UTC)
    for i, asset in enumerate(assets):
        await _readings(
            migrated_timescale_db,
            tenant_a,
            asset,
            [(s, now - timedelta(days=d, seconds=i)) for s in ("a", "b", "c") for d in (0, 9, 20)],
        )

    resp = await _get(client, facility, _token(tenant_a))

    assert resp.status_code == 200, resp.text
    got = _by_asset(resp.json())
    assert all(got[str(a)] is not None for a in assets)
    assert datetime.fromisoformat(got[str(assets[5])]) == now - timedelta(seconds=5)


async def test_every_role_may_read_it_but_a_missing_or_bad_token_may_not(
    client, migrated_main_db, tenant_a
) -> None:
    facility, _ = await _facility_with_assets(migrated_main_db, tenant_a, 1)
    for role in ("viewer", "technician", "facility_manager", "tenant_admin"):
        assert (await _get(client, facility, _token(tenant_a, role))).status_code == 200, role
    assert (await _get(client, facility, {})).status_code == 401
    assert (await _get(client, facility, {"Authorization": "Bearer nope"})).status_code == 401
