"""Agent 5 — Simulation & Decision Agent LangGraph node (PROJECT_PLAN.md §4.3).

Executes cascade failure propagation simulation over dependency graph and synthesizes
executive decision summary. Validates all outputs against SimulationDecisionOutput.
"""

import logging
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.state import MAX_GRAPH_ITERATIONS, FacilityTwinState
from app.agents.validation import AgentEscalationRequired, execute_agent_with_retry
from app.schemas.agent_outputs.simulation_decision import SimulationDecisionOutput

logger = logging.getLogger("agents.simulation_decision")


async def default_simulation_decision_callable(context: dict[str, Any]) -> dict[str, Any]:
    """Default placeholder callable for Simulation & Decision Agent producing valid dummy output."""
    facility_id_raw = context.get("facility_id", str(uuid.uuid4()))
    facility_id = (
        uuid.UUID(facility_id_raw) if isinstance(facility_id_raw, str) else facility_id_raw
    )
    asset_id = uuid.uuid4()

    trigger = context.get("trigger", "scheduled")
    scenario_trigger = "anomaly_alert" if trigger == "alert" else trigger
    if scenario_trigger not in ("user_query", "scheduled", "anomaly_alert"):
        scenario_trigger = "scheduled"

    return {
        "decision_id": str(uuid.uuid4()),
        "facility_id": str(facility_id),
        "scenario_trigger": scenario_trigger,
        "cascade_impact": {
            "root_cause_asset_id": str(asset_id),
            "directly_affected_asset_ids": [],
            "downstream_shutoff_asset_ids": [],
            "total_affected_assets": 1,
            "critical_subsystems_interrupted": [],
            "estimated_downtime_hours": 2.5,
            "cascade_depth": 0,
        },
        "executive_summary": (
            "Multi-agent digital twin analysis concluded successfully. "
            "Asset maintenance scheduled and technician dispatch route generated."
        ),
        "confidence_score": 0.94,
        "human_escalation_required": False,
        "escalation_reason": None,
        "recommended_interventions": [
            "Proceed with planned preventive maintenance on identified asset"
        ],
    }


async def simulation_decision_node(
    state: FacilityTwinState,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """LangGraph node executing Simulation & Decision Agent with schema validation."""
    if state.get("halted_for_escalation"):
        return {}

    current_iterations = state.get("iteration_count", 0)
    if current_iterations >= MAX_GRAPH_ITERATIONS:
        logger.error(
            "Simulation decision node loop guard triggered: %d >= %d",
            current_iterations,
            MAX_GRAPH_ITERATIONS,
        )
        return {
            "halted_for_escalation": True,
            "escalation_reason": "Loop guard exceeded maximum iterations",
            "errors": [
                f"Loop guard exceeded {MAX_GRAPH_ITERATIONS} iterations in simulation_decision_node"
            ],
            "iteration_count": current_iterations + 1,
        }

    configurable = (config or {}).get("configurable", {})
    agent_fn = configurable.get(
        "simulation_decision_callable", default_simulation_decision_callable
    )

    context: dict[str, Any] = {
        "tenant_id": state["tenant_id"],
        "facility_id": state["facility_id"],
        "trigger": state["trigger"],
        "user_query": state.get("user_query"),
        "asset_graph": state.get("asset_graph"),
        "risk_scores": state.get("risk_scores"),
        "maintenance_schedule": state.get("maintenance_schedule"),
        "inventory_gaps": state.get("inventory_gaps"),
        "dispatch_routes": state.get("dispatch_routes"),
    }

    try:
        result = await execute_agent_with_retry(
            "simulation_decision", SimulationDecisionOutput, agent_fn, context
        )
        validated: SimulationDecisionOutput = result.output

        return {
            "simulation_result": validated.cascade_impact.model_dump(mode="json"),
            "decision_report": validated.executive_summary,
            "confidence": validated.confidence_score,
            "iteration_count": current_iterations + 1,
        }
    except AgentEscalationRequired as exc:
        logger.error("Simulation decision failed validation after retry. Halting for escalation.")
        return {
            "halted_for_escalation": True,
            "escalation_reason": str(exc),
            "validation_errors": list(exc.validation_errors),
            "errors": [f"Simulation decision validation escalation: {exc}"],
            "iteration_count": current_iterations + 1,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in simulation_decision_node: %s", exc)
        return {
            "halted_for_escalation": True,
            "escalation_reason": f"Unhandled exception in simulation_decision_node: {exc}",
            "errors": [f"Simulation decision node runtime error: {exc}"],
            "iteration_count": current_iterations + 1,
        }
