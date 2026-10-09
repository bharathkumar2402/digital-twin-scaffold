"""RLS isolation test for `agent_runs` (PROJECT_PLAN.md §4.4, §5, Issue #30).

Enforces tenant-isolation boundary at the PostgreSQL RLS policy level for agent_runs:
A non-BYPASSRLS database role scoped via `app.current_tenant_id` session GUC must never
read or insert another tenant's agent execution logs or state snapshots.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest
from testcontainers.postgres import PostgresContainer

BACKEND_DIR = Path(__file__).resolve().parents[2]

APP_ROLE = "app_test_role_agent_runs"
APP_ROLE_PASSWORD = "app_test_password_agent_runs"


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
async def tenants_and_facilities(migrated_db: PostgresContainer):
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
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON tenants, facilities TO {APP_ROLE}"
        )
        await admin_conn.execute(f"GRANT SELECT, INSERT ON agent_runs TO {APP_ROLE}")

        tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
        facility_a, facility_b = uuid.uuid4(), uuid.uuid4()
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
        yield tenant_a, tenant_b, facility_a, facility_b
    finally:
        await admin_conn.execute("DELETE FROM agent_runs")
        await admin_conn.execute("DELETE FROM facilities")
        await admin_conn.execute("DELETE FROM tenants")
        await admin_conn.close()


async def _scoped_connection(pg: PostgresContainer, tenant_id: uuid.UUID) -> asyncpg.Connection:
    conn = await asyncpg.connect(_dsn(pg, APP_ROLE, APP_ROLE_PASSWORD))
    await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id))
    return conn


async def _insert_agent_run(
    conn: asyncpg.Connection,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    trigger: str = "scheduled",
    status: str = "completed",
    duration_ms: int = 1500,
) -> uuid.UUID:
    row = await conn.fetchrow(
        """
        INSERT INTO agent_runs
        (tenant_id, facility_id, trigger, status, duration_ms, state_snapshot_json, validation_errors_json, decision_report, confidence)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING id
        """,
        tenant_id,
        facility_id,
        trigger,
        status,
        duration_ms,
        json.dumps({"facility_id": str(facility_id)}),
        json.dumps([]),
        "Decision executive report",
        0.95,
    )
    return row["id"]


async def test_tenant_cannot_read_another_tenants_agent_runs(
    migrated_db: PostgresContainer, tenants_and_facilities
) -> None:
    tenant_a, tenant_b, facility_a, facility_b = tenants_and_facilities
    conn_a = await _scoped_connection(migrated_db, tenant_a)
    conn_b = await _scoped_connection(migrated_db, tenant_b)
    try:
        run_a = await _insert_agent_run(conn_a, tenant_id=tenant_a, facility_id=facility_a)
        run_b = await _insert_agent_run(conn_b, tenant_id=tenant_b, facility_id=facility_b)

        rows_a = await conn_a.fetch("SELECT id FROM agent_runs")
        ids_seen_by_a = {r["id"] for r in rows_a}
        assert run_a in ids_seen_by_a
        assert run_b not in ids_seen_by_a

        rows_b = await conn_b.fetch("SELECT id FROM agent_runs")
        ids_seen_by_b = {r["id"] for r in rows_b}
        assert run_b in ids_seen_by_b
        assert run_a not in ids_seen_by_b
    finally:
        await conn_a.close()
        await conn_b.close()


async def test_tenant_cannot_insert_agent_run_for_another_tenant(
    migrated_db: PostgresContainer, tenants_and_facilities
) -> None:
    tenant_a, tenant_b, facility_a, _ = tenants_and_facilities
    conn_a = await _scoped_connection(migrated_db, tenant_a)
    try:
        # Tenant A attempting to insert row with tenant_id = Tenant B
        with pytest.raises(asyncpg.PostgresError) as exc_info:
            await _insert_agent_run(conn_a, tenant_id=tenant_b, facility_id=facility_a)
        # RLS check policy violation
        assert "policy" in str(exc_info.value).lower() or "check" in str(exc_info.value).lower()
    finally:
        await conn_a.close()


async def test_empty_tenant_guc_sees_zero_agent_runs(
    migrated_db: PostgresContainer, tenants_and_facilities
) -> None:
    tenant_a, _, facility_a, _ = tenants_and_facilities
    conn_a = await _scoped_connection(migrated_db, tenant_a)
    await _insert_agent_run(conn_a, tenant_id=tenant_a, facility_id=facility_a)
    await conn_a.close()

    unscoped = await asyncpg.connect(_dsn(migrated_db, APP_ROLE, APP_ROLE_PASSWORD))
    try:
        rows = await unscoped.fetch("SELECT id FROM agent_runs")
        assert len(rows) == 0

        # Also test explicit empty string
        await unscoped.execute("SELECT set_config('app.current_tenant_id', '', false)")
        rows_empty = await unscoped.fetch("SELECT id FROM agent_runs")
        assert len(rows_empty) == 0
    finally:
        await unscoped.close()
