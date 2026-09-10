"""RLS isolation test for `risk_scores` (issue 3.3).

Same DB-level pattern as test_assets_rls.py: a non-BYPASSRLS role, scoped only via the
`app.current_tenant_id` session GUC, must never see or insert another tenant's risk
score rows. `risk_scores` is insert-only (no UPDATE/DELETE grant - see migration
0009), so unlike `assets` this only needs SELECT/INSERT coverage, not update/delete.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest
from testcontainers.postgres import PostgresContainer

BACKEND_DIR = Path(__file__).resolve().parents[2]

APP_ROLE = "app_test_role_risk_scores"
APP_ROLE_PASSWORD = "app_test_password_risk_scores"


def _run_migrations(env: dict) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
        capture_output=True,
        text=True,
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
    )
    _run_migrations(env)
    return pg_container


def _dsn(pg: PostgresContainer, user: str, password: str) -> str:
    return (
        f"postgresql://{user}:{password}@{pg.get_container_host_ip()}:"
        f"{pg.get_exposed_port(5432)}/{pg.dbname}"
    )


@pytest.fixture
async def tenants_facilities_assets(migrated_db: PostgresContainer):
    admin_conn = await asyncpg.connect(
        _dsn(migrated_db, migrated_db.username, migrated_db.password)
    )
    try:
        await admin_conn.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                    CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_ROLE_PASSWORD}' NOBYPASSRLS;
                END IF;
            END
            $$;
            """
        )
        await admin_conn.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON tenants, facilities, assets TO {APP_ROLE}"
        )
        await admin_conn.execute(f"GRANT SELECT, INSERT ON risk_scores TO {APP_ROLE}")

        tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
        facility_a, facility_b = uuid.uuid4(), uuid.uuid4()
        asset_a, asset_b = uuid.uuid4(), uuid.uuid4()
        await admin_conn.execute(
            "INSERT INTO tenants (id, name, plan_tier) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_a,
            "Tenant A",
            "standard",
            tenant_b,
            "Tenant B",
            "standard",
        )
        await admin_conn.execute(
            "INSERT INTO facilities (id, tenant_id, name) VALUES ($1, $2, $3), ($4, $5, $6)",
            facility_a,
            tenant_a,
            "Plant A",
            facility_b,
            tenant_b,
            "Plant B",
        )
        await admin_conn.execute(
            "INSERT INTO assets (id, tenant_id, facility_id, name, type, x, y, status) "
            "VALUES ($1, $2, $3, 'Pump A', 'pump', 1.0, 1.0, 'operational'), "
            "($4, $5, $6, 'Pump B', 'pump', 1.0, 1.0, 'operational')",
            asset_a,
            tenant_a,
            facility_a,
            asset_b,
            tenant_b,
            facility_b,
        )
        yield tenant_a, tenant_b, facility_a, facility_b, asset_a, asset_b
    finally:
        await admin_conn.execute("DELETE FROM risk_scores")
        await admin_conn.execute("DELETE FROM assets")
        await admin_conn.execute("DELETE FROM facilities")
        await admin_conn.execute("DELETE FROM tenants")
        await admin_conn.close()


async def _scoped_connection(pg: PostgresContainer, tenant_id: uuid.UUID) -> asyncpg.Connection:
    conn = await asyncpg.connect(_dsn(pg, APP_ROLE, APP_ROLE_PASSWORD))
    await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id))
    return conn


async def _insert_score(
    conn: asyncpg.Connection,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    asset_id: uuid.UUID,
    score: float = 42.0,
) -> uuid.UUID:
    row = await conn.fetchrow(
        "INSERT INTO risk_scores "
        "(tenant_id, facility_id, asset_id, score, model_version, factors_json) "
        "VALUES ($1, $2, $3, $4, 'v-test', '{}') RETURNING id",
        tenant_id,
        facility_id,
        asset_id,
        score,
    )
    return row["id"]


async def test_tenant_cannot_read_another_tenants_risk_score_rows(
    migrated_db: PostgresContainer, tenants_facilities_assets
) -> None:
    tenant_a, tenant_b, facility_a, facility_b, asset_a, asset_b = tenants_facilities_assets
    conn_a = await _scoped_connection(migrated_db, tenant_a)
    conn_b = await _scoped_connection(migrated_db, tenant_b)
    try:
        await _insert_score(conn_a, tenant_id=tenant_a, facility_id=facility_a, asset_id=asset_a)
        await _insert_score(conn_b, tenant_id=tenant_b, facility_id=facility_b, asset_id=asset_b)

        rows_a = await conn_a.fetch("SELECT * FROM risk_scores")
        rows_b = await conn_b.fetch("SELECT * FROM risk_scores")

        assert {r["asset_id"] for r in rows_a} == {asset_a}
        assert {r["asset_id"] for r in rows_b} == {asset_b}
    finally:
        await conn_a.close()
        await conn_b.close()


async def test_tenant_cannot_insert_risk_score_row_for_another_tenant(
    migrated_db: PostgresContainer, tenants_facilities_assets
) -> None:
    tenant_a, tenant_b, facility_a, _fb, asset_a, _ab = tenants_facilities_assets
    conn_a = await _scoped_connection(migrated_db, tenant_a)
    try:
        with pytest.raises(asyncpg.exceptions.PostgresError):
            await _insert_score(
                conn_a, tenant_id=tenant_b, facility_id=facility_a, asset_id=asset_a
            )
    finally:
        await conn_a.close()


async def test_unscoped_session_sees_zero_risk_score_rows(
    migrated_db: PostgresContainer, tenants_facilities_assets
) -> None:
    """Fail-closed check: no GUC set at all means zero rows, not every tenant's."""
    tenant_a, _tb, facility_a, _fb, asset_a, _ab = tenants_facilities_assets
    scoped = await _scoped_connection(migrated_db, tenant_a)
    await _insert_score(scoped, tenant_id=tenant_a, facility_id=facility_a, asset_id=asset_a)
    await scoped.close()

    conn = await asyncpg.connect(_dsn(migrated_db, APP_ROLE, APP_ROLE_PASSWORD))
    try:
        rows = await conn.fetch("SELECT * FROM risk_scores")
        assert rows == []
    finally:
        await conn.close()


async def test_score_outside_valid_range_rejected_by_check_constraint(
    migrated_db: PostgresContainer, tenants_facilities_assets
) -> None:
    """Defense-in-depth below the Pydantic `RiskScoreResult` bound (app layer): even a
    direct SQL insert bypassing app-level validation can't write an out-of-range
    score, thanks to migration 0009's `ck_risk_scores_score_range` CHECK constraint."""
    tenant_a, _tb, facility_a, _fb, asset_a, _ab = tenants_facilities_assets
    conn_a = await _scoped_connection(migrated_db, tenant_a)
    try:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await _insert_score(
                conn_a,
                tenant_id=tenant_a,
                facility_id=facility_a,
                asset_id=asset_a,
                score=150.0,
            )
    finally:
        await conn_a.close()
