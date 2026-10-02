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
from app.schemas.agent_outputs.route_optimization import RouteOptimizationOutput

logger = logging.getLogger("agents.route_optimization")


async def default_route_optimization_callable(context: dict[str, Any]) -> dict[str, Any]:
    """Default placeholder callable for Route Optimization Agent producing valid dummy output."""
    facility_id_raw = context.get("facility_id", str(uuid.uuid4()))
    facility_id = (
        uuid.UUID(facility_id_raw) if isinstance(facility_id_raw, str) else facility_id_raw
    )
    asset_id = uuid.uuid4()

    return {
        "optimization_id": str(uuid.uuid4()),
        "facility_id": str(facility_id),
        "solver_status": "OPTIMAL",
        "solver_duration_seconds": 1.45,
        "technician_routes": [
            {
                "technician_id": "tech_alpha",
                "technician_name": "Jordan Lee",
                "assigned_stops": [
                    {
                        "stop_number": 1,
                        "asset_id": str(asset_id),
                        "task_id": "maint_task_01",
                        "estimated_arrival_minutes": 12.0,
                        "service_duration_minutes": 75.0,
                        "travel_time_from_previous_minutes": 12.0,
                    }
                ],
                "total_travel_minutes": 12.0,
                "total_service_minutes": 75.0,
                "total_route_duration_minutes": 87.0,
            }
        ],
        "unassigned_task_ids": [],
        "total_distance_meters": 280.0,
        "confidence": 0.95,
    }


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
        logger.error(
            "Route optimization failed validation after retry. Halting for escalation."
        )
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
