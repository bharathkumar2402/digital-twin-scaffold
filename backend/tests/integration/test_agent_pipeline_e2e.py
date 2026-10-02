"""End-to-end integration tests for Phase 4: Full 5-Agent LangGraph Pipeline (Issue #31).

Verifies the complete Phase 4 Definition of Done:
1. Full 5-agent pipeline runs end-to-end on a real scripted scenario ("Pump 7 fails")
   and produces a valid, schema-checked decision report.
2. A deliberately bad agent output triggers retry-then-escalate, not a silent write.
3. "What if Pump 7 fails?" produces an impact report naming the correct downstream
   assets from the dependency graph (Pump 7 -> Heat Exchanger 2 -> Turbine Generator 1).
4. Pipeline run completes within the 30-second SLO on a representative test facility size.
5. agent_runs table has a complete, queryable history of the test scenario.
"""

from __future__ import annotations

import time
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.runnables import RunnableConfig

from app.agents.graph import build_facility_twin_graph, run_facility_twin_pipeline
from app.agents.state import FacilityTwinState, create_initial_state
from app.agents.tools.asset_graph_tool import build_asset_graph_from_records
from app.models.agent_run import AgentRun
from app.schemas.agent_outputs.maintenance_inventory import MaintenanceInventoryOutput
from app.schemas.agent_outputs.planner import PlannerOutput
from app.schemas.agent_outputs.risk_assessment import RiskAssessmentOutput
from app.schemas.agent_outputs.route_optimization import RouteOptimizationOutput
from app.schemas.agent_outputs.simulation_decision import (
    CascadeImpactReport,
    SimulationDecisionOutput,
)
from app.services.agent_run_service import execute_and_record_pipeline


@pytest.fixture
def sample_tenant_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def sample_facility_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def representative_facility_topology() -> dict[str, Any]:
    """Sets up a realistic industrial facility topology with linear and branching cascades.

    Topology:
      Pump 7 (root cooling pump)
        -> Heat Exchanger 2 (primary thermal loop)
             -> Turbine Generator 1 (power generation block)
      Aux Compressor 4 (independent utility unit)
    """
    pump_7_id = uuid.uuid4()
    hx_2_id = uuid.uuid4()
    turbine_1_id = uuid.uuid4()
    aux_comp_id = uuid.uuid4()

    assets = [
        {
            "id": pump_7_id,
            "name": "Cooling Pump 7",
            "type": "centrifugal_pump",
            "x": 40.0,
            "y": 50.0,
            "status": "operational",
            "zone": "Sector A Cooling",
        },
        {
            "id": hx_2_id,
            "name": "Heat Exchanger 2",
            "type": "heat_exchanger",
            "x": 85.0,
            "y": 90.0,
            "status": "operational",
            "zone": "Sector A Thermal Loop",
        },
        {
            "id": turbine_1_id,
            "name": "Turbine Generator 1",
            "type": "turbine_generator",
            "x": 140.0,
            "y": 130.0,
            "status": "operational",
            "zone": "Sector B Generation Block",
        },
        {
            "id": aux_comp_id,
            "name": "Aux Compressor 4",
            "type": "compressor",
            "x": 210.0,
            "y": 60.0,
            "status": "operational",
            "zone": "Sector C Utility",
        },
    ]

    dependencies = [
        {"child_asset_id": pump_7_id, "parent_asset_id": hx_2_id},
        {"child_asset_id": hx_2_id, "parent_asset_id": turbine_1_id},
    ]

    coordinates = {
        str(pump_7_id): (40.0, 50.0),
        str(hx_2_id): (85.0, 90.0),
        str(turbine_1_id): (140.0, 130.0),
        str(aux_comp_id): (210.0, 60.0),
    }

    graph, summary, _ = build_asset_graph_from_records(assets, dependencies)

    return {
        "pump_7_id": pump_7_id,
        "hx_2_id": hx_2_id,
        "turbine_1_id": turbine_1_id,
        "aux_comp_id": aux_comp_id,
        "assets": assets,
        "dependencies": dependencies,
        "coordinates": coordinates,
        "graph": graph,
        "summary": summary,
    }


