"""RLS isolation test for the `users` and `facilities` tables.

This is the DB-level guarantee for Phase 1 session 1 (schema & migrations): a
Postgres role with no BYPASSRLS, scoped only via the `app.current_tenant_id`
session GUC, can never see or write another tenant's rows. It intentionally
does not go through the app/auth layer — that HTTP-level template is built in
Phase 1 session 3 (tenant-context middleware) on top of this guarantee.
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

APP_ROLE = "app_test_role"
APP_ROLE_PASSWORD = "app_test_password"


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
    """Creates a low-privilege, non-BYPASSRLS role and two tenant rows."""
    admin_conn = await asyncpg.connect(
        _asyncpg_dsn(migrated_db, migrated_db.username, migrated_db.password)
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
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON tenants, users, facilities TO {APP_ROLE}"
        )

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
        await admin_conn.execute("DELETE FROM facilities")
        await admin_conn.execute("DELETE FROM tenants")
        await admin_conn.close()


async def _scoped_connection(pg: PostgresContainer, tenant_id: uuid.UUID) -> asyncpg.Connection:
    conn = await asyncpg.connect(_asyncpg_dsn(pg, APP_ROLE, APP_ROLE_PASSWORD))
    await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id))
    return conn


@pytest.mark.parametrize("table", ["users", "facilities"])
async def test_tenant_cannot_read_other_tenants_rows(migrated_db, tenants, table):
    tenant_a, tenant_b = tenants
    conn_a = await _scoped_connection(migrated_db, tenant_a)
    conn_b = await _scoped_connection(migrated_db, tenant_b)
    try:
        if table == "users":
            await conn_a.execute(
                "INSERT INTO users (tenant_id, email, role, hashed_password) "
                "VALUES ($1, $2, 'viewer', 'x')",
                tenant_a,
                "a@example.com",
            )
            await conn_b.execute(
                "INSERT INTO users (tenant_id, email, role, hashed_password) "
                "VALUES ($1, $2, 'viewer', 'x')",
                tenant_b,
                "b@example.com",
            )
        else:
            await conn_a.execute(
                "INSERT INTO facilities (tenant_id, name) VALUES ($1, $2)", tenant_a, "Plant A"
            )
            await conn_b.execute(
                "INSERT INTO facilities (tenant_id, name) VALUES ($1, $2)", tenant_b, "Plant B"
            )

        rows_a = await conn_a.fetch(f"SELECT * FROM {table}")
        rows_b = await conn_b.fetch(f"SELECT * FROM {table}")

        assert {r["tenant_id"] for r in rows_a} == {tenant_a}
        assert {r["tenant_id"] for r in rows_b} == {tenant_b}
    finally:
        await conn_a.close()
        await conn_b.close()


@pytest.mark.parametrize("table", ["users", "facilities"])
async def test_tenant_cannot_insert_row_for_another_tenant(migrated_db, tenants, table):
    tenant_a, tenant_b = tenants
    conn_a = await _scoped_connection(migrated_db, tenant_a)
    try:
        with pytest.raises(asyncpg.exceptions.PostgresError):
            if table == "users":
                await conn_a.execute(
                    "INSERT INTO users (tenant_id, email, role, hashed_password) "
                    "VALUES ($1, $2, 'viewer', 'x')",
                    tenant_b,
                    "sneaky@example.com",
                )
            else:
                await conn_a.execute(
                    "INSERT INTO facilities (tenant_id, name) VALUES ($1, $2)",
                    tenant_b,
                    "Sneaky Plant",
                )
    finally:
        await conn_a.close()


async def test_unscoped_session_sees_no_rows(migrated_db, tenants):
    tenant_a, _ = tenants
    conn = await asyncpg.connect(_asyncpg_dsn(migrated_db, APP_ROLE, APP_ROLE_PASSWORD))
    try:
        scoped = await _scoped_connection(migrated_db, tenant_a)
        await scoped.execute(
            "INSERT INTO users (tenant_id, email, role, hashed_password) "
            "VALUES ($1, $2, 'viewer', 'x')",
            tenant_a,
            "a@example.com",
        )
        await scoped.close()

        rows = await conn.fetch("SELECT * FROM users")
        assert rows == []
    finally:
        await conn.close()
