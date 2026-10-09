"""Unit tests for Phase 4 Task 7: Agent 5 — Simulation & Decision (PROJECT_PLAN.md §4.3, Issue #29).

Asserts on:
1. NetworkX directed failure cascade propagation over linear and branching topologies.
2. Topological cascade depth and projected downtime calculation.
3. Root cause asset resolution from user query, risk scores, or default graph nodes.
4. Multi-agent synthesis of risk, schedule, routing, and inventory telemetry.
5. Deterministic human escalation triggering and descriptive reason formatting.
6. Strict Pydantic validation contract compliance for SimulationDecisionOutput.
7. LangGraph node execution and retry-then-escalate safety harness.
"""

from __future__ import annotations

import uuid
from typing import Any

import networkx as nx
import pytest
from langchain_core.runnables import RunnableConfig

from app.agents.simulation_decision import (
    real_simulation_decision_callable,
    simulation_decision_node,
)
from app.agents.state import create_initial_state
from app.agents.tools.asset_graph_tool import build_asset_graph_from_records
from app.agents.tools.cascade_simulator_tool import (
    resolve_simulation_target_asset,
    simulate_failure_cascade,
)
from app.agents.tools.decision_synthesizer_tool import synthesize_executive_decision
from app.agents.validation import validate_agent_output
from app.schemas.agent_outputs.simulation_decision import (
    CascadeImpactReport,
    SimulationDecisionOutput,
)


