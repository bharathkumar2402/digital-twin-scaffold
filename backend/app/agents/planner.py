"""Agent 1 — Planner Agent LangGraph node (PROJECT_PLAN.md §4.3).

Decomposes incoming triggers/queries into agent tasks and loads the asset dependency
graph topology. Validates all outputs against PlannerOutput before updating state.
"""

import logging
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.state import MAX_GRAPH_ITERATIONS, FacilityTwinState
from app.agents.validation import AgentEscalationRequired, execute_agent_with_retry
from app.schemas.agent_outputs.planner import PlannerOutput

logger = logging.getLogger("agents.planner")


async def default_planner_callable(context: dict[str, Any]) -> dict[str, Any]:
    """Default placeholder callable for the Planner Agent producing valid dummy output."""
    facility_id_raw = context.get("facility_id", str(uuid.uuid4()))
    facility_id = (
        uuid.UUID(facility_id_raw) if isinstance(facility_id_raw, str) else facility_id_raw
    )
    asset_1 = uuid.uuid4()
    asset_2 = uuid.uuid4()

    return {
        "plan_id": str(uuid.uuid4()),
        "goal": "Decompose facility state and coordinate asset health analysis",
        "target_facility_id": str(facility_id),
        "active_agents": [
            "risk_assessment",
            "maintenance_inventory",
            "route_optimization",
            "simulation_decision",
        ],
        "tasks": [
            {
                "task_id": "task_risk_eval",
                "agent": "risk_assessment",
                "action": "Evaluate telemetry risk scores across facility assets",
                "priority": 1,
                "dependencies": [],
                "target_asset_ids": [str(asset_1), str(asset_2)],
            },
            {
                "task_id": "task_maintenance_plan",
                "agent": "maintenance_inventory",
                "action": "Plan maintenance window and check spare parts inventory",
                "priority": 2,
                "dependencies": ["task_risk_eval"],
                "target_asset_ids": [str(asset_1)],
            },
        ],
        "asset_graph_summary": {
            "total_nodes": 2,
            "total_edges": 1,
            "root_asset_ids": [str(asset_1)],
            "critical_path_asset_ids": [str(asset_1)],
        },
        "reasoning": "Standard scheduled analysis decomposing topology and queuing assessment.",
        "confidence": 0.95,
    }


async def planner_node(
    state: FacilityTwinState,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """LangGraph node executing the Planner Agent with schema validation and retry harness."""
    if state.get("halted_for_escalation"):
        return {}

    current_iterations = state.get("iteration_count", 0)
    if current_iterations >= MAX_GRAPH_ITERATIONS:
        logger.error(
            "Planner node loop guard triggered: %d >= %d", current_iterations, MAX_GRAPH_ITERATIONS
        )
        return {
            "halted_for_escalation": True,
            "escalation_reason": "Loop guard exceeded maximum iterations",
            "errors": [
                f"Loop guard exceeded maximum iterations ({MAX_GRAPH_ITERATIONS}) in planner_node"
            ],
            "iteration_count": current_iterations + 1,
        }

    configurable = (config or {}).get("configurable", {})
    agent_fn = configurable.get("planner_callable", default_planner_callable)

    context: dict[str, Any] = {
        "tenant_id": state["tenant_id"],
        "facility_id": state["facility_id"],
        "trigger": state["trigger"],
        "user_query": state.get("user_query"),
    }

    try:
        result = await execute_agent_with_retry("planner", PlannerOutput, agent_fn, context)
        validated: PlannerOutput = result.output

        return {
            "asset_graph": {
                "summary": validated.asset_graph_summary.model_dump(mode="json"),
                "tasks": [t.model_dump(mode="json") for t in validated.tasks],
                "plan_id": str(validated.plan_id),
                "active_agents": list(validated.active_agents),
                "reasoning": validated.reasoning,
            },
            "confidence": validated.confidence,
            "iteration_count": current_iterations + 1,
        }
    except AgentEscalationRequired as exc:
        logger.error("Planner failed schema validation after retry. Halting branch for escalation.")
        return {
            "halted_for_escalation": True,
            "escalation_reason": str(exc),
            "validation_errors": list(exc.validation_errors),
            "errors": [f"Planner validation escalation: {exc}"],
            "iteration_count": current_iterations + 1,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in planner_node: %s", exc)
        return {
            "halted_for_escalation": True,
            "escalation_reason": f"Unhandled exception in planner_node: {exc}",
            "errors": [f"Planner node runtime error: {exc}"],
            "iteration_count": current_iterations + 1,
        }
