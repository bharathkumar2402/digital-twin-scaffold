"""The live anomaly baseline query (Phase L task L.2b): same answers, bounded cost, still isolated.

L.0 measured ingest getting 6x slower between 30k and 1M stored rows because the baseline was one
unbounded 7-day aggregate per (asset, sensor). L.2b replaced it with ONE statement that reads at
most `BASELINE_MAX_SAMPLES` newest rows per group through a new index. Everything here runs against
a real TimescaleDB with the real forced-RLS policy, connecting as the non-bypass `app_role`:
the failure to guard against is a faster query that returns different statistics, leaks another
tenant's readings into a baseline, or quietly falls back to scanning the whole history.
"""

# ruff: noqa: F401, F811  (pytest fixtures are imported from test_telemetry_isolation)
import json
import os
import random
import statistics
import subprocess
import sys
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.core.tenant_context import scope_session_to_tenant
from app.schemas.requests.telemetry import SensorReadingIn
from app.services import anomaly_detection_service as svc
from app.services.anomaly_detection_service import (
    BASELINE_MAX_SAMPLES,
    LOOKBACK_DAYS,
    MIN_SAMPLE_COUNT,
    check_readings_for_anomalies,
)
from tests.cross_tenant.test_telemetry_isolation import (
    APP_TIMESCALE_ROLE_PASSWORD,
    BACKEND_DIR,
    _dsn,
    _run_migrations,
    migrated_timescale_db,
    timescale_pg_container,
)

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)  # the batch's (earliest) timestamp in most tests
SENSOR = "torque"
INDEX = "ix_sensor_readings_asset_sensor_time"


# ---- fixtures and helpers -------------------------------------------------------------------


@pytest.fixture
async def admin(
    migrated_timescale_db: PostgresContainer,
) -> AsyncGenerator[asyncpg.Connection, None]:
    """Superuser connection (bypasses RLS): arranges data and runs the reference query only."""
    conn = await asyncpg.connect(
        _dsn(
            migrated_timescale_db,
            user=migrated_timescale_db.username,
            password=migrated_timescale_db.password,
            driver="postgresql",
        )
    )
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def app_engine(migrated_timescale_db: PostgresContainer):
    engine = create_async_engine(
        _dsn(
            migrated_timescale_db,
            user="app_role",
            password=APP_TIMESCALE_ROLE_PASSWORD,
            driver="postgresql+asyncpg",
        )
    )
    try:
        yield engine
    finally:
        await engine.dispose()


async def _insert(
    admin: asyncpg.Connection,
    tenant: uuid.UUID,
    asset: uuid.UUID,
    values_at: list[tuple[float, datetime]],
    sensor: str = SENSOR,
) -> None:
    await admin.copy_records_to_table(
        "sensor_readings",
        records=[(tenant, asset, sensor, v, "x", ts) for v, ts in values_at],
        columns=["tenant_id", "asset_id", "sensor_type", "value", "unit", "timestamp"],
    )


def _series(n: int, *, end: datetime = T0, step_s: int = 15, base: float = 40.0, seed: int = 1):
    """n readings ending one step before `end`, oldest first."""
    rng = random.Random(seed)
    return [(base + rng.gauss(0, 2), end - timedelta(seconds=step_s * (n - i))) for i in range(n)]


def _reading(asset: uuid.UUID, ts: datetime = T0, value: float = 40.0, sensor: str = SENSOR):
    return SensorReadingIn(asset_id=asset, sensor_type=sensor, value=value, unit="x", timestamp=ts)