@pytest.fixture
def sample_facility_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def sample_tenant_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def linear_topology() -> tuple[nx.DiGraph, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Builds a linear cascade: Pump A -> Heat Exchanger B -> Turbine C."""
    pump_id = uuid.uuid4()
    hx_id = uuid.uuid4()
    turbine_id = uuid.uuid4()

    assets = [
        {"id": pump_id, "name": "Primary Feed Pump 7", "type": "pump", "zone": "Intake Zone"},
        {"id": hx_id, "name": "Heat Exchanger Alpha", "type": "heat_exchanger", "zone": "Thermal Loop"},
        {"id": turbine_id, "name": "Turbine Gen 1", "type": "turbine", "zone": "Power Block"},
    ]
    deps = [
        {"child_asset_id": pump_id, "parent_asset_id": hx_id},
        {"child_asset_id": hx_id, "parent_asset_id": turbine_id},
    ]
    graph, _, _ = build_asset_graph_from_records(assets, deps)
    return graph, pump_id, hx_id, turbine_id


@pytest.fixture
def branching_topology() -> tuple[nx.DiGraph, uuid.UUID, list[uuid.UUID], list[uuid.UUID]]:
    """Builds a branching tree: Substation -> [Feeder 1, Feeder 2] -> [Pump 1, Pump 2]."""
    substation = uuid.uuid4()
    feeder_1 = uuid.uuid4()
    feeder_2 = uuid.uuid4()
    pump_1 = uuid.uuid4()
    pump_2 = uuid.uuid4()

    assets = [
        {"id": substation, "name": "Main Substation", "type": "substation", "zone": "Grid"},
        {"id": feeder_1, "name": "Feeder Bus 1", "type": "feeder", "zone": "North Wing"},
        {"id": feeder_2, "name": "Feeder Bus 2", "type": "feeder", "zone": "South Wing"},
        {"id": pump_1, "name": "Pump North", "type": "pump", "zone": "North Wing"},
        {"id": pump_2, "name": "Pump South", "type": "pump", "zone": "South Wing"},
    ]
    deps = [
        {"child_asset_id": substation, "parent_asset_id": feeder_1},
        {"child_asset_id": substation, "parent_asset_id": feeder_2},
        {"child_asset_id": feeder_1, "parent_asset_id": pump_1},
        {"child_asset_id": feeder_2, "parent_asset_id": pump_2},
    ]
    graph, _, _ = build_asset_graph_from_records(assets, deps)
    return graph, substation, [feeder_1, feeder_2], [pump_1, pump_2]


def test_linear_cascade_propagation(
    linear_topology: tuple[nx.DiGraph, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    """Asserts that failure at top node propagates correctly downstream through all children."""
    graph, pump_id, hx_id, turbine_id = linear_topology

    report = simulate_failure_cascade(graph=graph, root_cause_asset_id=pump_id)
    assert isinstance(report, CascadeImpactReport)
    assert report.root_cause_asset_id == pump_id
    assert report.directly_affected_asset_ids == [hx_id]
    assert set(report.downstream_shutoff_asset_ids) == {hx_id, turbine_id}
    assert report.total_affected_assets == 3
    assert report.cascade_depth == 2
    assert report.estimated_downtime_hours > 0.0


def test_branching_cascade_propagation(
    branching_topology: tuple[nx.DiGraph, uuid.UUID, list[uuid.UUID], list[uuid.UUID]],
) -> None:
    """Asserts that failure at root branch spreads through all multi-path descendants."""
    graph, substation, feeders, pumps = branching_topology

    report = simulate_failure_cascade(graph=graph, root_cause_asset_id=substation)
    assert report.root_cause_asset_id == substation
    assert set(report.directly_affected_asset_ids) == set(feeders)
    assert set(report.downstream_shutoff_asset_ids) == set(feeders + pumps)
    assert report.total_affected_assets == 5
    assert report.cascade_depth == 2


def test_isolated_asset_cascade_propagation() -> None:
    """Verifies that an unlinked asset failure has cascade depth 0 and 1 total affected."""
    graph = nx.DiGraph()
    isolated_id = uuid.uuid4()
    graph.add_node(str(isolated_id), name="Standalone Compressor", type="compressor")

    report = simulate_failure_cascade(graph=graph, root_cause_asset_id=isolated_id)
    assert report.root_cause_asset_id == isolated_id
    assert len(report.directly_affected_asset_ids) == 0
    assert len(report.downstream_shutoff_asset_ids) == 0
    assert report.total_affected_assets == 1
    assert report.cascade_depth == 0


def test_resolve_simulation_target_asset(
    linear_topology: tuple[nx.DiGraph, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    """Tests target asset identification from user query text and risk rankings."""
    graph, pump_id, hx_id, _ = linear_topology

    # 1. Matching from query text ("Pump 7")
    target_from_query = resolve_simulation_target_asset(
        graph, user_query="What happens if Pump 7 fails?"
    )
    assert target_from_query == pump_id

    # 2. Matching from risk scores
    risks = [
        {"asset_id": hx_id, "risk_score": 88.5},
        {"asset_id": pump_id, "risk_score": 42.0},
    ]
    target_from_risk = resolve_simulation_target_asset(graph, risk_scores=risks)
    assert target_from_risk == hx_id


def test_executive_decision_synthesis_nominal(
    sample_facility_id: uuid.UUID,
    linear_topology: tuple[nx.DiGraph, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    """Verifies natural-language summary and confidence score for nominal operational cycle."""
    graph, pump_id, _, _ = linear_topology
    report = simulate_failure_cascade(graph=graph, root_cause_asset_id=pump_id)

    decision = synthesize_executive_decision(
        facility_id=sample_facility_id,
        scenario_trigger="scheduled",
        cascade_impact=report,
    )
    assert isinstance(decision, SimulationDecisionOutput)
    assert "Multi-agent digital twin analysis concluded" in decision.executive_summary
    assert decision.confidence_score >= 0.85
    assert decision.human_escalation_required is False
    assert decision.escalation_reason is None
    assert len(decision.recommended_interventions) >= 1


def test_human_escalation_triggered_on_critical_inventory_gap(
    sample_facility_id: uuid.UUID,
    linear_topology: tuple[nx.DiGraph, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    """Verifies that spare parts shortages for critical assets trigger human escalation."""
    graph, pump_id, _, _ = linear_topology
    report = simulate_failure_cascade(graph=graph, root_cause_asset_id=pump_id)

    risks = [{"asset_id": pump_id, "risk_tier": "critical", "risk_score": 85.0}]
    shortages = [{"part_id": "SEAL-01", "needed_quantity": 2, "available_quantity": 0}]

    decision = synthesize_executive_decision(
        facility_id=sample_facility_id,
        scenario_trigger="anomaly_alert",
        cascade_impact=report,
        risk_scores=risks,
        inventory_gaps=shortages,
    )
    assert decision.human_escalation_required is True
    assert decision.escalation_reason is not None
    assert "shortage" in decision.escalation_reason.lower()
    assert decision.confidence_score < 0.94


@pytest.mark.anyio
async def test_simulation_decision_callable_and_node(
    sample_tenant_id: uuid.UUID,
    sample_facility_id: uuid.UUID,
    linear_topology: tuple[nx.DiGraph, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    """Verifies real Simulation & Decision Agent callable and LangGraph node execution."""
    graph, pump_id, _, _ = linear_topology
    state = create_initial_state(
        str(sample_tenant_id),
        str(sample_facility_id),
        trigger="user_query",
        user_query="What happens if Pump 7 goes offline?",
    )

    config: RunnableConfig = {"configurable": {"graph": graph}}
    result_delta = await simulation_decision_node(state, config=config)

    assert "simulation_result" in result_delta
    assert "decision_report" in result_delta
    assert result_delta["confidence"] is not None
    assert result_delta["iteration_count"] == 1
    assert result_delta["simulation_result"]["total_affected_assets"] == 3


@pytest.mark.anyio
async def test_simulation_decision_node_escalates_on_invalid_output(
    sample_tenant_id: uuid.UUID,
    sample_facility_id: uuid.UUID,
) -> None:
    """Verifies fail-fast escalation when Simulation & Decision produces invalid output."""
    state = create_initial_state(str(sample_tenant_id), str(sample_facility_id))

    async def bad_callable(ctx: dict[str, Any]) -> dict[str, Any]:
        return {
            "decision_id": str(uuid.uuid4()),
            "facility_id": ctx.get("facility_id", str(uuid.uuid4())),
            # Missing scenario_trigger, cascade_impact, executive_summary
            "confidence_score": 2.5,  # Exceeds max 1.0
        }

    config: RunnableConfig = {"configurable": {"simulation_decision_callable": bad_callable}}
    result_delta = await simulation_decision_node(state, config=config)

    assert result_delta["halted_for_escalation"] is True
    assert "simulation_decision" in result_delta["escalation_reason"].lower()
    assert len(result_delta["validation_errors"]) >= 1
