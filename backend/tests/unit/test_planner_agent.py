"""Unit tests for Phase 4 Task 3: Agent 1 — Planner (PROJECT_PLAN.md §4.3, Issue #25).

Asserts on:
1. Asset graph construction from records and topological summary computation.
2. Graceful handling of empty facility topologies.
3. Task decomposition for scheduled facility evaluations.
4. Targeted scenario decomposition for user queries ("What if X fails?").
5. Emergency triage decomposition for telemetry anomaly alerts.
6. Strict Pydantic validation contract compliance for PlannerOutput.
7. Integration of real planner logic into the LangGraph pipeline.
"""

from __future__ import annotations

import uuid
from typing import Any

import networkx as nx
import pytest
from langchain_core.runnables import RunnableConfig

from app.agents.graph import run_facility_twin_pipeline
from app.agents.planner import (
    decompose_facility_plan,
    planner_node,
    real_planner_callable,
)
from app.agents.state import create_initial_state
from app.agents.tools.asset_graph_tool import build_asset_graph_from_records
from app.agents.validation import validate_agent_output
from app.schemas.agent_outputs.planner import PlannerOutput


@pytest.fixture
def sample_tenant_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def sample_facility_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def sample_asset_records() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], uuid.UUID, uuid.UUID, uuid.UUID
]:
    """Provides a 3-node linear dependency graph: Pump 1 (upstream) -> Boiler 2 -> Turbine 3."""
    pump_id = uuid.uuid4()
    boiler_id = uuid.uuid4()
    turbine_id = uuid.uuid4()

    assets = [
        {
            "id": pump_id,
            "name": "Main Water Pump 1",
            "type": "pump",
            "status": "operational",
            "x": 100.0,
            "y": 150.0,
        },
        {
            "id": boiler_id,
            "name": "High-Pressure Boiler 2",
            "type": "boiler",
            "status": "operational",
            "x": 200.0,
            "y": 250.0,
        },
        {
            "id": turbine_id,
            "name": "Steam Turbine 3",
            "type": "turbine",
            "status": "operational",
            "x": 300.0,
            "y": 350.0,
        },
    ]

    # Boiler depends on Pump (pump is child/upstream), Turbine depends on Boiler
    dependencies = [
        {"parent_asset_id": boiler_id, "child_asset_id": pump_id},
        {"parent_asset_id": turbine_id, "child_asset_id": boiler_id},
    ]

    return assets, dependencies, pump_id, boiler_id, turbine_id


# ============================================================================
# 1. Asset Graph Tool Tests
# ============================================================================


def test_asset_graph_tool_constructs_networkx_graph_and_summary(
    sample_asset_records: tuple[
        list[dict[str, Any]], list[dict[str, Any]], uuid.UUID, uuid.UUID, uuid.UUID
    ],
) -> None:
    assets, deps, pump_id, boiler_id, turbine_id = sample_asset_records
    graph, summary, serialized = build_asset_graph_from_records(assets, deps)

    assert isinstance(graph, nx.DiGraph)
    assert graph.number_of_nodes() == 3
    assert graph.number_of_edges() == 2

    # Edge direction: child -> parent (upstream -> downstream cascade flow)
    assert graph.has_edge(str(pump_id), str(boiler_id))
    assert graph.has_edge(str(boiler_id), str(turbine_id))

    # Node attributes
    node_data = graph.nodes[str(pump_id)]
    assert node_data["name"] == "Main Water Pump 1"
    assert node_data["type"] == "pump"
    assert node_data["x"] == 100.0

    # Summary topology checks
    assert summary.total_nodes == 3
    assert summary.total_edges == 2
    assert summary.root_asset_ids == [pump_id]  # Only pump has in-degree 0
    assert summary.critical_path_asset_ids == [
        boiler_id,
        pump_id,
    ] or summary.critical_path_asset_ids == [pump_id, boiler_id]

    # Serialized structure
    assert "nodes" in serialized
    assert "edges" in serialized or "links" in serialized


def test_asset_graph_tool_empty_facility() -> None:
    """Verifies that an empty facility produces a clean zero-node/zero-edge summary."""
    graph, summary, serialized = build_asset_graph_from_records([], [])

    assert graph.number_of_nodes() == 0
    assert graph.number_of_edges() == 0
    assert summary.total_nodes == 0
    assert summary.total_edges == 0
    assert summary.root_asset_ids == []
    assert summary.critical_path_asset_ids == []
    assert serialized["nodes"] == []


