"""Unit tests for Phase 4 Task 2: FacilityTwinState + 5-Agent LangGraph skeleton.

Extra Scrutiny Task (PROJECT_PLAN.md §4.4, §4.5, PHASE_PLAN.md Phase 4 Task 2):
Asserts on:
1. End-to-end happy path traversal across all 5 hops with schema validation.
2. Hop-by-hop schema contracts: validation catches malformed outputs at each node.
3. Fail-fast safety boundary: persistent failure at any hop halts pipeline branch
   and prevents downstream nodes from receiving corrupted state.
4. Self-healing transient recovery: retry with structured error feedback succeeds.
5. Solver SLA duration enforcement at Route Optimization node.
6. State immutability: nodes return delta updates without in-place mutation of input state.
7. Loop guard: prevents runaway execution when iteration limit is reached.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from app.agents.graph import (
    check_pipeline_branch,
    run_facility_twin_pipeline,
)
from app.agents.maintenance_inventory import maintenance_inventory_node
from app.agents.planner import planner_node
from app.agents.risk_assessment import risk_assessment_node
from app.agents.route_optimization import route_optimization_node
from app.agents.simulation_decision import simulation_decision_node
from app.agents.state import (
    MAX_GRAPH_ITERATIONS,
    FacilityTwinState,
    create_initial_state,
)


@pytest.fixture
def sample_tenant_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def sample_facility_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def sample_initial_state(sample_tenant_id: str, sample_facility_id: str) -> FacilityTwinState:
    return create_initial_state(
        tenant_id=sample_tenant_id,
        facility_id=sample_facility_id,
        trigger="scheduled",
        user_query="Conduct quarterly digital twin risk evaluation",
    )


# ============================================================================
# 1. Happy Path End-to-End Test
# ============================================================================


@pytest.mark.anyio
async def test_langgraph_skeleton_runs_end_to_end_with_valid_dummy_data(
    sample_initial_state: FacilityTwinState,
) -> None:
    """Verifies that all 5 placeholder nodes execute sequentially and produce
    Pydantic-validated state at each hop from START to END."""
    final_state = await run_facility_twin_pipeline(sample_initial_state)

    # 1. State integrity
    assert final_state["tenant_id"] == sample_initial_state["tenant_id"]
    assert final_state["facility_id"] == sample_initial_state["facility_id"]
    assert final_state["trigger"] == "scheduled"
    assert final_state["halted_for_escalation"] is False
    assert final_state["escalation_reason"] is None
    assert final_state["validation_errors"] == []
    assert final_state["errors"] == []
    assert final_state["iteration_count"] == 5

    # 2. Hop 1: Planner
    assert final_state["asset_graph"] is not None
    assert "summary" in final_state["asset_graph"]
    assert "tasks" in final_state["asset_graph"]
    assert len(final_state["asset_graph"]["tasks"]) >= 1

    # 3. Hop 2: Risk Assessment
    assert final_state["risk_scores"] is not None
    assert len(final_state["risk_scores"]) == 1
    assert final_state["risk_scores"][0]["risk_score"] == 78.4
    assert final_state["risk_scores"][0]["risk_tier"] == "critical"

    # 4. Hop 3: Maintenance & Inventory
    assert final_state["maintenance_schedule"] is not None
    assert len(final_state["maintenance_schedule"]) == 1
    assert final_state["inventory_gaps"] is not None

    # 5. Hop 4: Route Optimization
    assert final_state["dispatch_routes"] is not None
    assert len(final_state["dispatch_routes"]) == 1
    assert final_state["dispatch_routes"][0]["technician_name"] == "Jordan Lee"

    # 6. Hop 5: Simulation & Decision
    assert final_state["simulation_result"] is not None
    assert final_state["decision_report"] is not None
    assert "Multi-agent digital twin analysis concluded" in final_state["decision_report"]
    assert final_state["confidence"] == 0.94


# ============================================================================
# 2. Adversarial & Fail-Fast Safety Boundary Tests
# ============================================================================


@pytest.mark.anyio
async def test_hop_1_planner_schema_failure_halts_pipeline_immediately(
    sample_initial_state: FacilityTwinState,
) -> None:
    """Adversarial: Planner produces invalid confidence (> 1.0) and extra hallucinated keys.
    Asserts pipeline halts at Hop 1, sets escalation latch, and prevents Hops 2-5 from running."""

    async def malformed_planner(ctx: dict[str, Any]) -> dict[str, Any]:
        return {
            "invalid_hallucinated_key": True,
            "confidence": 99.9,  # invalid bound
        }

    config: RunnableConfig = {"configurable": {"planner_callable": malformed_planner}}
    final_state = await run_facility_twin_pipeline(sample_initial_state, config=config)

    assert final_state["halted_for_escalation"] is True
    assert final_state["escalation_reason"] is not None
    assert "planner" in final_state["escalation_reason"].lower()
    assert len(final_state["validation_errors"]) >= 1
    assert any("confidence" in err for err in final_state["validation_errors"])

    # Downstream nodes must NEVER have executed
    assert final_state["risk_scores"] is None
    assert final_state["maintenance_schedule"] is None
    assert final_state["inventory_gaps"] is None
    assert final_state["dispatch_routes"] is None
    assert final_state["simulation_result"] is None
    assert final_state["decision_report"] is None
    assert final_state["iteration_count"] == 1


@pytest.mark.anyio
async def test_mid_pipeline_failure_at_hop_3_halts_branch_cleanly(
    sample_initial_state: FacilityTwinState,
) -> None:
    """Adversarial: Hops 1 and 2 succeed. Hop 3 (Maintenance & Inventory) produces negative parts.
    Asserts pipeline halts after Hop 3; valid prior outputs remain intact, while Hops 4 and 5
    are never reached."""

    async def invalid_maintenance_callable(ctx: dict[str, Any]) -> dict[str, Any]:
        facility_id = ctx.get("facility_id", str(uuid.uuid4()))
        return {
            "schedule_id": str(uuid.uuid4()),
            "facility_id": str(facility_id),
            "scheduled_items": [
                {
                    "item_id": "bad_item",
                    "asset_id": str(uuid.uuid4()),
                    "scheduled_date": "2026-10-02",
                    "priority": "critical",
                    "estimated_duration_hours": 2.0,
                    "required_parts": [
                        {
                            "part_id": "PART-1",
                            "part_name": "Part 1",
                            "quantity": -10,  # Negative quantity violates ge=1
                        }
                    ],
                }
            ],
            "total_tasks_scheduled": 1,
            "critical_shortage_count": 0,
            "schedule_summary": "Attempting invalid maintenance allocation",
            "confidence": 0.85,
        }

    config: RunnableConfig = {
        "configurable": {"maintenance_inventory_callable": invalid_maintenance_callable}
    }
    final_state = await run_facility_twin_pipeline(sample_initial_state, config=config)

    assert final_state["halted_for_escalation"] is True
    assert "maintenance_inventory" in final_state["escalation_reason"]
    assert any("quantity" in err for err in final_state["validation_errors"])

    # Hops 1 and 2 completed successfully and their state is preserved
    assert final_state["asset_graph"] is not None
    assert final_state["risk_scores"] is not None

    # Hops 4 and 5 were never executed
    assert final_state["dispatch_routes"] is None
    assert final_state["simulation_result"] is None
    assert final_state["decision_report"] is None
    assert final_state["iteration_count"] == 3


@pytest.mark.anyio
async def test_transient_failure_at_hop_2_self_heals_via_retry_feedback(
    sample_initial_state: FacilityTwinState,
) -> None:
    """Verifies that transient failure at Hop 2 (Risk Assessment) retries with error feedback,
    recovers a valid output on attempt 2, and allows the pipeline to finish all 5 hops."""
    attempts = 0

    async def transient_risk_assessor(ctx: dict[str, Any]) -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        facility_id = ctx.get("facility_id", str(uuid.uuid4()))

        if attempts == 1:
            # Attempt 1 returns string risk_score (hallucination)
            return {
                "assessment_id": str(uuid.uuid4()),
                "facility_id": str(facility_id),
                "model_version": "xgboost-ai4i-v1.0",
                "ranked_assets": [
                    {
                        "asset_id": str(uuid.uuid4()),
                        "risk_score": "CRITICAL_HAZARD",  # invalid type
                        "urgency_rank": 1,
                        "risk_tier": "critical",
                        "recommended_action": "Inspect",
                    }
                ],
                "high_risk_count": 1,
                "medium_risk_count": 0,
                "low_risk_count": 0,
                "executive_summary": "Transient risk summary",
                "confidence": 0.9,
            }

        # Attempt 2 receives feedback and self-heals
        assert "last_validation_error" in ctx
        assert "risk_score" in ctx["last_validation_error"]
        return {
            "assessment_id": str(uuid.uuid4()),
            "facility_id": str(facility_id),
            "model_version": "xgboost-ai4i-v1.0",
            "ranked_assets": [
                {
                    "asset_id": str(uuid.uuid4()),
                    "risk_score": 88.0,  # corrected
                    "urgency_rank": 1,
                    "risk_tier": "critical",
                    "recommended_action": "Inspect immediately",
                }
            ],
            "high_risk_count": 1,
            "medium_risk_count": 0,
            "low_risk_count": 0,
            "executive_summary": "Recovered risk assessment summary",
            "confidence": 0.95,
        }

    config: RunnableConfig = {"configurable": {"risk_assessment_callable": transient_risk_assessor}}
    final_state = await run_facility_twin_pipeline(sample_initial_state, config=config)

    assert attempts == 2
    assert final_state["halted_for_escalation"] is False
    assert final_state["risk_scores"][0]["risk_score"] == 88.0
    assert final_state["decision_report"] is not None
    assert final_state["iteration_count"] == 5


@pytest.mark.anyio
async def test_hop_4_route_optimization_enforces_cvrp_solver_sla_limit(
    sample_initial_state: FacilityTwinState,
) -> None:
    """Adversarial: Route Optimization reports solver_duration_seconds = 35.0s,
    violating the 5s OR-Tools SLA constraint (max bound 10.0s in schema)."""

    async def slow_route_solver(ctx: dict[str, Any]) -> dict[str, Any]:
        facility_id = ctx.get("facility_id", str(uuid.uuid4()))
        return {
            "optimization_id": str(uuid.uuid4()),
            "facility_id": str(facility_id),
            "solver_status": "TIME_LIMIT_REACHED",
            "solver_duration_seconds": 35.0,  # Violates le=10.0
            "technician_routes": [],
            "unassigned_task_ids": ["task_1"],
            "total_distance_meters": 0.0,
            "confidence": 0.4,
        }

    config: RunnableConfig = {"configurable": {"route_optimization_callable": slow_route_solver}}
    final_state = await run_facility_twin_pipeline(sample_initial_state, config=config)

    assert final_state["halted_for_escalation"] is True
    assert "route_optimization" in final_state["escalation_reason"]
    assert any("solver_duration_seconds" in err for err in final_state["validation_errors"])
    assert final_state["dispatch_routes"] is None
    assert final_state["simulation_result"] is None


# ============================================================================
# 3. State Immutability & Loop Guard Tests
# ============================================================================


@pytest.mark.anyio
async def test_nodes_do_not_mutate_input_state_in_place(
    sample_initial_state: FacilityTwinState,
) -> None:
    """Extra Scrutiny: Nodes must return discrete delta dictionaries and never
    mutate incoming state dictionaries in-place."""
    state_copy = copy.deepcopy(sample_initial_state)

    delta_planner = await planner_node(sample_initial_state)
    assert isinstance(delta_planner, dict)
    # Incoming state dictionary must remain unmodified
    assert sample_initial_state == state_copy

    # Simulate LangGraph state merging
    merged_state = dict(sample_initial_state, **delta_planner)
    merged_copy = copy.deepcopy(merged_state)

    delta_risk = await risk_assessment_node(merged_state)  # type: ignore[arg-type]
    assert isinstance(delta_risk, dict)
    assert merged_state == merged_copy


@pytest.mark.anyio
async def test_loop_guard_halts_execution_when_max_iterations_reached(
    sample_initial_state: FacilityTwinState,
) -> None:
    """Extra Scrutiny: State with iteration_count >= MAX_GRAPH_ITERATIONS triggers
    loop guard, halting execution and setting error telemetry."""
    overflow_state = dict(sample_initial_state, iteration_count=MAX_GRAPH_ITERATIONS)

    delta = await planner_node(overflow_state)  # type: ignore[arg-type]

    assert delta["halted_for_escalation"] is True
    assert "Loop guard exceeded" in delta["escalation_reason"]
    assert any("Loop guard" in err for err in delta["errors"])
    assert delta["iteration_count"] == MAX_GRAPH_ITERATIONS + 1


def test_check_pipeline_branch_logic() -> None:
    """Tests the conditional router unit logic."""
    clean_state: FacilityTwinState = {
        "tenant_id": "t1",
        "facility_id": "f1",
        "trigger": "scheduled",
        "user_query": None,
        "asset_graph": None,
        "risk_scores": None,
        "maintenance_schedule": None,
        "inventory_gaps": None,
        "dispatch_routes": None,
        "simulation_result": None,
        "decision_report": None,
        "confidence": None,
        "validation_errors": [],
        "errors": [],
        "iteration_count": 2,
        "halted_for_escalation": False,
        "escalation_reason": None,
    }
    assert check_pipeline_branch(clean_state) == "continue"

    escalated_state = dict(clean_state, halted_for_escalation=True)
    assert check_pipeline_branch(escalated_state) == "halt"  # type: ignore[arg-type]

    overflow_state = dict(clean_state, iteration_count=MAX_GRAPH_ITERATIONS)
    assert check_pipeline_branch(overflow_state) == "halt"  # type: ignore[arg-type]


# ============================================================================
# 4. Hop 5 Failure & Direct Node Contract Unit Tests
# ============================================================================


@pytest.mark.anyio
async def test_hop_5_simulation_decision_schema_failure_sets_escalation(
    sample_initial_state: FacilityTwinState,
) -> None:
    """Adversarial: Simulation & Decision produces malformed payload missing
    required human_escalation_required boolean. Pipeline halts for escalation."""

    async def invalid_decision_maker(ctx: dict[str, Any]) -> dict[str, Any]:
        return {
            "decision_id": str(uuid.uuid4()),
            "facility_id": ctx.get("facility_id", str(uuid.uuid4())),
            # Missing scenario_trigger, cascade_impact, executive_summary, etc.
            "confidence_score": 0.9,
        }

    config: RunnableConfig = {
        "configurable": {"simulation_decision_callable": invalid_decision_maker}
    }
    final_state = await run_facility_twin_pipeline(sample_initial_state, config=config)

    assert final_state["halted_for_escalation"] is True
    assert "simulation_decision" in final_state["escalation_reason"]
    assert len(final_state["validation_errors"]) >= 1
    assert final_state["decision_report"] is None


@pytest.mark.anyio
async def test_all_five_nodes_direct_invocation_contracts(
    sample_initial_state: FacilityTwinState,
) -> None:
    """Directly invokes all 5 nodes individually verifying their contract schemas."""
    # Hop 1
    p_delta = await planner_node(sample_initial_state)
    assert "asset_graph" in p_delta
    assert p_delta["confidence"] == 0.95
    assert p_delta["iteration_count"] == 1

    # Hop 2
    state_after_p = dict(sample_initial_state, **p_delta)
    r_delta = await risk_assessment_node(state_after_p)  # type: ignore[arg-type]
    assert "risk_scores" in r_delta
    assert len(r_delta["risk_scores"]) == 1
    assert r_delta["iteration_count"] == 2

    # Hop 3
    state_after_r = dict(state_after_p, **r_delta)
    m_delta = await maintenance_inventory_node(state_after_r)  # type: ignore[arg-type]
    assert "maintenance_schedule" in m_delta
    assert "inventory_gaps" in m_delta
    assert m_delta["iteration_count"] == 3

    # Hop 4
    state_after_m = dict(state_after_r, **m_delta)
    ro_delta = await route_optimization_node(state_after_m)  # type: ignore[arg-type]
    assert "dispatch_routes" in ro_delta
    assert ro_delta["iteration_count"] == 4

    # Hop 5
    state_after_ro = dict(state_after_m, **ro_delta)
    sd_delta = await simulation_decision_node(state_after_ro)  # type: ignore[arg-type]
    assert "simulation_result" in sd_delta
    assert "decision_report" in sd_delta
    assert sd_delta["confidence"] == 0.94
    assert sd_delta["iteration_count"] == 5
