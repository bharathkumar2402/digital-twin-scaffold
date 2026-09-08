"""RLS isolation test for `asset_dependencies` (issue 2.7).

Same DB-level pattern as test_assets_rls.py: a non-BYPASSRLS role, scoped only via the
`app.current_tenant_id` session GUC, must never see, write, or delete another tenant's
dependency-edge rows. No UPDATE coverage here (unlike assets) - the table has no UPDATE
grant, edges are only ever created or deleted.
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

APP_ROLE = "app_test_role_asset_deps"
APP_ROLE_PASSWORD = "app_test_password_asset_deps"


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
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON tenants, facilities, assets, "
            f"asset_dependencies TO {APP_ROLE}"
        )

        tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
        facility_a, facility_b = uuid.uuid4(), uuid.uuid4()
        pump_a, tank_a = uuid.uuid4(), uuid.uuid4()
        pump_b, tank_b = uuid.uuid4(), uuid.uuid4()
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
            "INSERT INTO assets (id, tenant_id, facility_id, name, type, x, y, status) VALUES "
            "($1, $2, $3, 'Pump A', 'pump', 0, 0, 'operational'), "
            "($4, $2, $3, 'Tank A', 'tank', 1, 1, 'operational'), "
            "($5, $6, $7, 'Pump B', 'pump', 0, 0, 'operational'), "
            "($8, $6, $7, 'Tank B', 'tank', 1, 1, 'operational')",
            pump_a,
            tenant_a,
            facility_a,
            tank_a,
            pump_b,
            tenant_b,
            facility_b,
            tank_b,
        )
        yield tenant_a, tenant_b, facility_a, facility_b, pump_a, tank_a, pump_b, tank_b
    finally:
        await admin_conn.execute("DELETE FROM asset_dependencies")
        await admin_conn.execute("DELETE FROM assets")
        await admin_conn.execute("DELETE FROM facilities")
        await admin_conn.execute("DELETE FROM tenants")
        await admin_conn.close()


async def _scoped_connection(pg: PostgresContainer, tenant_id: uuid.UUID) -> asyncpg.Connection:
    conn = await asyncpg.connect(_dsn(pg, APP_ROLE, APP_ROLE_PASSWORD))
    await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id))
    return conn


async def _insert_edge(
    conn: asyncpg.Connection,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    parent_asset_id: uuid.UUID,
    child_asset_id: uuid.UUID,
) -> uuid.UUID:
    row = await conn.fetchrow(
        "INSERT INTO asset_dependencies (tenant_id, facility_id, parent_asset_id, child_asset_id) "
        "VALUES ($1, $2, $3, $4) RETURNING id",
        tenant_id,
        facility_id,
        parent_asset_id,
        child_asset_id,
    )
    return row["id"]


async def test_tenant_cannot_read_another_tenants_dependency_rows(
    migrated_db: PostgresContainer, tenants_facilities_assets
) -> None:
    tenant_a, tenant_b, facility_a, facility_b, pump_a, tank_a, pump_b, tank_b = (
        tenants_facilities_assets
    )
    conn_a = await _scoped_connection(migrated_db, tenant_a)
    conn_b = await _scoped_connection(migrated_db, tenant_b)
    try:
        edge_a = await _insert_edge(
            conn_a,
            tenant_id=tenant_a,
            facility_id=facility_a,
            parent_asset_id=pump_a,
            child_asset_id=tank_a,
        )
        await _insert_edge(
            conn_b,
            tenant_id=tenant_b,
            facility_id=facility_b,
            parent_asset_id=pump_b,
            child_asset_id=tank_b,
        )

        rows_a = await conn_a.fetch("SELECT id FROM asset_dependencies")
        rows_b = await conn_b.fetch("SELECT id FROM asset_dependencies")

        assert {r["id"] for r in rows_a} == {edge_a}
        assert edge_a not in {r["id"] for r in rows_b}
    finally:
        await conn_a.close()
        await conn_b.close()


async def test_tenant_cannot_insert_dependency_row_for_another_tenant(
    migrated_db: PostgresContainer, tenants_facilities_assets
) -> None:
    tenant_a, tenant_b, facility_a, _fb, pump_a, tank_a, _pb, _tb = tenants_facilities_assets
    conn_a = await _scoped_connection(migrated_db, tenant_a)
    try:
        with pytest.raises(asyncpg.exceptions.PostgresError):
            await _insert_edge(
                conn_a,
                tenant_id=tenant_b,
                facility_id=facility_a,
                parent_asset_id=pump_a,
                child_asset_id=tank_a,
            )
    finally:
        await conn_a.close()


async def test_unscoped_session_sees_zero_dependency_rows(
    migrated_db: PostgresContainer, tenants_facilities_assets
) -> None:
    """Fail-closed check: no GUC set at all means zero rows, not every tenant's."""
    tenant_a, _tb, facility_a, _fb, pump_a, tank_a, _pb, _tank_b = tenants_facilities_assets
    scoped = await _scoped_connection(migrated_db, tenant_a)
    await _insert_edge(
        scoped,
        tenant_id=tenant_a,
        facility_id=facility_a,
        parent_asset_id=pump_a,
        child_asset_id=tank_a,
    )
    await scoped.close()

    conn = await asyncpg.connect(_dsn(migrated_db, APP_ROLE, APP_ROLE_PASSWORD))
    try:
        rows = await conn.fetch("SELECT * FROM asset_dependencies")
        assert rows == []
    finally:
        await conn.close()


async def test_tenant_cannot_delete_another_tenants_dependency_row(
    migrated_db: PostgresContainer, tenants_facilities_assets
) -> None:
    """RLS's USING clause must make tenant B's session unable to even see tenant A's
    row to delete it - the DELETE should affect zero rows, and the row must survive."""
    tenant_a, tenant_b, facility_a, _fb, pump_a, tank_a, _pb, _tb = tenants_facilities_assets
    conn_a = await _scoped_connection(migrated_db, tenant_a)
    conn_b = await _scoped_connection(migrated_db, tenant_b)
    try:
        edge_id = await _insert_edge(
            conn_a,
            tenant_id=tenant_a,
            facility_id=facility_a,
            parent_asset_id=pump_a,
            child_asset_id=tank_a,
        )

        result = await conn_b.execute("DELETE FROM asset_dependencies WHERE id = $1", edge_id)
        assert result == "DELETE 0"

        row = await conn_a.fetchrow("SELECT id FROM asset_dependencies WHERE id = $1", edge_id)
        assert row is not None
    finally:
        await conn_a.close()
        await conn_b.close()
