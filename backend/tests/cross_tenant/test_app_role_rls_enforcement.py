"""Verifies the app's runtime DB role (`app_role`, migration 0002) actually has RLS
enforced against it, unlike the migration/admin role the app used to connect as.

Without this, the `app.current_tenant_id` GUC that `scope_session_to_tenant` sets is
pure theater: a superuser (or, on Supabase, the `postgres` role) has BYPASSRLS and
`FORCE ROW LEVEL SECURITY` does not override that. This test connects as `app_role`
directly, the same way `app/core/db.py` does at runtime, and confirms the policy from
migration 0001 actually blocks it absent a matching GUC.
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
        # Fixed so the test can connect as app_role with a known password afterward.
        APP_DB_PASSWORD="test-app-role-password",
    )
    _run_migrations(env)
    return pg_container


def _dsn(pg: PostgresContainer, *, user: str, password: str) -> str:
    return (
        f"postgresql://{user}:{password}@{pg.get_container_host_ip()}:"
        f"{pg.get_exposed_port(5432)}/{pg.dbname}"
    )


@pytest.fixture
async def tenant_id(migrated_db: PostgresContainer):
    admin_conn = await asyncpg.connect(
        _dsn(migrated_db, user=migrated_db.username, password=migrated_db.password)
    )
    try:
        tid = uuid.uuid4()
        await admin_conn.execute(
            "INSERT INTO tenants (id, name, plan_tier) VALUES ($1, $2, $3)",
            tid,
            "Tenant",
            "standard",
        )
        yield tid
    finally:
        await admin_conn.execute("DELETE FROM users WHERE tenant_id = $1", tid)
        await admin_conn.execute("DELETE FROM tenants WHERE id = $1", tid)
        await admin_conn.close()


async def test_app_role_does_not_have_bypassrls(migrated_db: PostgresContainer):
    admin_conn = await asyncpg.connect(
        _dsn(migrated_db, user=migrated_db.username, password=migrated_db.password)
    )
    try:
        row = await admin_conn.fetchrow(
            "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = 'app_role'"
        )
        assert row is not None
        assert row["rolbypassrls"] is False
        assert row["rolsuper"] is False
    finally:
        await admin_conn.close()


async def test_app_role_with_no_guc_sees_zero_rows(migrated_db: PostgresContainer, tenant_id):
    admin_conn = await asyncpg.connect(
        _dsn(migrated_db, user=migrated_db.username, password=migrated_db.password)
    )
    try:
        await admin_conn.execute(
            "INSERT INTO users (tenant_id, email, role, hashed_password) "
            "VALUES ($1, $2, 'viewer', 'x')",
            tenant_id,
            "a@example.com",
        )
    finally:
        await admin_conn.close()

    app_conn = await asyncpg.connect(
        _dsn(migrated_db, user="app_role", password="test-app-role-password")
    )
    try:
        rows = await app_conn.fetch("SELECT * FROM users")
        assert rows == []
    finally:
        await app_conn.close()


async def test_app_role_insert_without_matching_guc_is_rejected(
    migrated_db: PostgresContainer, tenant_id
):
    app_conn = await asyncpg.connect(
        _dsn(migrated_db, user="app_role", password="test-app-role-password")
    )
    try:
        with pytest.raises(asyncpg.exceptions.PostgresError):
            await app_conn.execute(
                "INSERT INTO users (tenant_id, email, role, hashed_password) "
                "VALUES ($1, $2, 'viewer', 'x')",
                tenant_id,
                "sneaky@example.com",
            )
    finally:
        await app_conn.close()


async def test_app_role_can_read_and_write_when_guc_matches(
    migrated_db: PostgresContainer, tenant_id
):
    app_conn = await asyncpg.connect(
        _dsn(migrated_db, user="app_role", password="test-app-role-password")
    )
    try:
        await app_conn.execute(
            "SELECT set_config('app.current_tenant_id', $1, false)", str(tenant_id)
        )
        await app_conn.execute(
            "INSERT INTO users (tenant_id, email, role, hashed_password) "
            "VALUES ($1, $2, 'viewer', 'x')",
            tenant_id,
            "b@example.com",
        )
        rows = await app_conn.fetch("SELECT * FROM users")
        assert {r["email"] for r in rows} == {"b@example.com"}
    finally:
        await app_conn.close()
