"""Retention on the RLS table `sensor_readings` (Phase L task L.8), on a real TimescaleDB.

The risk of this task is not the deletion, it is the migration: it must not weaken the table's
row-level security or its grants, and the cleanup must work on every tenant's old data without ever
being something an API user can trigger. Everything runs against the real migration chain.
"""

# ruff: noqa: F401, F811  (pytest fixtures are imported from test_telemetry_isolation)
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from tests.cross_tenant.test_telemetry_isolation import (
    APP_TIMESCALE_ROLE_PASSWORD,
    BACKEND_DIR,
    _dsn,
    migrated_timescale_db,
    timescale_pg_container,
)

POLICY = "tenant_isolation_sensor_readings"
JOBS_SQL = (
    "SELECT job_id, config->>'drop_after' AS drop_after FROM timescaledb_information.jobs "
    "WHERE proc_name = 'policy_retention' AND hypertable_name = 'sensor_readings'"
)


def _ago(**kw) -> datetime:
    return datetime.now(UTC) - timedelta(**kw)


async def _admin(db) -> asyncpg.Connection:
    return await asyncpg.connect(
        _dsn(db, user=db.username, password=db.password, driver="postgresql")
    )


async def _app(db, tenant: uuid.UUID | None) -> asyncpg.Connection:
    """A connection as the non-bypass runtime role, scoped to `tenant` like the API does."""
    conn = await asyncpg.connect(
        _dsn(db, user="app_role", password=APP_TIMESCALE_ROLE_PASSWORD, driver="postgresql")
    )
    if tenant is not None:
        await conn.execute(f"SELECT set_config('app.current_tenant_id', '{tenant}', false)")
    return conn


async def _insert(db, tenant: uuid.UUID, stamps: list[datetime]) -> None:
    conn = await _admin(db)
    try:
        asset = uuid.uuid4()
        await conn.copy_records_to_table(
            "sensor_readings",
            records=[(tenant, asset, "rpm", 1.0, "x", ts) for ts in stamps],
            columns=["tenant_id", "asset_id", "sensor_type", "value", "unit", "timestamp"],
        )
    finally:
        await conn.close()


async def _count(db, tenant: uuid.UUID) -> int:
    conn = await _admin(db)
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM sensor_readings WHERE tenant_id = $1", tenant
        )
    finally:
        await conn.close()


async def _run_retention_job(db) -> None:
    conn = await _admin(db)
    try:
        jobs = await conn.fetch(JOBS_SQL)
        assert len(jobs) == 1, "expected exactly one retention job"
        await conn.execute(f"CALL run_job({jobs[0]['job_id']})")
    finally:
        await conn.close()


def _alembic(db, *args: str) -> None:
    env = os.environ.copy()
    env.update(
        TIMESCALE_HOST=db.get_container_host_ip(),
        TIMESCALE_PORT=str(db.get_exposed_port(5432)),
        TIMESCALE_DB=db.dbname,
        TIMESCALE_USER=db.username,
        TIMESCALE_PASSWORD=db.password,
        APP_TIMESCALE_PASSWORD=APP_TIMESCALE_ROLE_PASSWORD,
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic_timescale.ini", *args],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture(autouse=True)
async def clean(migrated_timescale_db):
    conn = await _admin(migrated_timescale_db)
    try:
        await conn.execute("TRUNCATE sensor_readings")
    finally:
        await conn.close()


# ---- the policy is registered, once -------------------------------------------------------------


async def test_exactly_one_14_day_retention_policy_is_registered_and_the_upgrade_is_repeatable(
    migrated_timescale_db,
) -> None:
    conn = await _admin(migrated_timescale_db)
    try:
        assert [r["drop_after"] for r in await conn.fetch(JOBS_SQL)] == ["14 days"]
        # the migration's own statement, run again: a no-op, never a second job
        await conn.execute(
            "SELECT add_retention_policy('sensor_readings', INTERVAL '14 days', "
            "if_not_exists => true)"
        )
        assert len(await conn.fetch(JOBS_SQL)) == 1
    finally:
        await conn.close()


async def test_the_first_cleanup_is_scheduled_after_the_deploy_not_during_it(
    migrated_timescale_db,
) -> None:
    """An immediate first run would delete old rows the moment the migration finishes (and it did
    drop fixed-date fixture rows from other suites' tests while they were running)."""
    conn = await _admin(migrated_timescale_db)
    try:
        (job,) = await conn.fetch(JOBS_SQL)
        in_future = await conn.fetchval(
            "SELECT next_start > now() + INTERVAL '30 minutes' "
            "FROM timescaledb_information.job_stats WHERE job_id = $1",
            job["job_id"],
        )
    finally:
        await conn.close()

    assert in_future is True


# ---- RLS and grants are exactly as before -------------------------------------------------------


async def test_the_migration_leaves_rls_forced_and_the_policy_intact(
    migrated_timescale_db,
) -> None:
    conn = await _admin(migrated_timescale_db)
    try:
        flags = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'sensor_readings'"
        )
        policies = await conn.fetch(
            "SELECT policyname, qual, with_check FROM pg_policies "
            "WHERE tablename = 'sensor_readings'"
        )
    finally:
        await conn.close()

    assert (flags["relrowsecurity"], flags["relforcerowsecurity"]) == (True, True)
    assert [p["policyname"] for p in policies] == [POLICY]
    expected = "current_setting('app.current_tenant_id'"
    assert expected in policies[0]["qual"] and expected in policies[0]["with_check"]


