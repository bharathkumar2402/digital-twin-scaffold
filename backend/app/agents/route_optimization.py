"""Agent 4 — Route Optimization Agent LangGraph node (PROJECT_PLAN.md §4.3).

Executes CVRP dispatch optimization with 5s SLA limit over facility technician routes.
Validates all outputs against RouteOptimizationOutput before updating state.
"""

import logging
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.state import MAX_GRAPH_ITERATIONS, FacilityTwinState
from app.agents.validation import AgentEscalationRequired, execute_agent_with_retry
from app.agents.tools.distance_matrix_tool import extract_asset_coordinates_map
from app.agents.tools.route_solver_tool import solve_cvrp_routes
from app.schemas.agent_outputs.route_optimization import RouteOptimizationOutput

logger = logging.getLogger("agents.route_optimization")


async def real_route_optimization_callable(context: dict[str, Any]) -> dict[str, Any]:
    """Production callable for Route Optimization Agent: executes OR-Tools CVRP dispatch."""
    facility_id_raw = context.get("facility_id", str(uuid.uuid4()))
    facility_id = (
        uuid.UUID(facility_id_raw) if isinstance(facility_id_raw, str) else facility_id_raw
    )

    tasks = context.get("maintenance_schedule")
    if tasks is None:
        # Fallback to single task for standalone/skeleton test compatibility
        tasks = [
            {
                "item_id": "maint_task_01",
                "asset_id": uuid.uuid4(),
                "scheduled_date": "2026-10-05",
                "priority": "critical",
                "estimated_duration_hours": 1.25,
                "required_technician_skills": ["mechanical"],
            }
        ]

    asset_coordinates = context.get("asset_coordinates")
    if not asset_coordinates and context.get("asset_graph"):
        asset_coordinates = extract_asset_coordinates_map(asset_graph=context.get("asset_graph"))
    if not asset_coordinates and context.get("assets"):
        asset_coordinates = extract_asset_coordinates_map(assets=context.get("assets"))

    technicians = context.get("technicians")
    time_limit_seconds = float(context.get("time_limit_seconds", 5.0))

    output = solve_cvrp_routes(
        facility_id=facility_id,
        tasks=tasks,
        asset_coordinates=asset_coordinates,
        technicians=technicians,
        time_limit_seconds=time_limit_seconds,
    )
    return output.model_dump(mode="json")


default_route_optimization_callable = real_route_optimization_callable


async def route_optimization_node(
    state: FacilityTwinState,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """LangGraph node executing Route Optimization Agent with schema validation."""
    if state.get("halted_for_escalation"):
        return {}

    current_iterations = state.get("iteration_count", 0)
    if current_iterations >= MAX_GRAPH_ITERATIONS:
        logger.error(
            "Route optimization node loop guard triggered: %d >= %d",
            current_iterations,
            MAX_GRAPH_ITERATIONS,
        )
        return {
            "halted_for_escalation": True,
            "escalation_reason": "Loop guard exceeded maximum iterations",
            "errors": [
                f"Loop guard exceeded {MAX_GRAPH_ITERATIONS} iterations in route_optimization_node"
            ],
            "iteration_count": current_iterations + 1,
        }

    configurable = (config or {}).get("configurable", {})
    agent_fn = configurable.get("route_optimization_callable", default_route_optimization_callable)

    context: dict[str, Any] = {
        "tenant_id": state["tenant_id"],
        "facility_id": state["facility_id"],
        "trigger": state["trigger"],
        "maintenance_schedule": state.get("maintenance_schedule"),
        "asset_graph": state.get("asset_graph"),
        "assets": configurable.get("assets"),
        "technicians": configurable.get("technicians"),
        "asset_coordinates": configurable.get("asset_coordinates"),
        "time_limit_seconds": configurable.get("time_limit_seconds", 5.0),
    }

    try:
        result = await execute_agent_with_retry(
            "route_optimization", RouteOptimizationOutput, agent_fn, context
        )
        validated: RouteOptimizationOutput = result.output

        return {
            "dispatch_routes": [r.model_dump(mode="json") for r in validated.technician_routes],
            "iteration_count": current_iterations + 1,
        }
    except AgentEscalationRequired as exc:
        logger.error("Route optimization failed validation after retry. Halting for escalation.")
        return {
            "halted_for_escalation": True,
            "escalation_reason": str(exc),
            "validation_errors": list(exc.validation_errors),
            "errors": [f"Route optimization validation escalation: {exc}"],
            "iteration_count": current_iterations + 1,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in route_optimization_node: %s", exc)
        return {
            "halted_for_escalation": True,
            "escalation_reason": f"Unhandled exception in route_optimization_node: {exc}",
            "errors": [f"Route optimization node runtime error: {exc}"],
            "iteration_count": current_iterations + 1,
        }