@pytest.mark.anyio
async def test_end_to_end_pump_7_fails_scenario(
    sample_tenant_id: uuid.UUID,
    sample_facility_id: uuid.UUID,
    representative_facility_topology: dict[str, Any],
) -> None:
    """DoD Items 1, 3, 4: Exercises all 5 agents on 'What if Pump 7 fails?' scenario.

    Asserts:
    1. Full 5-agent pipeline executes end-to-end within 30-second SLO.
    2. Outputs of all 5 hops conform to their respective Pydantic schemas.
    3. Cascade impact analysis correctly identifies Pump 7 as root cause and names
       Heat Exchanger 2 and Turbine Generator 1 as downstream shutoff assets.
    4. Decision report synthesizes operational impact and technician dispatch route.
    """
    topo = representative_facility_topology
    pump_7_id = topo["pump_7_id"]
    hx_2_id = topo["hx_2_id"]
    turbine_1_id = topo["turbine_1_id"]

    initial_state = create_initial_state(
        tenant_id=str(sample_tenant_id),
        facility_id=str(sample_facility_id),
        trigger="user_query",
        user_query="What happens if Pump 7 fails?",
    )

    config: RunnableConfig = {
        "configurable": {
            "assets": topo["assets"],
            "dependencies": topo["dependencies"],
            "asset_coordinates": topo["coordinates"],
            "graph": topo["graph"],
        }
    }

    # Execute pipeline under timer for 30s SLO verification
    start_time = time.monotonic()
    final_state = await run_facility_twin_pipeline(initial_state, config=config)
    elapsed_seconds = time.monotonic() - start_time

    # 1. 30-second SLO check (DoD item 4)
    assert elapsed_seconds < 30.0, f"Pipeline exceeded 30s SLO: took {elapsed_seconds:.2f}s"

    # 2. Pipeline health assertions
    assert final_state["halted_for_escalation"] is False
    assert final_state["escalation_reason"] is None
    assert len(final_state["validation_errors"]) == 0
    assert len(final_state["errors"]) == 0
    assert final_state["iteration_count"] == 5

    # 3. Hop 1: Planner Agent output verification
    assert final_state["asset_graph"] is not None
    assert "summary" in final_state["asset_graph"]
    assert "tasks" in final_state["asset_graph"]
    assert final_state["asset_graph"]["summary"]["total_nodes"] == 4
    assert final_state["asset_graph"]["summary"]["total_edges"] == 2

    # 4. Hop 2: Risk Assessment Agent output verification
    assert final_state["risk_scores"] is not None
    assert len(final_state["risk_scores"]) >= 1
    # Pump 7 identified and evaluated
    asset_ids_scored = [str(r["asset_id"]) for r in final_state["risk_scores"]]
    assert str(pump_7_id) in asset_ids_scored

    # 5. Hop 3: Maintenance & Inventory Planning output verification
    assert final_state["maintenance_schedule"] is not None
    assert final_state["inventory_gaps"] is not None
    assert len(final_state["maintenance_schedule"]) >= 1

    # 6. Hop 4: Route Optimization Agent output verification
    assert final_state["dispatch_routes"] is not None
    assert len(final_state["dispatch_routes"]) >= 1
    # Check that technician route has assigned stops
    first_route = final_state["dispatch_routes"][0]
    assert "assigned_stops" in first_route
    assert first_route["total_route_duration_minutes"] >= 0.0

    # 7. Hop 5: Simulation & Decision Agent output verification (DoD items 1 & 3)
    sim_result = final_state["simulation_result"]
    assert sim_result is not None
    assert isinstance(sim_result, dict)

    # Assert root cause is Pump 7
    assert sim_result["root_cause_asset_id"] == str(pump_7_id)

    # Assert downstream cascade names the correct dependents
    directly_affected = [str(a) for a in sim_result["directly_affected_asset_ids"]]
    downstream_shutoff = [str(a) for a in sim_result["downstream_shutoff_asset_ids"]]

    assert str(hx_2_id) in directly_affected
    assert str(hx_2_id) in downstream_shutoff
    assert str(turbine_1_id) in downstream_shutoff

    assert sim_result["total_affected_assets"] == 3  # Pump 7 + HX 2 + Turbine 1
    assert sim_result["cascade_depth"] == 2
    assert sim_result["estimated_downtime_hours"] > 0.0

    # Assert executive decision report
    decision_report = final_state["decision_report"]
    assert decision_report is not None
    assert len(decision_report) >= 20
    assert "Multi-agent digital twin analysis concluded" in decision_report
    assert str(pump_7_id) in decision_report

    # Assert confidence
    confidence = final_state["confidence"]
    assert confidence is not None
    assert 0.0 <= confidence <= 1.0