# ============================================================================
# 2. Planner Task Decomposition Tests
# ============================================================================


def test_planner_agent_scheduled_trigger_decomposition(
    sample_tenant_id: uuid.UUID,
    sample_facility_id: uuid.UUID,
    sample_asset_records: tuple[
        list[dict[str, Any]], list[dict[str, Any]], uuid.UUID, uuid.UUID, uuid.UUID
    ],
) -> None:
    """Verifies that scheduled triggers produce a 4-agent DAG pipeline."""
    assets, deps, pump_id, _, _ = sample_asset_records
    graph, summary, _ = build_asset_graph_from_records(assets, deps)

    plan = decompose_facility_plan(
        tenant_id=sample_tenant_id,
        facility_id=sample_facility_id,
        trigger="scheduled",
        user_query=None,
        graph=graph,
        summary=summary,
    )

    assert isinstance(plan, PlannerOutput)
    assert plan.target_facility_id == sample_facility_id
    assert len(plan.active_agents) == 4
    assert set(plan.active_agents) == {
        "risk_assessment",
        "maintenance_inventory",
        "route_optimization",
        "simulation_decision",
    }

    # Verify task ordering and dependencies
    assert len(plan.tasks) == 4
    task_map = {t.task_id: t for t in plan.tasks}

    assert "task_sched_risk" in task_map
    assert task_map["task_sched_risk"].agent == "risk_assessment"
    assert task_map["task_sched_risk"].priority == 1
    assert task_map["task_sched_risk"].dependencies == []

    assert "task_sched_maint" in task_map
    assert task_map["task_sched_maint"].agent == "maintenance_inventory"
    assert task_map["task_sched_maint"].dependencies == ["task_sched_risk"]

    assert "task_sched_route" in task_map
    assert task_map["task_sched_route"].dependencies == ["task_sched_maint"]

    assert "task_sched_decision" in task_map
    assert task_map["task_sched_decision"].dependencies == ["task_sched_route"]

    assert plan.confidence >= 0.90


def test_planner_agent_user_query_shutdown_scenario(
    sample_tenant_id: uuid.UUID,
    sample_facility_id: uuid.UUID,
    sample_asset_records: tuple[
        list[dict[str, Any]], list[dict[str, Any]], uuid.UUID, uuid.UUID, uuid.UUID
    ],
) -> None:
    """Verifies that a user query asking 'What if Pump 1 fails?' correctly resolves
    the target asset and downstream dependents in the cascade tree."""
    assets, deps, pump_id, boiler_id, turbine_id = sample_asset_records
    graph, summary, _ = build_asset_graph_from_records(assets, deps)

    query = "What happens if Main Water Pump 1 fails unexpectedly?"
    plan = decompose_facility_plan(
        tenant_id=sample_tenant_id,
        facility_id=sample_facility_id,
        trigger="user_query",
        user_query=query,
        graph=graph,
        summary=summary,
    )

    assert isinstance(plan, PlannerOutput)
    assert "Main Water Pump 1" in plan.goal
    assert "Main Water Pump 1" in plan.reasoning
    assert plan.confidence >= 0.90

    # Sub-tasks should specifically include cascade simulation
    task_map = {t.task_id: t for t in plan.tasks}
    assert "task_2_cascade_sim" in task_map
    cascade_task = task_map["task_2_cascade_sim"]
    assert cascade_task.agent == "simulation_decision"

    # Must target the pump and both downstream descendants (Boiler and Turbine)
    assert pump_id in cascade_task.target_asset_ids
    assert boiler_id in cascade_task.target_asset_ids
    assert turbine_id in cascade_task.target_asset_ids


def test_planner_agent_anomaly_alert_trigger(
    sample_tenant_id: uuid.UUID,
    sample_facility_id: uuid.UUID,
    sample_asset_records: tuple[
        list[dict[str, Any]], list[dict[str, Any]], uuid.UUID, uuid.UUID, uuid.UUID
    ],
) -> None:
    """Verifies that an anomaly alert trigger generates prioritized emergency triage."""
    assets, deps, pump_id, _, _ = sample_asset_records
    graph, summary, _ = build_asset_graph_from_records(assets, deps)

    plan = decompose_facility_plan(
        tenant_id=sample_tenant_id,
        facility_id=sample_facility_id,
        trigger="alert",
        user_query=None,
        graph=graph,
        summary=summary,
        target_asset_ids=[pump_id],
    )

    assert "anomalous" in plan.goal.lower() or "alert" in plan.goal.lower()
    task_map = {t.task_id: t for t in plan.tasks}
    assert "task_alert_risk" in task_map
    assert task_map["task_alert_risk"].priority == 1
    assert task_map["task_alert_maint"].priority == 1
    assert plan.confidence >= 0.90


