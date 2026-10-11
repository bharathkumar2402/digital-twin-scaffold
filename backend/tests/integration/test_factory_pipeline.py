# ruff: noqa: F811 - pytest fixtures imported from the sibling module and re-requested by name
"""The whole Meridian plant (3 floors, 44 machines, 60 links) through the real agent pipeline.

The plant specification (`scripts/demo_factory.py`) is loaded into real Postgres/Timescale under
RLS exactly as the demo does, then the 5 agents run on it. Cascades must cross floors, name only
real machines, and technician routes must pay for changing floors.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from testcontainers.postgres import PostgresContainer

from scripts.demo_factory import (
    BY_KEY,
    FLOORS,
    LINKS,
    MACHINES,
    downstream_of,
    tile_prefix,
)
from tests.integration.test_risk_inference_service import (  # noqa: F401 - fixtures + helpers
    _dsn,
    _insert_ai4i_snapshot,
    _make_facility,
    _make_tenant,
    _scoped,
    fake_model,
    main_pg_container,
    main_session_factory,
    migrated_main_db,
    migrated_timescale_db,
    timescale_pg_container,
    timescale_session_factory,
)


@pytest.fixture(autouse=True)
def use_test_databases(
    monkeypatch: pytest.MonkeyPatch,
    main_session_factory: async_sessionmaker,
    timescale_session_factory: async_sessionmaker,
) -> None:
    from app.services import agent_context_service

    monkeypatch.setattr(agent_context_service, "async_session_factory", main_session_factory)
    monkeypatch.setattr(
        agent_context_service, "timescale_session_factory", timescale_session_factory
    )


async def _load_plant(
    main_db: PostgresContainer,
    ts_db: PostgresContainer,
    factory: async_sessionmaker,
) -> tuple[uuid.UUID, uuid.UUID, dict[str, uuid.UUID]]:
    from app.services.asset_dependency_service import create_dependency
    from app.services.asset_service import create_asset

    tenant = await _make_tenant(main_db, "Tenant Meridian")
    facility = await _make_facility(main_db, tenant_id=tenant, name="Meridian Components Plant")

    conn = await asyncpg.connect(
        _dsn(main_db, user=main_db.username, password=main_db.password, driver="postgresql")
    )
    try:  # one finished map per floor first: assets are validated against the facility's floors
        for floor in FLOORS:
            await conn.execute(
                "INSERT INTO facility_map_uploads (id, tenant_id, facility_id, original_filename, "
                "storage_key, format, status, tile_prefix, floor_level, floor_name, created_at) "
                "VALUES ($1,$2,$3,'p.png','k','png','tiled',$4,$5,$6,$7)",
                uuid.uuid4(),
                tenant,
                facility,
                tile_prefix(floor.level),
                floor.level,
                floor.name,
                datetime.now(UTC),
            )
    finally:
        await conn.close()

    ids: dict[str, uuid.UUID] = {}
    for m in MACHINES:
        session = await _scoped(factory, tenant)
        try:
            asset = await create_asset(
                session,
                tenant_id=tenant,
                facility_id=facility,
                name=m.name,
                type=m.type,
                floor_level=m.floor,
                x=float(m.x),
                y=float(m.y),
                status_="operational",
                installed_date=None,
                manufacturer=None,
                model=None,
            )
            ids[m.key] = asset.id
        finally:
            await session.close()
        await _insert_ai4i_snapshot(ts_db, tenant_id=tenant, asset_id=ids[m.key])

    for dependent, upstream, _ in LINKS:
        session = await _scoped(factory, tenant)
        try:
            await create_dependency(
                session,
                tenant_id=tenant,
                facility_id=facility,
                parent_asset_id=ids[dependent],
                child_asset_id=ids[upstream],
            )
        finally:
            await session.close()
    return tenant, facility, ids


async def _run(
    factory: async_sessionmaker, tenant: uuid.UUID, facility: uuid.UUID, query: str
) -> Any:
    from app.services.agent_run_service import run_pipeline_for_facility

    session = await _scoped(factory, tenant)
    try:
        _, run = await run_pipeline_for_facility(
            session,
            tenant_id=tenant,
            facility_id=facility,
            trigger="user_query",
            user_query=query,
        )
        return run
    finally:
        await session.close()


async def test_the_whole_plant_runs_through_the_pipeline_and_cascades_cross_floors(
    migrated_main_db: PostgresContainer,
    migrated_timescale_db: PostgresContainer,
    main_session_factory: async_sessionmaker,
) -> None:
    tenant, facility, ids = await _load_plant(
        migrated_main_db, migrated_timescale_db, main_session_factory
    )
    key_of = {str(v): k for k, v in ids.items()}

    # --- a failing compressor on the ground floor stops the assembly line one floor up ---------
    run = await _run(main_session_factory, tenant, facility, "What if Air Compressor AC-1 fails?")
    assert run.status == "completed", run.state_snapshot_json.get("escalation_reason")
    state = run.state_snapshot_json
    assert len(state["risk_scores"]) == 44  # every machine on every floor was scored
    sim = state["simulation_result"]
    assert key_of[sim["root_cause_asset_id"]] == "AC-1"
    stopped = {key_of[a] for a in sim["downstream_shutoff_asset_ids"]}
    assert stopped == downstream_of("AC-1")  # exactly the spec's cascade, no more, no less
    assert {BY_KEY[k].floor for k in stopped} == {1}  # ground-floor cause, first-floor effect
    assert sim["total_affected_assets"] == 1 + len(downstream_of("AC-1"))

    # nothing in the whole run refers to a machine outside the plant
    seen = {str(s["asset_id"]) for s in state["risk_scores"]}
    seen |= {str(t["asset_id"]) for t in state["maintenance_schedule"]}
    seen |= {str(sp["asset_id"]) for r in state["dispatch_routes"] for sp in r["assigned_stops"]}
    assert seen <= set(key_of)

    # --- the main switchgear on the roof is a plant-wide event across all three floors ---------
    run = await _run(main_session_factory, tenant, facility, "What if Main Switchgear SG-1 fails?")
    assert run.status == "completed"
    sim = run.state_snapshot_json["simulation_result"]
    assert key_of[sim["root_cause_asset_id"]] == "SG-1"
    stopped = {key_of[a] for a in sim["downstream_shutoff_asset_ids"]}
    assert stopped == downstream_of("SG-1")
    assert {BY_KEY[k].floor for k in stopped} == {0, 1, 2}
    assert sim["estimated_downtime_hours"] > 0


async def test_technician_routes_pay_for_changing_floors(
    migrated_main_db: PostgresContainer,
    migrated_timescale_db: PostgresContainer,
    main_session_factory: async_sessionmaker,
) -> None:
    """Real asset data from the database, one job on each floor: the route must charge for the
    lift/stairs, and must cost more than the very same jobs with the floors ignored."""
    from app.agents.route_optimization import real_route_optimization_callable
    from app.agents.tools.distance_matrix_tool import FLOOR_TRANSIT_MINUTES
    from app.services.agent_context_service import real_pipeline_config

    tenant, facility, ids = await _load_plant(
        migrated_main_db, migrated_timescale_db, main_session_factory
    )
    floor_of = {str(v): BY_KEY[k].floor for k, v in ids.items()}
    async with real_pipeline_config(tenant_id=tenant, facility_id=facility) as config:
        cfg = config["configurable"]
        assets, meters_per_pixel = cfg["assets"], cfg["meters_per_pixel"]
    assert {a["floor_level"] for a in assets} == {0, 1, 2}  # the floors came out of the database

    def tasks() -> list[dict[str, Any]]:
        return [
            {
                "item_id": f"job-{key}",
                "asset_id": str(ids[key]),
                "scheduled_date": "2026-10-12",
                "priority": "high",
                "estimated_duration_hours": 0.5,
                "required_technician_skills": [],
            }
            for key in ("AC-1", "R-1", "AHU-1")  # ground, first, roof
        ]

    async def route(with_floors: bool) -> dict[str, Any]:
        used = assets if with_floors else [{**a, "floor_level": 0} for a in assets]
        return await real_route_optimization_callable(
            {
                "facility_id": str(facility),
                "maintenance_schedule": tasks(),
                "assets": used,
                "meters_per_pixel": meters_per_pixel,
                "time_limit_seconds": 2.0,
            }
        )

    multi, flat = await route(True), await route(False)
    stops = [sp for r in multi["technician_routes"] for sp in r["assigned_stops"]]
    assert {str(sp["asset_id"]) for sp in stops} == {str(ids[k]) for k in ("AC-1", "R-1", "AHU-1")}

    for r in multi["technician_routes"]:
        previous = 0  # technicians start at the ground-floor depot
        for sp in r["assigned_stops"]:
            level = floor_of[str(sp["asset_id"])]
            if level != previous:  # every floor change is charged at least the lift/stairs time
                changed = abs(level - previous)
                assert (
                    sp["travel_time_from_previous_minutes"] >= changed * FLOOR_TRANSIT_MINUTES - 0.1
                )
            previous = level

    def travel(out: dict[str, Any]) -> float:
        return sum(r["total_travel_minutes"] for r in out["technician_routes"])

    # up to the roof and back down costs at least four floor changes (0 -> 1 -> 2 -> 0 = 4 floors)
    assert travel(multi) >= travel(flat) + 4 * FLOOR_TRANSIT_MINUTES - 0.5
