# ruff: noqa: F811 - pytest fixtures imported from the sibling module and re-requested by name
"""Floors: GET /facilities lists them, assets and map uploads carry a level, and an asset cannot be
placed on a floor the facility does not have."""

import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
import httpx
import pytest
from testcontainers.postgres import PostgresContainer

from tests.integration.test_auth_routes import _dsn
from tests.integration.test_risk_scores_routes import (  # noqa: F401 - fixtures + helper
    _token,
    client,
    migrated_db,
    pg_container,
)


async def _conn(db: PostgresContainer) -> asyncpg.Connection:
    return await asyncpg.connect(_dsn(db, driver="postgresql"))


async def _cleanup(db: PostgresContainer) -> None:
    conn = await _conn(db)
    try:
        for table in ("facility_map_uploads", "assets", "facilities", "tenants"):
            await conn.execute(f"DELETE FROM {table}")
    finally:
        await conn.close()


async def _seed_plant(db: PostgresContainer) -> dict[str, Any]:
    """Tenant A: a 3-level facility (ground, first, roof) plus noise; tenant B: its own facility."""
    ids = {k: uuid.uuid4() for k in ("ta", "tb", "fa", "fb", "fnone")}
    conn = await _conn(db)
    try:
        await conn.execute(
            "INSERT INTO tenants (id, name, plan_tier) "
            "VALUES ($1,'A','standard'),($2,'B','standard')",
            ids["ta"],
            ids["tb"],
        )
        await conn.execute(
            "INSERT INTO facilities (id, tenant_id, name) "
            "VALUES ($1,$2,'Plant'),($3,$2,'Empty'),($4,$5,'Other')",
            ids["fa"],
            ids["ta"],
            ids["fnone"],
            ids["fb"],
            ids["tb"],
        )

        async def upload(
            facility: str, tenant: str, level: int, name: str | None, status: str, day: int
        ):
            uid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO facility_map_uploads (id, tenant_id, facility_id, original_filename, "
                "storage_key, format, status, tile_prefix, floor_level, floor_name, created_at) "
                "VALUES ($1,$2,$3,'p.png','k','png',$4,'pfx',$5,$6,$7)",
                uid,
                ids[tenant],
                ids[facility],
                status,
                level,
                name,
                datetime(2026, 1, day, tzinfo=UTC),
            )
            return uid

        ids["l0"] = await upload("fa", "ta", 0, "Ground floor", "tiled", 1)
        ids["l1_old"] = await upload("fa", "ta", 1, "First floor", "tiled", 2)
        ids["l1"] = await upload("fa", "ta", 1, "First floor", "tiled", 3)  # newest tiled wins
        ids["l1_failed"] = await upload("fa", "ta", 1, "First floor", "failed", 9)  # ignored
        ids["l2"] = await upload("fa", "ta", 2, None, "tiled", 4)  # no name -> "Level 2"
        ids["l3_pending"] = await upload("fa", "ta", 3, "Attic", "pending", 5)  # not a floor yet
        ids["b0"] = await upload("fb", "tb", 0, "B ground", "tiled", 1)

        for level, n in ((0, 3), (1, 2), (2, 1)):
            for k in range(n):
                await conn.execute(
                    "INSERT INTO assets "
                    "(id, tenant_id, facility_id, name, type, floor_level, x, y, status) "
                    "VALUES ($1,$2,$3,$4,'pump',$5,1,1,'operational')",
                    uuid.uuid4(),
                    ids["ta"],
                    ids["fa"],
                    f"A-{level}-{k}",
                    level,
                )
    finally:
        await conn.close()
    return ids


def _auth(tenant: uuid.UUID, role: str = "viewer") -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(tenant_id=tenant, role=role)}"}