async def test_rls_still_hides_other_tenants_rows_and_rejects_foreign_inserts(
    migrated_timescale_db,
) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    await _insert(migrated_timescale_db, tenant_a, [_ago(hours=1)])
    await _insert(migrated_timescale_db, tenant_b, [_ago(hours=1), _ago(hours=2)])

    as_a = await _app(migrated_timescale_db, tenant_a)
    unscoped = await _app(migrated_timescale_db, None)
    try:
        assert await as_a.fetchval("SELECT count(*) FROM sensor_readings") == 1
        assert await unscoped.fetchval("SELECT count(*) FROM sensor_readings") == 0
        with pytest.raises(asyncpg.exceptions.PostgresError):
            await as_a.execute(
                "INSERT INTO sensor_readings (tenant_id, asset_id, sensor_type, value, unit, "
                "timestamp) VALUES ($1, $2, 'rpm', 1, 'x', now())",
                tenant_b,
                uuid.uuid4(),
            )
    finally:
        await as_a.close()
        await unscoped.close()


async def test_the_runtime_role_still_has_only_select_and_insert(migrated_timescale_db) -> None:
    conn = await _admin(migrated_timescale_db)
    try:
        privileges = {
            r["privilege_type"]
            for r in await conn.fetch(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE grantee = 'app_role' AND table_name = 'sensor_readings'"
            )
        }
    finally:
        await conn.close()

    assert privileges == {"SELECT", "INSERT"}


async def test_the_runtime_role_cannot_delete_readings_or_manage_the_retention_policy(
    migrated_timescale_db,
) -> None:
    """Cleanup is a database job, never something an API user (the app role) can trigger."""
    tenant = uuid.uuid4()
    await _insert(migrated_timescale_db, tenant, [_ago(days=40)])
    app = await _app(migrated_timescale_db, tenant)
    try:
        for statement in (
            "DELETE FROM sensor_readings",
            "TRUNCATE sensor_readings",
            "SELECT remove_retention_policy('sensor_readings')",
            "SELECT add_retention_policy('sensor_readings', INTERVAL '1 day', "
            "if_not_exists => true)",
            "SELECT drop_chunks('sensor_readings', older_than => INTERVAL '1 day')",
        ):
            with pytest.raises(asyncpg.exceptions.PostgresError):
                await app.execute(statement)
    finally:
        await app.close()

    assert await _count(migrated_timescale_db, tenant) == 1


# ---- the cleanup itself -------------------------------------------------------------------------


async def test_old_chunks_are_dropped_and_recent_data_is_kept_for_every_tenant(
    migrated_timescale_db,
) -> None:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    # 30 and 25 days old always sit in 7-day chunks that ended more than 14 days ago
    await _insert(migrated_timescale_db, tenant_a, [_ago(days=30), _ago(days=25), _ago(days=1)])
    await _insert(migrated_timescale_db, tenant_b, [_ago(days=30), _ago(hours=2), _ago(minutes=1)])

    await _run_retention_job(migrated_timescale_db)

    assert await _count(migrated_timescale_db, tenant_a) == 1
    assert await _count(migrated_timescale_db, tenant_b) == 2
    conn = await _admin(migrated_timescale_db)
    try:
        oldest = await conn.fetchval("SELECT min(timestamp) FROM sensor_readings")
    finally:
        await conn.close()
    assert oldest > _ago(days=2)


async def test_retention_is_by_time_not_by_tenant_so_a_busy_tenant_cannot_shield_old_data(
    migrated_timescale_db,
) -> None:
    quiet, busy = uuid.uuid4(), uuid.uuid4()
    await _insert(migrated_timescale_db, quiet, [_ago(days=40)])
    await _insert(
        migrated_timescale_db, busy, [_ago(days=40)] + [_ago(minutes=m) for m in range(50)]
    )

    await _run_retention_job(migrated_timescale_db)

    assert await _count(migrated_timescale_db, quiet) == 0
    assert await _count(migrated_timescale_db, busy) == 50


async def test_a_recent_only_table_is_untouched_by_the_job(migrated_timescale_db) -> None:
    tenant = uuid.uuid4()
    await _insert(migrated_timescale_db, tenant, [_ago(days=3), _ago(days=1), _ago(hours=1)])

    await _run_retention_job(migrated_timescale_db)

    assert await _count(migrated_timescale_db, tenant) == 3


# ---- the downgrade ------------------------------------------------------------------------------


async def test_downgrade_removes_only_the_policy_and_the_upgrade_restores_it(
    migrated_timescale_db,
) -> None:
    tenant = uuid.uuid4()
    await _insert(migrated_timescale_db, tenant, [_ago(days=40), _ago(hours=1)])

    _alembic(migrated_timescale_db, "downgrade", "0003")
    try:
        conn = await _admin(migrated_timescale_db)
        try:
            jobs = len(await conn.fetch(JOBS_SQL))
            forced = await conn.fetchval(
                "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'sensor_readings'"
            )
            policy = await conn.fetchval(
                "SELECT count(*) FROM pg_policies WHERE policyname = $1", POLICY
            )
        finally:
            await conn.close()
        assert (jobs, forced, policy) == (0, True, 1)  # job gone; table, rows and RLS stay
        assert await _count(migrated_timescale_db, tenant) == 2
    finally:
        _alembic(migrated_timescale_db, "upgrade", "head")

    conn = await _admin(migrated_timescale_db)
    try:
        assert len(await conn.fetch(JOBS_SQL)) == 1
    finally:
        await conn.close()
