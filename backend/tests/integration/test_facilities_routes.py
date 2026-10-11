# ruff: noqa: F811 - pytest fixtures imported from the sibling module and re-requested by name
"""GET /facilities: the facility picker's data, scoped to the caller's tenant."""

import uuid
from datetime import UTC, datetime

import asyncpg
import httpx
from testcontainers.postgres import PostgresContainer

from tests.integration.test_auth_routes import _dsn
from tests.integration.test_risk_scores_routes import (  # noqa: F401 - fixtures + helper
    _token,
    client,
    migrated_db,
    pg_container,
)


async def _seed(migrated_db: PostgresContainer) -> dict[str, uuid.UUID]:
    ids = {
        k: uuid.uuid4()
        for k in ("ta", "tb", "fa", "fa2", "fb", "up_old", "up_new", "up_fail", "up_b")
    }
    conn = await asyncpg.connect(_dsn(migrated_db, driver="postgresql"))
    try:
        await conn.execute(
            "INSERT INTO tenants (id, name, plan_tier) "
            "VALUES ($1,'A','standard'),($2,'B','standard')",
            ids["ta"],
            ids["tb"],
        )
        await conn.execute(
            "INSERT INTO facilities (id, tenant_id, name) VALUES "
            "($1,$2,'Alpha Plant'),($3,$2,'Zulu Plant'),($4,$5,'Bravo Plant')",
            ids["fa"],
            ids["ta"],
            ids["fa2"],
            ids["fb"],
            ids["tb"],
        )
        for name, facility in (("Pump 1", "fa"), ("Pump 2", "fa"), ("Fan", "fb")):
            tenant = ids["ta"] if facility == "fa" else ids["tb"]
            await conn.execute(
                "INSERT INTO assets (id, tenant_id, facility_id, name, type, x, y, status) "
                "VALUES ($1,$2,$3,$4,'pump',1,1,'operational')",
                uuid.uuid4(),
                tenant,
                ids[facility],
                name,
            )
        # Alpha: an old tiled map, a newer tiled map, and a newest one that failed.
        for key, facility, tenant, status, ts in (
            ("up_old", "fa", "ta", "tiled", datetime(2026, 1, 1, tzinfo=UTC)),
            ("up_new", "fa", "ta", "tiled", datetime(2026, 2, 1, tzinfo=UTC)),
            ("up_fail", "fa", "ta", "failed", datetime(2026, 3, 1, tzinfo=UTC)),
            ("up_b", "fb", "tb", "tiled", datetime(2026, 2, 1, tzinfo=UTC)),
        ):
            await conn.execute(
                "INSERT INTO facility_map_uploads (id, tenant_id, facility_id, original_filename, "
                "storage_key, format, status, tile_prefix, created_at) "
                "VALUES ($1,$2,$3,'p.pdf','k','pdf',$4,'pfx',$5)",
                ids[key],
                ids[tenant],
                ids[facility],
                status,
                ts,
            )
    finally:
        await conn.close()
    return ids


async def _cleanup(migrated_db: PostgresContainer) -> None:
    conn = await asyncpg.connect(_dsn(migrated_db, driver="postgresql"))
    try:
        for table in ("facility_map_uploads", "assets", "facilities", "tenants"):
            await conn.execute(f"DELETE FROM {table}")
    finally:
        await conn.close()


async def test_lists_only_the_callers_facilities_with_map_and_asset_count(
    client: httpx.AsyncClient, migrated_db: PostgresContainer
) -> None:
    ids = await _seed(migrated_db)
    try:
        resp = await client.get(
            "/facilities",
            headers={"Authorization": f"Bearer {_token(tenant_id=ids['ta'], role='viewer')}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [f["name"] for f in body] == ["Alpha Plant", "Zulu Plant"]  # sorted, tenant A only
        alpha, zulu = body
        assert alpha["asset_count"] == 2 and zulu["asset_count"] == 0
        # newest *tiled* map: not the older one, and not the newer one that failed
        assert alpha["latest_map_upload_id"] == str(ids["up_new"])
        assert zulu["latest_map_upload_id"] is None
    finally:
        await _cleanup(migrated_db)


async def test_another_tenant_never_sees_these_facilities_or_their_maps(
    client: httpx.AsyncClient, migrated_db: PostgresContainer
) -> None:
    ids = await _seed(migrated_db)
    try:
        resp = await client.get(
            "/facilities", headers={"Authorization": f"Bearer {_token(tenant_id=ids['tb'])}"}
        )
        body = resp.json()
        assert [f["name"] for f in body] == ["Bravo Plant"]
        assert body[0]["latest_map_upload_id"] == str(ids["up_b"])
        leaked = {str(ids["fa"]), str(ids["fa2"]), str(ids["up_new"]), str(ids["up_old"])}
        assert leaked.isdisjoint(
            {f["id"] for f in body} | {f["latest_map_upload_id"] for f in body}
        )
    finally:
        await _cleanup(migrated_db)


async def test_requires_authentication(client: httpx.AsyncClient) -> None:
    assert (await client.get("/facilities")).status_code in (401, 403)


async def test_empty_tenant_gets_an_empty_list(
    client: httpx.AsyncClient, migrated_db: PostgresContainer
) -> None:
    ids = await _seed(migrated_db)
    try:
        empty = uuid.uuid4()
        resp = await client.get(
            "/facilities", headers={"Authorization": f"Bearer {_token(tenant_id=empty)}"}
        )
        assert resp.status_code == 200 and resp.json() == []
    finally:
        await _cleanup(migrated_db)
        assert ids  # keep the seed result referenced