# ============================================================================
# 3. Pydantic Schema Validation & Node Execution Tests
# ============================================================================


def test_planner_agent_output_satisfies_pydantic_schema(
    sample_tenant_id: uuid.UUID,
    sample_facility_id: uuid.UUID,
    sample_asset_records: tuple[
        list[dict[str, Any]], list[dict[str, Any]], uuid.UUID, uuid.UUID, uuid.UUID
    ],
) -> None:
    """Verifies that PlannerOutput generated by decompose_facility_plan conforms
    strictly to PlannerOutput validation with extra='forbid'."""
    assets, deps, _, _, _ = sample_asset_records
    graph, summary, _ = build_asset_graph_from_records(assets, deps)

    plan = decompose_facility_plan(
        tenant_id=sample_tenant_id,
        facility_id=sample_facility_id,
        trigger="scheduled",
        user_query=None,
        graph=graph,
        summary=summary,
    )

    raw_dict = plan.model_dump(mode="json")
    validated = validate_agent_output(PlannerOutput, raw_dict)
    assert validated.plan_id == plan.plan_id
    assert validated.confidence == plan.confidence
    assert len(validated.tasks) == len(plan.tasks)


@pytest.mark.anyio
async def test_planner_node_direct_execution_with_records(
    sample_tenant_id: uuid.UUID,
    sample_facility_id: uuid.UUID,
    sample_asset_records: tuple[
        list[dict[str, Any]], list[dict[str, Any]], uuid.UUID, uuid.UUID, uuid.UUID
    ],
) -> None:
    """Verifies planner_node execution when asset records are supplied via config."""
    assets, deps, pump_id, _, _ = sample_asset_records
    initial_state = create_initial_state(
        tenant_id=str(sample_tenant_id),
        facility_id=str(sample_facility_id),
        trigger="user_query",
        user_query="Inspect Main Water Pump 1 status",
    )

    config: RunnableConfig = {
        "configurable": {
            "assets": assets,
            "dependencies": deps,
            "planner_callable": real_planner_callable,
        }
    }

    delta = await planner_node(initial_state, config=config)

    assert "asset_graph" in delta
    assert delta["asset_graph"]["summary"]["total_nodes"] == 3
    assert delta["asset_graph"]["summary"]["total_edges"] == 2
    assert "tasks" in delta["asset_graph"]
    assert delta["confidence"] >= 0.90
    assert delta["iteration_count"] == 1


@pytest.mark.anyio
async def test_planner_node_within_compiled_pipeline(
    sample_tenant_id: uuid.UUID,
    sample_facility_id: uuid.UUID,
    sample_asset_records: tuple[
        list[dict[str, Any]], list[dict[str, Any]], uuid.UUID, uuid.UUID, uuid.UUID
    ],
) -> None:
    """Verifies that the compiled LangGraph pipeline runs end-to-end with the
    real Planner agent decomposing a query against custom facility assets."""
    assets, deps, _, _, _ = sample_asset_records
    initial_state = create_initial_state(
        tenant_id=str(sample_tenant_id),
        facility_id=str(sample_facility_id),
        trigger="user_query",
        user_query="What happens if High-Pressure Boiler 2 goes offline?",
    )

    config: RunnableConfig = {
        "configurable": {
            "assets": assets,
            "dependencies": deps,
            "planner_callable": real_planner_callable,
        }
    }

    final_state = await run_facility_twin_pipeline(initial_state, config=config)

    assert final_state["halted_for_escalation"] is False
    assert final_state["asset_graph"] is not None
    assert final_state["asset_graph"]["summary"]["total_nodes"] == 3
    assert "High-Pressure Boiler 2" in final_state["asset_graph"]["reasoning"]
    assert final_state["iteration_count"] == 5
    assert final_state["decision_report"] is not None
