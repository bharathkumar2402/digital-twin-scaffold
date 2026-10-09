"""Unit tests for Phase 4 Task 6: Agent 4 — Route Optimization (PROJECT_PLAN.md §4.3, Issue #28).

Asserts on:
1. Spatial coordinate extraction and Euclidean distance/time matrix generation.
2. Google OR-Tools CVRP solver execution with strict 5-second time limit adherence (Rule 7).
3. Single and multi-technician route generation and sequence ordering.
4. Technician skill constraint matching and vehicle filtering.
5. Shift duration capacity enforcement and unassigned task tracking.
6. Empty task list graceful handling.
7. Strict Pydantic validation contract compliance for RouteOptimizationOutput.
8. LangGraph node execution and retry-then-escalate safety harness.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from app.agents.route_optimization import (
    real_route_optimization_callable,
    route_optimization_node,
)
from app.agents.state import create_initial_state
from app.agents.tools.distance_matrix_tool import (
    build_distance_and_time_matrices,
    compute_euclidean_distance,
    extract_asset_coordinates_map,
    get_or_create_coordinate,
)
from app.agents.tools.route_solver_tool import (
    DEFAULT_TECHNICIANS,
    MULTI_TECHNICIAN_ROSTER,
    solve_cvrp_routes,
)
from app.agents.validation import validate_agent_output
from app.schemas.agent_outputs.route_optimization import RouteOptimizationOutput


@pytest.fixture
def sample_facility_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def sample_tenant_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def sample_tasks() -> list[dict[str, Any]]:
    """Generates 3 representative maintenance tasks with skills and durations."""
    return [
        {
            "item_id": "task_pump_01",
            "asset_id": uuid.uuid4(),
            "scheduled_date": "2026-10-06",
            "priority": "critical",
            "estimated_duration_hours": 1.5,
            "required_technician_skills": ["mechanical"],
        },
        {
            "item_id": "task_motor_02",
            "asset_id": uuid.uuid4(),
            "scheduled_date": "2026-10-06",
            "priority": "high",
            "estimated_duration_hours": 1.0,
            "required_technician_skills": ["electrical"],
        },
        {
            "item_id": "task_hvac_03",
            "asset_id": uuid.uuid4(),
            "scheduled_date": "2026-10-06",
            "priority": "medium",
            "estimated_duration_hours": 2.0,
            "required_technician_skills": ["hvac"],
        },
    ]


@pytest.fixture
def sample_coordinates(
    sample_tasks: list[dict[str, Any]],
) -> dict[str, tuple[float, float]]:
    return {
        str(sample_tasks[0]["asset_id"]): (30.0, 40.0),   # 50m from depot (0,0)
        str(sample_tasks[1]["asset_id"]): (60.0, 80.0),   # 100m from depot
        str(sample_tasks[2]["asset_id"]): (120.0, 50.0),  # 130m from depot
    }


def test_distance_matrix_and_euclidean_distance() -> None:
    """Verifies 2D Euclidean distance and travel time calculation."""
    d = compute_euclidean_distance((0.0, 0.0), (30.0, 40.0))
    assert d == pytest.approx(50.0, rel=1e-3)

    locations = [(0.0, 0.0), (30.0, 40.0), (60.0, 80.0)]
    dist_matrix, time_matrix = build_distance_and_time_matrices(
        locations, speed_meters_per_minute=60.0
    )
    assert len(dist_matrix) == 3
    assert dist_matrix[0][1] == pytest.approx(50.0, rel=1e-3)
    assert time_matrix[0][1] == pytest.approx(50.0 / 60.0, rel=1e-2)


def test_coordinate_extraction_and_fallback_grid() -> None:
    """Verifies asset coordinate extraction and deterministic fallback coordinates."""
    asset_id_1 = str(uuid.uuid4())
    asset_records = [{"id": asset_id_1, "x": 12.5, "y": 34.5}]
    coord_map = extract_asset_coordinates_map(assets=asset_records)
    assert coord_map[asset_id_1] == (12.5, 34.5)

    # Missing asset gets deterministic grid coordinates
    missing_id = str(uuid.uuid4())
    fallback_coord = get_or_create_coordinate(missing_id, coord_map, fallback_index=3)
    assert fallback_coord[0] > 0.0
    assert fallback_coord[1] > 0.0


def test_cvrp_solver_single_technician(
    sample_facility_id: uuid.UUID,
    sample_tasks: list[dict[str, Any]],
    sample_coordinates: dict[str, tuple[float, float]],
) -> None:
    """Solves CVRP with default single technician and asserts on ordered stops and duration."""
    result = solve_cvrp_routes(
        facility_id=sample_facility_id,
        tasks=sample_tasks,
        asset_coordinates=sample_coordinates,
        technicians=DEFAULT_TECHNICIANS,
        time_limit_seconds=5.0,
    )

    assert isinstance(result, RouteOptimizationOutput)
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    assert result.solver_duration_seconds <= 5.5
    assert len(result.technician_routes) == 1
    assert result.technician_routes[0].technician_name == "Jordan Lee"
    assert len(result.technician_routes[0].assigned_stops) == 3
    assert result.total_distance_meters > 0.0
    assert result.confidence >= 0.90
    assert len(result.unassigned_task_ids) == 0


def test_cvrp_solver_multi_technician_skills_and_routing(
    sample_facility_id: uuid.UUID,
    sample_tasks: list[dict[str, Any]],
    sample_coordinates: dict[str, tuple[float, float]],
) -> None:
    """Solves CVRP with multi-technician roster and skill matching constraints."""
    result = solve_cvrp_routes(
        facility_id=sample_facility_id,
        tasks=sample_tasks,
        asset_coordinates=sample_coordinates,
        technicians=MULTI_TECHNICIAN_ROSTER,
        time_limit_seconds=5.0,
    )

    assert isinstance(result, RouteOptimizationOutput)
    assert len(result.technician_routes) == 2
    total_stops = sum(len(r.assigned_stops) for r in result.technician_routes)
    assert total_stops == 3


def test_cvrp_solver_strict_5s_time_limit_enforced(
    sample_facility_id: uuid.UUID,
) -> None:
    """Verifies that solver duration complies with Rule 7 (5s SLA time limit)."""
    # Create 8 tasks
    many_tasks = [
        {
            "item_id": f"task_{i}",
            "asset_id": uuid.uuid4(),
            "scheduled_date": "2026-10-06",
            "priority": "medium",
            "estimated_duration_hours": 1.0,
        }
        for i in range(8)
    ]

    start = time.monotonic()
    result = solve_cvrp_routes(
        facility_id=sample_facility_id,
        tasks=many_tasks,
        technicians=MULTI_TECHNICIAN_ROSTER,
        time_limit_seconds=2.0,
    )
    elapsed = time.monotonic() - start

    assert elapsed <= 5.0
    assert result.solver_duration_seconds <= 5.0
    assert result.solver_status in ("OPTIMAL", "FEASIBLE", "TIME_LIMIT_REACHED")


def test_cvrp_solver_empty_task_list(sample_facility_id: uuid.UUID) -> None:
    """Verifies graceful handling when no maintenance tasks are scheduled."""
    result = solve_cvrp_routes(
        facility_id=sample_facility_id,
        tasks=[],
        technicians=DEFAULT_TECHNICIANS,
    )
    assert result.solver_status == "OPTIMAL"
    assert result.total_distance_meters == 0.0
    assert result.confidence == 1.0
    assert len(result.unassigned_task_ids) == 0


def test_cvrp_solver_exceeded_shift_capacity(sample_facility_id: uuid.UUID) -> None:
    """Verifies that overloaded technician shifts drop tasks into unassigned_task_ids."""
    heavy_tasks = [
        {
            "item_id": f"heavy_task_{i}",
            "asset_id": uuid.uuid4(),
            "estimated_duration_hours": 5.0,  # 5 hours each
        }
        for i in range(4)  # 20 hours total
    ]
    # Single technician with only 6-hour shift (360 mins)
    short_shift_tech = [
        {
            "technician_id": "tech_short",
            "technician_name": "Taylor",
            "skills": ["general"],
            "max_shift_minutes": 360.0,
            "depot": (0.0, 0.0),
        }
    ]

    result = solve_cvrp_routes(
        facility_id=sample_facility_id,
        tasks=heavy_tasks,
        technicians=short_shift_tech,
    )
    assert len(result.unassigned_task_ids) > 0
    assert result.confidence < 0.90


@pytest.mark.anyio
async def test_route_optimization_callable_and_node(
    sample_tenant_id: uuid.UUID,
    sample_facility_id: uuid.UUID,
    sample_tasks: list[dict[str, Any]],
    sample_coordinates: dict[str, tuple[float, float]],
) -> None:
    """Verifies real route optimization callable and LangGraph node execution."""
    state = create_initial_state(str(sample_tenant_id), str(sample_facility_id))
    state["maintenance_schedule"] = sample_tasks

    config: RunnableConfig = {
        "configurable": {
            "asset_coordinates": sample_coordinates,
        }
    }

    result_delta = await route_optimization_node(state, config=config)
    assert "dispatch_routes" in result_delta
    assert len(result_delta["dispatch_routes"]) == 1
    assert result_delta["iteration_count"] == 1


@pytest.mark.anyio
async def test_route_optimization_node_escalates_on_invalid_output(
    sample_tenant_id: uuid.UUID,
    sample_facility_id: uuid.UUID,
) -> None:
    """Verifies fail-fast escalation when route optimization produces invalid output."""
    state = create_initial_state(str(sample_tenant_id), str(sample_facility_id))

    async def bad_callable(ctx: dict[str, Any]) -> dict[str, Any]:
        return {
            "optimization_id": str(uuid.uuid4()),
            "facility_id": ctx.get("facility_id", str(uuid.uuid4())),
            "solver_status": "INVALID_STATUS",  # Invalid enum
            "solver_duration_seconds": 12.0,     # Exceeds max 10.0
            "confidence": 1.5,                  # Exceeds 1.0
        }

    config: RunnableConfig = {"configurable": {"route_optimization_callable": bad_callable}}
    result_delta = await route_optimization_node(state, config=config)

    assert result_delta["halted_for_escalation"] is True
    assert "route_optimization" in result_delta["escalation_reason"].lower()
    assert len(result_delta["validation_errors"]) >= 1
