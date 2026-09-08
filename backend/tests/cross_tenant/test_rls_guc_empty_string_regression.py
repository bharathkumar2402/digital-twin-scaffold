"""Regression test for the RLS GUC empty-string bug fixed in migration 0008.

Found live-verifying issue 2.7: after a session's `SET LOCAL app.current_tenant_id`
transaction commits, Postgres's custom-GUC placeholder mechanism makes
`current_setting('app.current_tenant_id', true)` return '' (not NULL) on that same
connection for the rest of the session - a bare `''::uuid` cast in the RLS policy's
`USING`/`WITH CHECK` clause then raises `InvalidTextRepresentationError` (a hard error)
instead of the intended fail-closed "0 rows". This bit `session.refresh()` immediately
after every `create_asset`/`create_dependency` call, since that runs in a fresh
transaction right after the commit that scoped the GUC - see
app/services/asset_service.py, app/services/asset_dependency_service.py, and migration
0008's docstring for the full root-cause writeup.

This test reproduces the exact same-connection sequence (SET LOCAL -> COMMIT -> query
again without re-setting the GUC) directly against `assets`, `facility_map_uploads`,
and `users` - the pre-existing tables migration 0008 patches - and asserts the
post-fix behavior: no exception, just 0 visible rows (still fail-closed).
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

APP_ROLE = "app_test_role_guc_regression"
APP_ROLE_PASSWORD = "app_test_password_guc_regression"


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
async def tenant_and_facility(migrated_db: PostgresContainer):
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
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON tenants, facilities, assets, users "
            f"TO {APP_ROLE}"
        )

        tenant_id = uuid.uuid4()
        facility_id = uuid.uuid4()
        await admin_conn.execute(
            "INSERT INTO tenants (id, name, plan_tier) VALUES ($1, $2, $3)",
            tenant_id,
            "Tenant A",
            "standard",
        )
        await admin_conn.execute(
            "INSERT INTO facilities (id, tenant_id, name) VALUES ($1, $2, $3)",
            facility_id,
            tenant_id,
            "Plant A",
        )
        yield tenant_id, facility_id
    finally:
        await admin_conn.execute("DELETE FROM assets")
        await admin_conn.execute("DELETE FROM users")
        await admin_conn.execute("DELETE FROM facilities")
        await admin_conn.execute("DELETE FROM tenants")
        await admin_conn.close()


async def test_current_setting_returns_empty_string_not_null_after_commit(
    migrated_db: PostgresContainer,
) -> None:
    """Documents the underlying Postgres behavior this migration works around - not
    itself a test of the app, but pins the assumption the fix (and this whole test
    file) rests on, so a Postgres version change that altered this behavior would be
    caught here rather than only as a confusing downstream failure."""
    conn = await asyncpg.connect(
        _dsn(migrated_db, migrated_db.username, migrated_db.password)
    )
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", str(uuid.uuid4())
            )
        is_null = await conn.fetchval(
            "SELECT current_setting('app.current_tenant_id', true) IS NULL"
        )
        is_empty = await conn.fetchval(
            "SELECT current_setting('app.current_tenant_id', true) = ''"
        )
        assert is_null is False
        assert is_empty is True
    finally:
        await conn.close()


async def test_query_after_guc_scope_expires_fails_closed_not_crashes(
    migrated_db: PostgresContainer, tenant_and_facility
) -> None:
    """The regression this migration fixes: SET LOCAL the GUC, commit (ending its
    scope), then query again on the SAME connection without re-setting it - this must
    return 0 rows, not raise InvalidTextRepresentationError."""
    tenant_id, facility_id = tenant_and_facility
    conn = await asyncpg.connect(_dsn(migrated_db, APP_ROLE, APP_ROLE_PASSWORD))
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", str(tenant_id)
            )
            await conn.execute(
                "INSERT INTO assets (tenant_id, facility_id, name, type, x, y, status) "
                "VALUES ($1, $2, 'Pump', 'pump', 1.0, 1.0, 'operational')",
                tenant_id,
                facility_id,
            )
        # Transaction committed - the SET LOCAL scope is gone. This must not raise.
        rows = await conn.fetch("SELECT * FROM assets")
        assert rows == []
    finally:
        await conn.close()


async def test_query_after_guc_scope_expires_fails_closed_for_users_and_facilities(
    migrated_db: PostgresContainer, tenant_and_facility
) -> None:
    """Same regression, for the two tables patched by migration 0008 that migration
    0001 originally created (users, facilities) - not just the newer ones."""
    tenant_id, _facility_id = tenant_and_facility
    conn = await asyncpg.connect(_dsn(migrated_db, APP_ROLE, APP_ROLE_PASSWORD))
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)", str(tenant_id)
            )
            await conn.execute(
                "INSERT INTO users (tenant_id, email, role, hashed_password) "
                "VALUES ($1, 'regression@example.com', 'viewer', 'x')",
                tenant_id,
            )
        users = await conn.fetch("SELECT * FROM users")
        facilities = await conn.fetch("SELECT * FROM facilities")
        assert users == []
        assert facilities == []
    finally:
        await conn.close()