async def _check(engine, tenant: uuid.UUID, readings, *, scope: uuid.UUID | None | str = "same"):
    """Run the production function as app_role, RLS-scoped to `scope` (default: `tenant`)."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        scoped = tenant if scope == "same" else scope
        if scoped is not None:
            await scope_session_to_tenant(session, scoped)
        return await check_readings_for_anomalies(session, tenant_id=tenant, readings=readings)


async def _reference(admin, tenant, asset, sensor, cutoff):
    """The pre-L.2b query, verbatim in meaning: unbounded aggregate over the whole 7-day window."""
    row = await admin.fetchrow(
        """SELECT count(*) AS cnt, avg(value) AS mean, stddev_samp(value) AS stddev
           FROM sensor_readings
           WHERE tenant_id=$1 AND asset_id=$2 AND sensor_type=$3
             AND timestamp >= $4 AND timestamp < $5""",
        tenant, asset, sensor, cutoff - timedelta(days=LOOKBACK_DAYS), cutoff,
    )  # fmt: skip
    return row["cnt"], row["mean"], row["stddev"]


@pytest.fixture
def tenants() -> tuple[uuid.UUID, uuid.UUID]:
    return uuid.uuid4(), uuid.uuid4()


# ---- same answers as before -------------------------------------------------------------------


@pytest.mark.parametrize("n", [0, 1, 9, 10, 11, 60, 150, BASELINE_MAX_SAMPLES])
async def test_matches_the_old_unbounded_query_whenever_history_fits_under_the_cap(
    admin, app_engine, tenants, n: int
) -> None:
    tenant, _ = tenants
    asset = uuid.uuid4()
    await _insert(admin, tenant, asset, _series(n, seed=n))

    (result,) = await _check(app_engine, tenant, [_reading(asset)])

    cnt, mean, stddev = await _reference(admin, tenant, asset, SENSOR, T0)
    assert result.sample_count == cnt == n
    if n:
        assert result.rolling_mean == pytest.approx(mean)
    else:
        assert result.rolling_mean is None
    if n >= 2:
        assert result.rolling_stddev == pytest.approx(stddev)
    assert result.is_anomaly is False  # the value 40.0 is the baseline's own centre


async def test_flags_an_outlier_and_not_a_normal_value_like_before(
    admin, app_engine, tenants
) -> None:
    tenant, _ = tenants
    asset = uuid.uuid4()
    await _insert(admin, tenant, asset, _series(100))

    normal, spike = await _check(
        app_engine, tenant, [_reading(asset, value=41.0), _reading(asset, value=400.0)]
    )

    assert (normal.is_anomaly, spike.is_anomaly) == (False, True)
    assert spike.z_score is not None and spike.z_score > 10
    assert spike.sample_count == 100 >= MIN_SAMPLE_COUNT


async def test_below_the_minimum_sample_count_nothing_is_flagged(
    admin, app_engine, tenants
) -> None:
    tenant, _ = tenants
    asset = uuid.uuid4()
    await _insert(admin, tenant, asset, _series(MIN_SAMPLE_COUNT - 1))

    (result,) = await _check(app_engine, tenant, [_reading(asset, value=10_000.0)])

    assert result.sample_count == MIN_SAMPLE_COUNT - 1
    assert result.is_anomaly is False and result.z_score is None


async def test_flat_history_does_not_divide_by_zero(admin, app_engine, tenants) -> None:
    tenant, _ = tenants
    asset = uuid.uuid4()
    await _insert(
        admin, tenant, asset, [(7.0, T0 - timedelta(seconds=15 * (20 - i))) for i in range(20)]
    )

    (result,) = await _check(app_engine, tenant, [_reading(asset, value=9.0)])

    assert result.rolling_stddev == 0 and result.z_score is None and result.is_anomaly is False


# ---- the cap: bounded cost, newest readings win ------------------------------------------------


async def test_longer_history_uses_exactly_the_newest_cap_readings(
    admin, app_engine, tenants
) -> None:
    tenant, _ = tenants
    asset = uuid.uuid4()
    n = BASELINE_MAX_SAMPLES + 200
    # old half sits near 10, newest half near 90: an unbounded baseline would land near 50
    data = [
        ((10.0 if i < 200 else 90.0) + (i % 5) * 0.1, T0 - timedelta(seconds=15 * (n - i)))
        for i in range(n)
    ]
    await _insert(admin, tenant, asset, data)

    (result,) = await _check(app_engine, tenant, [_reading(asset)])

    newest = [v for v, _ in data[-BASELINE_MAX_SAMPLES:]]
    assert result.sample_count == BASELINE_MAX_SAMPLES
    assert result.rolling_mean == pytest.approx(statistics.mean(newest))
    assert result.rolling_stddev == pytest.approx(statistics.stdev(newest))


# ---- window edges -----------------------------------------------------------------------------


async def test_window_edges_and_the_batch_never_being_its_own_baseline(
    admin, app_engine, tenants
) -> None:
    tenant, _ = tenants
    asset = uuid.uuid4()
    window = timedelta(days=LOOKBACK_DAYS)
    rows = [
        (1.0, T0 - window - timedelta(seconds=1)),  # just outside the window: excluded
        (2.0, T0 - window),  # exactly at window start: included
        (3.0, T0 - timedelta(milliseconds=1)),  # just before the cutoff: included
        (4.0, T0),  # exactly at the cutoff (the batch's own row): excluded
        (5.0, T0 + timedelta(seconds=5)),  # after the cutoff: excluded
    ]
    await _insert(admin, tenant, asset, rows)

    (result,) = await _check(app_engine, tenant, [_reading(asset)])

    assert result.sample_count == 2
    assert result.rolling_mean == pytest.approx(2.5)


async def test_a_group_uses_its_earliest_timestamp_as_the_cutoff(
    admin, app_engine, tenants
) -> None:
    tenant, _ = tenants
    asset = uuid.uuid4()
    await _insert(admin, tenant, asset, _series(30))  # all before T0
    # an in-batch later reading must not see... nor change the shared baseline
    early, late = _reading(asset, T0, 41.0), _reading(asset, T0 + timedelta(seconds=10), 42.0)

    r_early, r_late = await _check(app_engine, tenant, [late, early])

    assert r_early.sample_count == r_late.sample_count == 30
    assert r_early.rolling_mean == r_late.rolling_mean


# ---- tenant isolation (the part that must never regress) ----------------------------------------


async def test_another_tenants_readings_never_enter_the_baseline(
    admin, app_engine, tenants
) -> None:
    tenant_a, tenant_b = tenants
    asset = uuid.uuid4()  # the SAME asset id and sensor in both tenants, values far apart
    await _insert(
        admin, tenant_a, asset, [(20.0 + (i % 3), ts) for i, (_, ts) in enumerate(_series(40))]
    )
    await _insert(
        admin, tenant_b, asset, [(5000.0 + i, ts) for i, (_, ts) in enumerate(_series(70, seed=9))]
    )

    (result_a,) = await _check(app_engine, tenant_a, [_reading(asset, value=21.0)])
    (result_b,) = await _check(app_engine, tenant_b, [_reading(asset, value=5001.0)])

    assert result_a.sample_count == 40 and result_a.rolling_mean == pytest.approx(21.0, abs=1.5)
    assert result_b.sample_count == 70 and result_b.rolling_mean > 5000


async def test_rls_alone_blocks_cross_tenant_baselines_even_if_the_explicit_filter_is_wrong(
    admin, app_engine, tenants
) -> None:
    """Defense in depth: ask for tenant B's baseline from a session scoped to tenant A, and from a
    session with no tenant at all. The WHERE clause names B, but RLS must still return nothing."""
    tenant_a, tenant_b = tenants
    asset = uuid.uuid4()
    await _insert(admin, tenant_b, asset, _series(50))

    (as_a,) = await _check(app_engine, tenant_b, [_reading(asset)], scope=tenant_a)
    (as_nobody,) = await _check(app_engine, tenant_b, [_reading(asset)], scope=None)

    assert as_a.sample_count == 0 and as_a.rolling_mean is None
    assert as_nobody.sample_count == 0 and as_nobody.rolling_mean is None


# ---- one statement per batch, and it uses the index ---------------------------------------------


async def test_a_220_group_batch_issues_exactly_one_baseline_statement(
    admin, app_engine, tenants
) -> None:
    tenant, _ = tenants
    assets = [uuid.uuid4() for _ in range(44)]
    sensors = ["air_temperature", "process_temperature", "rotational_speed", "torque", "tool_wear"]
    for asset in assets:
        for sensor in sensors:
            await _insert(
                admin, tenant, asset, _series(15, seed=hash((asset, sensor)) % 1000), sensor
            )
    batch = [_reading(a, sensor=s) for a in assets for s in sensors]
    statements: list[str] = []

    @event.listens_for(app_engine.sync_engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany) -> None:
        if "sensor_readings" in statement:
            statements.append(statement)

    try:
        results = await _check(app_engine, tenant, batch)
    finally:
        event.remove(app_engine.sync_engine, "before_cursor_execute", _count)

    assert len(batch) == 220 and len(results) == 220
    assert len(statements) == 1, f"expected one baseline statement, saw {len(statements)}"
    assert all(r.sample_count == 15 for r in results)


async def test_a_batch_of_the_api_maximum_size_works(admin, app_engine, tenants) -> None:
    tenant, _ = tenants
    batch = [_reading(uuid.uuid4()) for _ in range(1000)]  # POST /telemetry accepts up to 1000
    results = await _check(app_engine, tenant, batch)
    assert len(results) == 1000 and all(r.sample_count == 0 for r in results)


async def test_an_empty_batch_runs_no_query(app_engine, tenants) -> None:
    tenant, _ = tenants
    statements: list[str] = []

    @event.listens_for(app_engine.sync_engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    try:
        assert await _check(app_engine, tenant, []) == []
    finally:
        event.remove(app_engine.sync_engine, "before_cursor_execute", _count)
    assert [s for s in statements if "sensor_readings" in s] == []


async def test_the_baseline_plan_uses_the_new_index_and_never_scans_the_history(
    admin, app_engine, tenants
) -> None:
    """A silent fall-back to the old index or to a seq scan is the regression to catch."""
    tenant, _ = tenants
    assets = [uuid.uuid4() for _ in range(6)]
    sensors = ["torque", "tool_wear", "air_temperature"]
    for asset in assets:
        for sensor in sensors:
            await _insert(admin, tenant, asset, _series(2500, seed=3), sensor)
    await admin.execute("ANALYZE sensor_readings")
    params = {
        "asset_ids": [assets[0], assets[1]],
        "sensor_types": ["torque", "tool_wear"],
        "cutoffs": [T0, T0],
        "tenant_id": tenant,
        "lookback_days": LOOKBACK_DAYS,
        "cap": BASELINE_MAX_SAMPLES,
    }
    explain = text("EXPLAIN (FORMAT JSON) " + svc._BASELINE_SQL.text).bindparams(
        *svc._BASELINE_SQL._bindparams.values()
    )

    async with AsyncSession(app_engine) as session:
        await scope_session_to_tenant(session, tenant)
        raw = (await session.execute(explain, params)).scalar_one()

    plan = json.dumps(raw)
    assert INDEX in plan, "the baseline lookup must use the dedicated index"

    def seq_scans(node: dict) -> list[dict]:
        found = [node] if node.get("Node Type") == "Seq Scan" else []
        for child in node.get("Plans", []):
            found += seq_scans(child)
        return found

    # A seq scan over a near-empty leftover chunk is fine; one over a chunk holding the
    # history (these tests load 45k rows) would be the regression.
    big = [n for n in seq_scans(raw[0]["Plan"]) if n["Plan Rows"] > 100]
    assert big == [], f"history scanned sequentially: {big}"


# ---- the scoring queries must keep working with the new index (TimescaleDB SkipScan) ----------


async def _load_multi_chunk_history(admin, tenant, assets, sensors, rows_per_group=1500):
    """Weeks of history so the hypertable has several chunks, then fresh statistics."""
    for asset in assets:
        for sensor in sensors:
            rng = random.Random(f"{asset}{sensor}")
            await _insert(
                admin, tenant, asset,
                [
                    (rng.gauss(50, 3), T0 - timedelta(minutes=20 * (rows_per_group - i)))
                    for i in range(rows_per_group)
                ],
                sensor,
            )  # fmt: skip
    await admin.execute("ANALYZE sensor_readings")


async def test_scoring_queries_still_run_with_the_new_index_under_rls(
    admin, app_engine, tenants
) -> None:
    """Regression for a bug L.2b introduced and caught while re-measuring on the demo stack: once
    (asset_id, sensor_type, timestamp DESC) exists, TimescaleDB 2.29 plans the scoring queries'
    `DISTINCT ON` as a SkipScan and, with a row-level-security policy on the table, aborts with
    "unsupported subplan type for SkipScan: Result". Both queries were rewritten as LATERAL
    newest-row lookups; this runs them as app_role over multi-chunk data and checks the answers."""
    from app.services import feature_engineering_service as fes

    tenant, _ = tenants
    assets = [uuid.uuid4() for _ in range(3)]
    sensors = list(fes.SNAPSHOT_RAW_SENSORS)
    await _load_multi_chunk_history(admin, tenant, assets, sensors)
    chunks = await admin.fetchval("SELECT count(*) FROM show_chunks('sensor_readings')")
    assert chunks >= 3, "the failure needs a multi-chunk hypertable"
    expected = {
        (a, s): await admin.fetchrow(
            "SELECT value, timestamp FROM sensor_readings WHERE tenant_id=$1 AND asset_id=$2 "
            "AND sensor_type=$3 ORDER BY timestamp DESC LIMIT 1", tenant, a, s,
        )
        for a in assets for s in sensors
    }  # fmt: skip

    async with AsyncSession(app_engine) as session:
        await scope_session_to_tenant(session, tenant)
        latest = (
            await session.execute(
                fes._LATEST_READINGS_SQL,
                {
                    "tenant_id": tenant,
                    "asset_ids": assets + [uuid.uuid4()],
                    "sensor_types": sensors,
                },
            )
        ).all()
        windows = await fes._asset_sensor_windows(
            session, tenant_id=tenant, asset_id=assets[0], now=T0 + timedelta(days=1)
        )

    assert len(latest) == len(expected), "an asset without readings must simply be absent"
    for row in latest:
        want = expected[(row.asset_id, row.sensor_type)]
        assert (row.value, row.timestamp) == (want["value"], want["timestamp"])
    for window_days, by_sensor in windows.items():
        assert set(by_sensor) == set(sensors), window_days
        for sensor, stats in by_sensor.items():
            assert stats.latest_value == expected[(assets[0], sensor)]["value"]


# ---- the migration: index only, RLS untouched ---------------------------------------------------


def _env(c: PostgresContainer) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        TIMESCALE_HOST=c.get_container_host_ip(),
        TIMESCALE_PORT=str(c.get_exposed_port(5432)),
        TIMESCALE_DB=c.dbname,
        TIMESCALE_USER=c.username,
        TIMESCALE_PASSWORD=c.password,
        APP_TIMESCALE_PASSWORD=APP_TIMESCALE_ROLE_PASSWORD,
    )
    return env


async def _rls_facts(admin: asyncpg.Connection) -> dict:
    table = await admin.fetchrow(
        "SELECT relrowsecurity AS enabled, relforcerowsecurity AS forced FROM pg_class "
        "WHERE relname='sensor_readings'"
    )
    policy = await admin.fetchrow(
        "SELECT qual, with_check FROM pg_policies WHERE tablename='sensor_readings' "
        "AND policyname='tenant_isolation_sensor_readings'"
    )
    grants = await admin.fetch(
        "SELECT privilege_type FROM information_schema.role_table_grants "
        "WHERE table_name='sensor_readings' AND grantee='app_role' ORDER BY 1"
    )
    return {
        "enabled": table["enabled"], "forced": table["forced"],
        "qual": policy["qual"], "check": policy["with_check"],
        "grants": [g["privilege_type"] for g in grants],
    }  # fmt: skip


async def _has_index(admin: asyncpg.Connection) -> bool:
    return bool(await admin.fetchval("SELECT 1 FROM pg_indexes WHERE indexname = $1", INDEX))


async def test_migration_adds_only_an_index_and_round_trips_without_touching_rls(
    admin, app_engine, migrated_timescale_db, tenants
) -> None:
    tenant_a, tenant_b = tenants
    assert await _has_index(admin)
    before = await _rls_facts(admin)
    assert before["enabled"] and before["forced"], "precondition: RLS is forced on sensor_readings"

    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic_timescale.ini", "downgrade", "0002"],
        cwd=BACKEND_DIR, env=_env(migrated_timescale_db), check=True, capture_output=True,
    )  # fmt: skip
    try:
        assert not await _has_index(admin)
        assert await _rls_facts(admin) == before, "downgrade must not change RLS or grants"
    finally:
        _run_migrations("alembic_timescale.ini", _env(migrated_timescale_db))

    assert await _has_index(admin)
    assert await _rls_facts(admin) == before, "upgrade must not change RLS or grants"

    # and the policy still isolates tenants through the real runtime role after the round trip
    asset = uuid.uuid4()
    await _insert(admin, tenant_a, asset, _series(20))
    (as_a,) = await _check(app_engine, tenant_a, [_reading(asset)])
    (as_b,) = await _check(app_engine, tenant_a, [_reading(asset)], scope=tenant_b)
    assert as_a.sample_count == 20 and as_b.sample_count == 0
