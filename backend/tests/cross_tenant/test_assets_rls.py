"""RLS isolation test for `assets` (issue 2.6).

Same DB-level pattern as test_facility_map_uploads_rls.py: a non-BYPASSRLS role,
scoped only via the `app.current_tenant_id` session GUC, must never see, write,
update, or delete another tenant's asset rows. `assets` is the first tenant table
in this repo that's both updatable (drag-and-drop placement, field edits) and
deletable by end users, so unlike the read/insert-only coverage on
facility_map_uploads, this also checks that RLS's `USING` clause blocks
UPDATE/DELETE against invisible rows, not just SELECT/INSERT.
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

APP_ROLE = "app_test_role_assets"
APP_ROLE_PASSWORD = "app_test_password_assets"


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
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON tenants, facilities, assets TO {APP_ROLE}"
        )

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
        await admin_conn.execute("DELETE FROM assets")
        await admin_conn.execute("DELETE FROM facilities")
        await admin_conn.execute("DELETE FROM tenants")
        await admin_conn.close()


async def _scoped_connection(pg: PostgresContainer, tenant_id: uuid.UUID) -> asyncpg.Connection:
    conn = await asyncpg.connect(_dsn(pg, APP_ROLE, APP_ROLE_PASSWORD))
    await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id))
    return conn


async def _insert_asset(
    conn: asyncpg.Connection,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    name: str,
) -> uuid.UUID:
    row = await conn.fetchrow(
        "INSERT INTO assets (tenant_id, facility_id, name, type, x, y, status) "
        "VALUES ($1, $2, $3, 'pump', 1.0, 1.0, 'operational') RETURNING id",
        tenant_id,
        facility_id,
        name,
    )
    return row["id"]


async def test_tenant_cannot_read_another_tenants_asset_rows(
    migrated_db: PostgresContainer, tenants_and_facilities
) -> None:
    tenant_a, tenant_b, facility_a, facility_b = tenants_and_facilities
    conn_a = await _scoped_connection(migrated_db, tenant_a)
    conn_b = await _scoped_connection(migrated_db, tenant_b)
    try:
        await _insert_asset(conn_a, tenant_id=tenant_a, facility_id=facility_a, name="Pump A")
        await _insert_asset(conn_b, tenant_id=tenant_b, facility_id=facility_b, name="Pump B")

        rows_a = await conn_a.fetch("SELECT * FROM assets")
        rows_b = await conn_b.fetch("SELECT * FROM assets")

        assert {r["name"] for r in rows_a} == {"Pump A"}
        assert {r["name"] for r in rows_b} == {"Pump B"}
    finally:
        await conn_a.close()
        await conn_b.close()


async def test_tenant_cannot_insert_asset_row_for_another_tenant(
    migrated_db: PostgresContainer, tenants_and_facilities
) -> None:
    tenant_a, tenant_b, facility_a, _facility_b = tenants_and_facilities
    conn_a = await _scoped_connection(migrated_db, tenant_a)
    try:
        with pytest.raises(asyncpg.exceptions.PostgresError):
            await _insert_asset(
                conn_a, tenant_id=tenant_b, facility_id=facility_a, name="Sneaky Pump"
            )
    finally:
        await conn_a.close()


async def test_unscoped_session_sees_zero_asset_rows(
    migrated_db: PostgresContainer, tenants_and_facilities
) -> None:
    """Fail-closed check: no GUC set at all means zero rows, not every tenant's."""
    tenant_a, _tenant_b, facility_a, _facility_b = tenants_and_facilities
    scoped = await _scoped_connection(migrated_db, tenant_a)
    await _insert_asset(scoped, tenant_id=tenant_a, facility_id=facility_a, name="Pump A")
    await scoped.close()

    conn = await asyncpg.connect(_dsn(migrated_db, APP_ROLE, APP_ROLE_PASSWORD))
    try:
        rows = await conn.fetch("SELECT * FROM assets")
        assert rows == []
    finally:
        await conn.close()


async def test_tenant_cannot_update_another_tenants_asset_row(
    migrated_db: PostgresContainer, tenants_and_facilities
) -> None:
    """RLS's USING clause must make tenant B's session unable to even see tenant A's
    row to update it — the UPDATE should affect zero rows, not error, and the
    original value must survive untouched."""
    tenant_a, tenant_b, facility_a, _facility_b = tenants_and_facilities
    conn_a = await _scoped_connection(migrated_db, tenant_a)
    conn_b = await _scoped_connection(migrated_db, tenant_b)
    try:
        asset_id = await _insert_asset(
            conn_a, tenant_id=tenant_a, facility_id=facility_a, name="Pump A"
        )

        result = await conn_b.execute(
            "UPDATE assets SET x = 999.0 WHERE id = $1", asset_id
        )
        assert result == "UPDATE 0"

        row = await conn_a.fetchrow("SELECT x FROM assets WHERE id = $1", asset_id)
        assert row["x"] == 1.0
    finally:
        await conn_a.close()
        await conn_b.close()


async def test_tenant_cannot_delete_another_tenants_asset_row(
    migrated_db: PostgresContainer, tenants_and_facilities
) -> None:
    """Same USING-clause coverage as the update test, for DELETE: the row must
    survive a cross-tenant delete attempt."""
    tenant_a, tenant_b, facility_a, _facility_b = tenants_and_facilities
    conn_a = await _scoped_connection(migrated_db, tenant_a)
    conn_b = await _scoped_connection(migrated_db, tenant_b)
    try:
        asset_id = await _insert_asset(
            conn_a, tenant_id=tenant_a, facility_id=facility_a, name="Pump A"
        )

        result = await conn_b.execute("DELETE FROM assets WHERE id = $1", asset_id)
        assert result == "DELETE 0"

        row = await conn_a.fetchrow("SELECT id FROM assets WHERE id = $1", asset_id)
        assert row is not None
    finally:
        await conn_a.close()
        await conn_b.close()