@pytest.mark.anyio
async def test_deliberately_bad_output_triggers_retry_then_escalate(
    sample_tenant_id: uuid.UUID,
    sample_facility_id: uuid.UUID,
) -> None:
    """DoD Item 2: Injects malformed agent output into an intermediate node.

    Asserts that:
    1. Validation failure triggers retry with error feedback.
    2. Repeated failure immediately halts the pipeline branch via fail-fast safety.
    3. Halted state is set, escalation_reason is recorded, and no silent write occurs.
    4. Downstream nodes are never executed.
    """
    call_count = 0

    async def malformed_risk_agent(context: dict[str, Any]) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        # Intentionally malformed payload violating RiskAssessmentOutput schema
        return {
            "hallucinated_field": "corrupt_data",
            "confidence": 99.9,  # invalid: exceeds 1.0
        }

    initial_state = create_initial_state(
        tenant_id=str(sample_tenant_id),
        facility_id=str(sample_facility_id),
        trigger="scheduled",
    )

    config: RunnableConfig = {
        "configurable": {
            "risk_assessment_callable": malformed_risk_agent,
        }
    }

    final_state = await run_facility_twin_pipeline(initial_state, config=config)

    # 1. Retry mechanism executed (1 original + 1 retry = 2 calls)
    assert call_count == 2, f"Expected 2 attempts (initial + retry), got {call_count}"

    # 2. Pipeline halted for escalation
    assert final_state["halted_for_escalation"] is True
    assert final_state["escalation_reason"] is not None
    assert "risk_assessment" in final_state["escalation_reason"].lower()

    # 3. Validation errors captured
    assert len(final_state["validation_errors"]) >= 1

    # 4. Downstream nodes (Hop 3, 4, 5) MUST NOT have executed
    assert final_state["maintenance_schedule"] is None
    assert final_state["dispatch_routes"] is None
    assert final_state["simulation_result"] is None
    assert final_state["decision_report"] is None


@pytest.mark.anyio
async def test_agent_runs_complete_queryable_history(
    sample_tenant_id: uuid.UUID,
    sample_facility_id: uuid.UUID,
    representative_facility_topology: dict[str, Any],
) -> None:
    """DoD Item 5: Verifies that execute_and_record_pipeline persists complete queryable history."""
    topo = representative_facility_topology
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    config: RunnableConfig = {
        "configurable": {
            "assets": topo["assets"],
            "dependencies": topo["dependencies"],
            "asset_coordinates": topo["coordinates"],
            "graph": topo["graph"],
        }
    }

    final_state, run = await execute_and_record_pipeline(
        mock_session,
        tenant_id=sample_tenant_id,
        facility_id=sample_facility_id,
        trigger="user_query",
        user_query="What happens if Pump 7 fails?",
        config=config,
    )

    # Assert persistence call
    mock_session.add.assert_called_once()
    mock_session.commit.assert_awaited_once()

    # Assert record properties
    assert isinstance(run, AgentRun)
    assert run.tenant_id == sample_tenant_id
    assert run.facility_id == sample_facility_id
    assert run.trigger == "user_query"
    assert run.status == "completed"
    assert run.duration_ms > 0
    assert run.confidence is not None
    assert run.decision_report is not None

    # Assert snapshot contains the complete state machine data
    snapshot = run.state_snapshot_json
    assert snapshot["simulation_result"]["root_cause_asset_id"] == str(topo["pump_7_id"])
    assert len(snapshot["simulation_result"]["downstream_shutoff_asset_ids"]) == 2
    assert len(run.validation_errors_json) == 0


@pytest.mark.anyio
async def test_30_second_slo_representative_facility_benchmark(
    sample_tenant_id: uuid.UUID,
    sample_facility_id: uuid.UUID,
) -> None:
    """DoD Item 4: Benchmarks the pipeline execution on a 10-node facility topology."""
    # Generate 10-node facility topology
    node_ids = [uuid.uuid4() for _ in range(10)]
    assets = [
        {
            "id": node_ids[i],
            "name": f"Industrial Asset {i+1}",
            "type": "pump" if i % 2 == 0 else "motor",
            "x": float(30.0 + i * 20.0),
            "y": float(40.0 + (i % 3) * 30.0),
            "status": "operational",
            "zone": f"Sector {chr(65 + (i % 4))}",
        }
        for i in range(10)
    ]
    # Chain dependencies: 0->1->2, 3->4->5, 6->7->8
    deps = [
        {"child_asset_id": node_ids[0], "parent_asset_id": node_ids[1]},
        {"child_asset_id": node_ids[1], "parent_asset_id": node_ids[2]},
        {"child_asset_id": node_ids[3], "parent_asset_id": node_ids[4]},
        {"child_asset_id": node_ids[4], "parent_asset_id": node_ids[5]},
        {"child_asset_id": node_ids[6], "parent_asset_id": node_ids[7]},
        {"child_asset_id": node_ids[7], "parent_asset_id": node_ids[8]},
    ]
    coords = {str(a["id"]): (a["x"], a["y"]) for a in assets}
    graph, _, _ = build_asset_graph_from_records(assets, deps)

    initial_state = create_initial_state(
        tenant_id=str(sample_tenant_id),
        facility_id=str(sample_facility_id),
        trigger="scheduled",
    )

    config: RunnableConfig = {
        "configurable": {
            "assets": assets,
            "dependencies": deps,
            "asset_coordinates": coords,
            "graph": graph,
        }
    }

    start = time.monotonic()
    final_state = await run_facility_twin_pipeline(initial_state, config=config)
    duration = time.monotonic() - start

    assert duration < 30.0, f"Benchmark exceeded 30s SLO: took {duration:.2f}s"
    assert final_state["halted_for_escalation"] is False
    assert final_state["decision_report"] is not None
    assert final_state["simulation_result"] is not None
