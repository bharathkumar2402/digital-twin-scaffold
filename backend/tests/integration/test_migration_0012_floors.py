"""Migration 0012 (floors) on a database that already holds data: nothing breaks, existing rows land
on the ground floor, the new constraints hold, and it can be rolled back and re-applied."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import asyncpg
import pytest
from testcontainers.postgres import PostgresContainer

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _env(pg: PostgresContainer) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        POSTGRES_HOST=pg.get_container_host_ip(),
        POSTGRES_PORT=str(pg.get_exposed_port(5432)),
        POSTGRES_DB=pg.dbname,
        POSTGRES_USER=pg.username,
        POSTGRES_PASSWORD=pg.password,
        APP_DB_PASSWORD="migration-test-app-role",
    )
    return env


def _alembic(pg: PostgresContainer, *args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=BACKEND_DIR,
        env=_env(pg),
        check=True,
        capture_output=True,
        text=True,
    )


async def _connect(pg: PostgresContainer) -> asyncpg.Connection:
    return await asyncpg.connect(
        f"postgresql://{pg.username}:{pg.password}@{pg.get_container_host_ip()}:"
        f"{pg.get_exposed_port(5432)}/{pg.dbname}"
    )


async def _columns(conn: asyncpg.Connection, table: str) -> set[str]:
    rows = await conn.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = $1", table
    )
    return {r["column_name"] for r in rows}


async def test_migration_keeps_existing_rows_on_the_ground_floor_and_is_reversible() -> None:
    tenant, facility, asset, upload = (uuid.uuid4() for _ in range(4))
    with PostgresContainer("postgres:16-alpine") as pg:
        _alembic(pg, "upgrade", "0011")  # the schema before floors
        conn = await _connect(pg)
        try:
            assert "floor_level" not in await _columns(conn, "assets")
            await conn.execute(
                "INSERT INTO tenants (id, name, plan_tier) VALUES ($1, 'T', 'standard')", tenant
            )
            await conn.execute(
                "INSERT INTO facilities (id, tenant_id, name) VALUES ($1, $2, 'F')",
                facility,
                tenant,
            )
            await conn.execute(
                "INSERT INTO assets (id, tenant_id, facility_id, name, type, x, y, status) "
                "VALUES ($1, $2, $3, 'Old pump', 'pump', 5, 6, 'operational')",
                asset,
                tenant,
                facility,
            )
            await conn.execute(
                "INSERT INTO facility_map_uploads (id, tenant_id, facility_id, original_filename, "
                "storage_key, format, status) VALUES ($1, $2, $3, 'old.pdf', 'k', 'pdf', 'tiled')",
                upload,
                tenant,
                facility,
            )

            _alembic(pg, "upgrade", "head")  # apply floors on top of existing data

            asset_row = await conn.fetchrow("SELECT floor_level FROM assets WHERE id = $1", asset)
            upload_row = await conn.fetchrow(
                "SELECT floor_level, floor_name FROM facility_map_uploads WHERE id = $1", upload
            )
            assert asset_row["floor_level"] == 0  # existing assets are on the ground floor
            assert upload_row["floor_level"] == 0 and upload_row["floor_name"] is None

            # the range constraints hold, on both tables
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute("UPDATE assets SET floor_level = 500 WHERE id = $1", asset)
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "UPDATE facility_map_uploads SET floor_level = -50 WHERE id = $1", upload
                )

            # rolling back removes exactly the new columns and keeps the data
            _alembic(pg, "downgrade", "0011")
            assert "floor_level" not in await _columns(conn, "assets")
            assert {"floor_level", "floor_name"}.isdisjoint(
                await _columns(conn, "facility_map_uploads")
            )
            assert await conn.fetchval("SELECT name FROM assets WHERE id = $1", asset) == "Old pump"

            _alembic(pg, "upgrade", "head")  # and it can be applied again
            assert await conn.fetchval("SELECT floor_level FROM assets WHERE id = $1", asset) == 0
        finally:
            await conn.close()