async def test_facilities_list_their_floors_with_the_newest_finished_map_and_counts(
    client: httpx.AsyncClient, migrated_db: PostgresContainer
) -> None:
    ids = await _seed_plant(migrated_db)
    try:
        body = (await client.get("/facilities", headers=_auth(ids["ta"]))).json()
        plant = next(f for f in body if f["name"] == "Plant")
        assert [f["level"] for f in plant["floors"]] == [0, 1, 2]  # sorted; level 3 is only pending
        by_level = {f["level"]: f for f in plant["floors"]}
        assert by_level[1]["map_upload_id"] == str(
            ids["l1"]
        )  # not the older one, not the failed one
        assert by_level[0]["name"] == "Ground floor" and by_level[2]["name"] == "Level 2"
        assert [by_level[lvl]["asset_count"] for lvl in (0, 1, 2)] == [3, 2, 1]
        assert plant["asset_count"] == 6
        assert plant["latest_map_upload_id"] == str(ids["l0"])  # open the ground floor first
    finally:
        await _cleanup(migrated_db)


async def test_a_facility_without_a_ground_floor_opens_its_lowest_floor(
    client: httpx.AsyncClient, migrated_db: PostgresContainer
) -> None:
    ids = await _seed_plant(migrated_db)
    conn = await _conn(migrated_db)
    try:
        await conn.execute("DELETE FROM facility_map_uploads WHERE id = $1", ids["l0"])
    finally:
        await conn.close()
    try:
        plant = next(
            f
            for f in (await client.get("/facilities", headers=_auth(ids["ta"]))).json()
            if f["name"] == "Plant"
        )
        assert [f["level"] for f in plant["floors"]] == [1, 2]
        assert plant["latest_map_upload_id"] == str(ids["l1"])
    finally:
        await _cleanup(migrated_db)


async def test_a_facility_with_no_finished_map_has_no_floors_and_no_map(
    client: httpx.AsyncClient, migrated_db: PostgresContainer
) -> None:
    ids = await _seed_plant(migrated_db)
    try:
        empty = next(
            f
            for f in (await client.get("/facilities", headers=_auth(ids["ta"]))).json()
            if f["name"] == "Empty"
        )
        assert empty["floors"] == [] and empty["latest_map_upload_id"] is None
    finally:
        await _cleanup(migrated_db)


async def test_another_tenant_never_sees_these_floors(
    client: httpx.AsyncClient, migrated_db: PostgresContainer
) -> None:
    ids = await _seed_plant(migrated_db)
    try:
        body = (await client.get("/facilities", headers=_auth(ids["tb"]))).json()
        assert [f["name"] for f in body] == ["Other"]
        seen = {fl["map_upload_id"] for f in body for fl in f["floors"]}
        assert seen == {str(ids["b0"])}
        assert seen.isdisjoint({str(ids[k]) for k in ("l0", "l1", "l1_old", "l2")})
    finally:
        await _cleanup(migrated_db)


def _asset_body(floor: int | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"name": f"New-{uuid.uuid4().hex[:4]}", "type": "pump", "x": 10, "y": 20}
    if floor is not None:
        body["floor_level"] = floor
    return body


async def test_an_asset_cannot_be_placed_on_a_floor_the_facility_does_not_have(
    client: httpx.AsyncClient, migrated_db: PostgresContainer
) -> None:
    ids = await _seed_plant(migrated_db)
    admin = _auth(ids["ta"], "tenant_admin")
    try:
        ok = await client.post(
            f"/facilities/{ids['fa']}/assets", json=_asset_body(1), headers=admin
        )
        assert ok.status_code == 201 and ok.json()["floor_level"] == 1

        bad = await client.post(
            f"/facilities/{ids['fa']}/assets", json=_asset_body(7), headers=admin
        )
        assert bad.status_code == 400 and "no floor 7" in bad.text
        assert "[0, 1, 2]" in bad.text  # says which floors exist

        pending_only = await client.post(
            f"/facilities/{ids['fa']}/assets", json=_asset_body(3), headers=admin
        )
        assert (
            pending_only.status_code == 400
        )  # a floor whose map has not finished is not a floor yet
    finally:
        await _cleanup(migrated_db)


