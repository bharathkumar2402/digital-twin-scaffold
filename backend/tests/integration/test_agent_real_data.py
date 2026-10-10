# ruff: noqa: F811 - pytest fixtures imported from the sibling module and re-requested by name
"""The production agent path runs on the facility's REAL data (real Postgres/Timescale, RLS).

Before this wiring, `POST /agent-runs` passed no data to the agents, so each fell back to
hardcoded sample fixtures with random asset ids. These tests run `run_pipeline_for_facility`
and check that nothing in the output is invented.
"""

import uuid
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from testcontainers.postgres import PostgresContainer

from tests.integration.test_risk_inference_service import (  # noqa: F401 - fixtures + helpers
    _insert_ai4i_snapshot,
    _make_asset,
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

QUERY = "What if Primary Feed Pump 7 fails?"


@pytest.fixture(autouse=True)
def use_test_databases(
    monkeypatch: pytest.MonkeyPatch,
    main_session_factory: async_sessionmaker,
    timescale_session_factory: async_sessionmaker,
) -> None:
    """Point the run config builder at the test containers (app_role, RLS enforced)."""
    from app.services import agent_context_service

    monkeypatch.setattr(agent_context_service, "async_session_factory", main_session_factory)
    monkeypatch.setattr(
        agent_context_service, "timescale_session_factory", timescale_session_factory
    )


async def _plant(
    main_db: PostgresContainer, ts_db: PostgresContainer, factory: async_sessionmaker, name: str
) -> tuple[uuid.UUID, uuid.UUID, dict[str, uuid.UUID]]:
    """Tenant + facility + pump/exchanger/turbine/fan. A parent DEPENDS on its child, so the
    pump failing cascades to the exchanger and fan, and through the exchanger to the turbine."""
    from app.services.asset_dependency_service import create_dependency

    tenant = await _make_tenant(main_db, name)
    facility = await _make_facility(main_db, tenant_id=tenant, name=f"{name} Plant")
    ids = {
        n: await _make_asset(main_db, tenant_id=tenant, facility_id=facility, name=n)
        for n in ("Primary Feed Pump 7", "Heat Exchanger Alpha", "Turbine Gen 1", "Cooling Fan 2")
    }
    for asset in ids.values():
        await _insert_ai4i_snapshot(ts_db, tenant_id=tenant, asset_id=asset)
    for parent, child in (
        ("Heat Exchanger Alpha", "Primary Feed Pump 7"),
        ("Turbine Gen 1", "Heat Exchanger Alpha"),
        ("Cooling Fan 2", "Primary Feed Pump 7"),
    ):
        session = await _scoped(factory, tenant)
        try:
            await create_dependency(
                session,
                tenant_id=tenant,
                facility_id=facility,
                parent_asset_id=ids[parent],
                child_asset_id=ids[child],
            )
        finally:
            await session.close()
    return tenant, facility, ids


async def _run(
    factory: async_sessionmaker, tenant: uuid.UUID, facility: uuid.UUID, query: str | None
) -> Any:
    from app.services.agent_run_service import run_pipeline_for_facility

    session = await _scoped(factory, tenant)
    try:
        _, run = await run_pipeline_for_facility(
            session,
            tenant_id=tenant,
            facility_id=facility,
            trigger="user_query" if query else "scheduled",
            user_query=query,
        )
        return run
    finally:
        await session.close()


def _all_asset_ids(state: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for score in state.get("risk_scores") or []:
        found.add(str(score["asset_id"]))
    for task in state.get("maintenance_schedule") or []:
        found.add(str(task["asset_id"]))
    for route in state.get("dispatch_routes") or []:
        for stop in route.get("assigned_stops", []):
            if stop.get("asset_id"):
                found.add(str(stop["asset_id"]))
    sim = state.get("simulation_result") or {}
    for key in ("root_cause_asset_id",):
        if sim.get(key):
            found.add(str(sim[key]))
    for key in ("directly_affected_asset_ids", "downstream_shutoff_asset_ids"):
        found.update(str(a) for a in sim.get(key, []))
    return found


async def test_run_analyses_the_real_facility_and_invents_nothing(
    migrated_main_db: PostgresContainer,
    migrated_timescale_db: PostgresContainer,
    main_session_factory: async_sessionmaker,
) -> None:
    tenant, facility, ids = await _plant(
        migrated_main_db, migrated_timescale_db, main_session_factory, "Tenant Real A"
    )
    run = await _run(main_session_factory, tenant, facility, QUERY)

    assert run.status == "completed", run.state_snapshot_json.get("escalation_reason")
    state = run.state_snapshot_json
    real = {str(i) for i in ids.values()}
    seen = _all_asset_ids(state)
    assert seen and seen <= real, f"ids not in the facility: {seen - real}"

    sim = state["simulation_result"]
    assert sim["root_cause_asset_id"] == str(ids["Primary Feed Pump 7"])
    downstream = set(sim["downstream_shutoff_asset_ids"])
    assert str(ids["Heat Exchanger Alpha"]) in downstream
    assert str(ids["Turbine Gen 1"]) in downstream  # reached through the exchanger
    assert str(ids["Cooling Fan 2"]) in downstream
    assert len(state["risk_scores"]) == 4  # all four real assets were scored

    # the run was recorded under RLS even though the risk agent committed its own session
    check = await _scoped(main_session_factory, tenant)
    try:
        from app.models.agent_run import AgentRun

        assert (await check.execute(select(AgentRun.id))).scalars().all() == [run.id]
    finally:
        await check.close()


async def test_what_if_naming_an_unknown_asset_halts_with_the_reason(
    migrated_main_db: PostgresContainer,
    migrated_timescale_db: PostgresContainer,
    main_session_factory: async_sessionmaker,
) -> None:
    tenant, facility, ids = await _plant(
        migrated_main_db, migrated_timescale_db, main_session_factory, "Tenant Real B"
    )
    run = await _run(main_session_factory, tenant, facility, "What if the Flux Capacitor fails?")

    assert run.status == "halted_for_escalation"
    state = run.state_snapshot_json
    reason = state["escalation_reason"]
    assert "Flux Capacitor" in reason and "Primary Feed Pump 7" in reason  # says what is known
    assert state["simulation_result"] is None  # nothing simulated for some other asset
    assert state["decision_report"] is None


async def test_empty_facility_halts_instead_of_fabricating_assets(
    migrated_main_db: PostgresContainer,
    migrated_timescale_db: PostgresContainer,
    main_session_factory: async_sessionmaker,
) -> None:
    tenant = await _make_tenant(migrated_main_db, "Tenant Real C")
    facility = await _make_facility(migrated_main_db, tenant_id=tenant, name="Empty Plant")
    run = await _run(main_session_factory, tenant, facility, None)

    assert run.status != "completed"
    state = run.state_snapshot_json
    assert _all_asset_ids(state) == set()  # not one invented asset id anywhere
    assert state["simulation_result"] is None


async def test_another_tenants_facility_is_a_404_and_runs_nothing(
    migrated_main_db: PostgresContainer,
    migrated_timescale_db: PostgresContainer,
    main_session_factory: async_sessionmaker,
) -> None:
    from app.models.agent_run import AgentRun
    from app.services.agent_run_service import run_pipeline_for_facility

    tenant_a, facility_a, _ = await _plant(
        migrated_main_db, migrated_timescale_db, main_session_factory, "Tenant Real D"
    )
    tenant_b = await _make_tenant(migrated_main_db, "Tenant Real E")

    session = await _scoped(main_session_factory, tenant_b)
    try:
        with pytest.raises(HTTPException) as err:
            await run_pipeline_for_facility(
                session, tenant_id=tenant_b, facility_id=facility_a, user_query=QUERY
            )
        assert err.value.status_code == 404
    finally:
        await session.close()

    for tenant in (tenant_a, tenant_b):  # no run was recorded for either tenant
        check = await _scoped(main_session_factory, tenant)
        try:
            assert (await check.execute(select(AgentRun.id))).scalars().all() == []
        finally:
            await check.close()


async def test_a_scoring_failure_halts_the_run_and_is_still_recorded(
    monkeypatch: pytest.MonkeyPatch,
    migrated_main_db: PostgresContainer,
    migrated_timescale_db: PostgresContainer,
    main_session_factory: async_sessionmaker,
) -> None:
    from app.services import risk_inference_service

    def boom(version: str | None) -> Any:
        raise RuntimeError("model store unreachable")

    monkeypatch.setattr(risk_inference_service, "_resolve_and_load_model", boom)
    tenant, facility, _ = await _plant(
        migrated_main_db, migrated_timescale_db, main_session_factory, "Tenant Real F"
    )
    run = await _run(main_session_factory, tenant, facility, QUERY)

    assert run.status == "halted_for_escalation"  # never reported as completed
    assert "model store unreachable" in run.state_snapshot_json["escalation_reason"]
    assert run.state_snapshot_json["simulation_result"] is None  # nothing downstream ran on air
