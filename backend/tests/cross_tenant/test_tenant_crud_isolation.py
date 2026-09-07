"""Cross-tenant RLS proof for task 1.5 (tenant CRUD + admin-initiated user creation).

The integration tests in tests/integration/test_tenants_routes.py connect with admin
DB credentials (matching the existing test_users_routes.py convention) and only prove
route-level RBAC + business logic. This file is the DB-level guarantee that actually
exercises `app_role` (NOBYPASSRLS) — the role the app connects as at runtime — the same
way test_users_facilities_rls.py does for the base schema.

The scenario under test: a superadmin bootstraps a brand-new tenant B and creates its
first user there (exactly what `create_user_as_admin`/`POST /tenants/{id}/users` does —
re-scoping the session to tenant B explicitly, since the superadmin's own JWT tenant_id
is tenant A). This proves that re-scope doesn't leak: a connection scoped to tenant A
never sees the tenant-B user, and the transaction-local `SET LOCAL` doesn't bleed across
connections/transactions.
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

APP_ROLE = "app_role"
APP_ROLE_PASSWORD = "test-app-role-password"


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
    with PostgresContainer("timescale/timescaledb:latest-pg16") as pg:
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
        # Fixed so the test can connect as app_role with a known password afterward.
        APP_DB_PASSWORD=APP_ROLE_PASSWORD,
    )
    _run_migrations(env)
    return pg_container


def _asyncpg_dsn(pg: PostgresContainer, user: str, password: str) -> str:
    return (
        f"postgresql://{user}:{password}@{pg.get_container_host_ip()}:"
        f"{pg.get_exposed_port(5432)}/{pg.dbname}"
    )


@pytest.fixture
async def tenants(migrated_db: PostgresContainer):
    """Two pre-existing tenants (A, B), created via the admin/migration role."""
    admin_conn = await asyncpg.connect(
        _asyncpg_dsn(migrated_db, migrated_db.username, migrated_db.password)
    )
    try:
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        await admin_conn.execute(
            "INSERT INTO tenants (id, name, plan_tier) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_a,
            "Tenant A",
            "standard",
            tenant_b,
            "Tenant B",
            "standard",
        )
        yield tenant_a, tenant_b
    finally:
        await admin_conn.execute("DELETE FROM users")
        await admin_conn.execute("DELETE FROM tenants")
        await admin_conn.close()


async def _app_role_connection(pg: PostgresContainer) -> asyncpg.Connection:
    return await asyncpg.connect(_asyncpg_dsn(pg, APP_ROLE, APP_ROLE_PASSWORD))


async def test_app_role_creating_a_user_in_a_different_tenant_does_not_leak(
    migrated_db: PostgresContainer, tenants
) -> None:
    """Mirrors what create_user_as_admin does: connect, SET LOCAL to the *target*
    tenant (not the caller's own), insert, commit. Then prove tenant A's session never
    sees the tenant-B row, and a fresh connection scoped to tenant B does."""
    tenant_a, tenant_b = tenants

    bootstrap_conn = await _app_role_connection(migrated_db)
    try:
        async with bootstrap_conn.transaction():
            await bootstrap_conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", str(tenant_b)
            )
            await bootstrap_conn.execute(
                "INSERT INTO users (tenant_id, email, role, hashed_password) "
                "VALUES ($1, $2, 'tenant_admin', 'x')",
                tenant_b,
                "first-admin@example.com",
            )
    finally:
        await bootstrap_conn.close()

    conn_a = await _app_role_connection(migrated_db)
    conn_b = await _app_role_connection(migrated_db)
    try:
        await conn_a.execute("SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_a))
        await conn_b.execute("SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_b))

        rows_a = await conn_a.fetch("SELECT * FROM users")
        rows_b = await conn_b.fetch("SELECT * FROM users")

        assert rows_a == []
        assert {r["email"] for r in rows_b} == {"first-admin@example.com"}
    finally:
        await conn_a.close()
        await conn_b.close()


async def test_app_role_scoped_to_tenant_a_cannot_insert_user_for_tenant_b(
    migrated_db: PostgresContainer, tenants
) -> None:
    """The RLS WITH CHECK is what forces create_user_as_admin to explicitly re-scope
    per-target-tenant in the first place — this pins that behavior at the DB level."""
    tenant_a, tenant_b = tenants

    conn = await _app_role_connection(migrated_db)
    try:
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_a))
        with pytest.raises(asyncpg.exceptions.PostgresError):
            await conn.execute(
                "INSERT INTO users (tenant_id, email, role, hashed_password) "
                "VALUES ($1, $2, 'tenant_admin', 'x')",
                tenant_b,
                "sneaky-admin@example.com",
            )
    finally:
        await conn.close()


async def test_app_role_can_read_and_write_tenants_across_all_tenants(
    migrated_db: PostgresContainer, tenants
) -> None:
    """`tenants` intentionally has no RLS policy (task 1.1) — this pins that a plain
    app_role connection (no tenant GUC set at all) can see and update every tenant row,
    which is what makes superadmin tenant-CRUD possible. Authorization for that is
    enforced at the route level (require_roles(SUPERADMIN)), not by the DB."""
    tenant_a, tenant_b = tenants

    conn = await _app_role_connection(migrated_db)
    try:
        rows = await conn.fetch("SELECT id FROM tenants ORDER BY name")
        assert {r["id"] for r in rows} == {tenant_a, tenant_b}

        await conn.execute("UPDATE tenants SET plan_tier = 'enterprise' WHERE id = $1", tenant_a)
        updated = await conn.fetchrow("SELECT plan_tier FROM tenants WHERE id = $1", tenant_a)
        assert updated["plan_tier"] == "enterprise"
    finally:
        await conn.close()