async def test_moving_an_asset_to_another_floor_is_validated_too(
    client: httpx.AsyncClient, migrated_db: PostgresContainer
) -> None:
    ids = await _seed_plant(migrated_db)
    admin = _auth(ids["ta"], "tenant_admin")
    try:
        created = (
            await client.post(f"/facilities/{ids['fa']}/assets", json=_asset_body(0), headers=admin)
        ).json()
        url = f"/facilities/{ids['fa']}/assets/{created['id']}"
        assert (await client.patch(url, json={"floor_level": 2}, headers=admin)).json()[
            "floor_level"
        ] == 2
        assert (await client.patch(url, json={"floor_level": 9}, headers=admin)).status_code == 400
        assert (await client.get(url, headers=admin)).json()[
            "floor_level"
        ] == 2  # unchanged by the bad try
        assert (
            await client.patch(url, json={"x": 5}, headers=admin)
        ).status_code == 200  # no floor: fine
    finally:
        await _cleanup(migrated_db)


async def test_a_facility_with_no_finished_map_accepts_any_floor_and_defaults_to_ground(
    client: httpx.AsyncClient, migrated_db: PostgresContainer
) -> None:
    ids = await _seed_plant(migrated_db)
    admin = _auth(ids["ta"], "tenant_admin")
    try:
        fresh = await client.post(
            f"/facilities/{ids['fnone']}/assets", json=_asset_body(), headers=admin
        )
        assert fresh.status_code == 201 and fresh.json()["floor_level"] == 0
        far = await client.post(
            f"/facilities/{ids['fnone']}/assets", json=_asset_body(4), headers=admin
        )
        assert far.status_code == 201  # nothing to validate against yet
    finally:
        await _cleanup(migrated_db)


@pytest.mark.parametrize("bad", [-11, 101, "high"])
async def test_a_nonsense_floor_level_is_rejected_at_the_boundary(
    client: httpx.AsyncClient, migrated_db: PostgresContainer, bad: Any
) -> None:
    ids = await _seed_plant(migrated_db)
    try:
        resp = await client.post(
            f"/facilities/{ids['fnone']}/assets",
            json={**_asset_body(), "floor_level": bad},
            headers=_auth(ids["ta"], "tenant_admin"),
        )
        assert resp.status_code == 422
    finally:
        await _cleanup(migrated_db)


async def test_another_tenant_cannot_place_an_asset_on_this_facilitys_floors(
    client: httpx.AsyncClient, migrated_db: PostgresContainer
) -> None:
    ids = await _seed_plant(migrated_db)
    try:
        resp = await client.post(
            f"/facilities/{ids['fa']}/assets",
            json=_asset_body(1),
            headers=_auth(ids["tb"], "tenant_admin"),
        )
        assert resp.status_code == 404  # B cannot even see A's facility
    finally:
        await _cleanup(migrated_db)


async def test_map_upload_accepts_a_floor_and_rejects_nonsense(
    client: httpx.AsyncClient, migrated_db: PostgresContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import facility_map_service

    monkeypatch.setattr(facility_map_service, "put_raw_upload", lambda key, content: None)
    monkeypatch.setattr(facility_map_service.celery_app, "send_task", lambda *a, **k: None)
    ids = await _seed_plant(migrated_db)
    admin = _auth(ids["ta"], "tenant_admin")
    pdf = {"file": ("roof.pdf", b"%PDF-1.4 minimal", "application/pdf")}
    try:
        ok = await client.post(
            f"/facilities/{ids['fa']}/map",
            files=pdf,
            data={"floor_level": "3", "floor_name": "Attic"},
            headers=admin,
        )
        assert ok.status_code == 202, ok.text
        assert ok.json()["floor_level"] == 3 and ok.json()["floor_name"] == "Attic"

        bad = await client.post(
            f"/facilities/{ids['fa']}/map", files=pdf, data={"floor_level": "500"}, headers=admin
        )
        assert bad.status_code == 422
    finally:
        await _cleanup(migrated_db)
