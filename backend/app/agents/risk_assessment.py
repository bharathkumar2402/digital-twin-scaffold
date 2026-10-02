"""Agent 2 — Risk Assessment Agent LangGraph node (PROJECT_PLAN.md §4.3).

Executes ML risk inference and prioritization over facility assets. Validates all outputs
against RiskAssessmentOutput before updating state.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.state import MAX_GRAPH_ITERATIONS, FacilityTwinState
from app.agents.validation import AgentEscalationRequired, execute_agent_with_retry
from app.schemas.agent_outputs.risk_assessment import RiskAssessmentOutput

logger = logging.getLogger("agents.risk_assessment")


async def default_risk_assessment_callable(context: dict[str, Any]) -> dict[str, Any]:
    """Default placeholder callable for the Risk Assessment Agent producing valid dummy output."""
    facility_id_raw = context.get("facility_id", str(uuid.uuid4()))
    facility_id = (
        uuid.UUID(facility_id_raw) if isinstance(facility_id_raw, str) else facility_id_raw
    )
    asset_id = uuid.uuid4()

    return {
        "assessment_id": str(uuid.uuid4()),
        "facility_id": str(facility_id),
        "model_version": "xgboost-ai4i-v1.0",
        "assessed_at": datetime.now(UTC).isoformat(),
        "ranked_assets": [
            {
                "asset_id": str(asset_id),
                "risk_score": 78.4,
                "urgency_rank": 1,
                "risk_tier": "critical",
                "predicted_failure_mode": "HDF",
                "primary_risk_factors": ["Heat dissipation threshold breach", "Torque variance"],
                "telemetry_anomaly_count_30d": 3,
                "recommended_action": "Inspect cooling lines and bearing lubrication",
            }
        ],
        "high_risk_count": 1,
        "medium_risk_count": 0,
        "low_risk_count": 0,
        "executive_summary": (
            "1 asset identified in critical risk tier requiring prompt inspection."
        ),
        "confidence": 0.92,
    }


async def risk_assessment_node(
    state: FacilityTwinState,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """LangGraph node executing the Risk Assessment Agent with validation and retry harness."""
    if state.get("halted_for_escalation"):
        return {}

    current_iterations = state.get("iteration_count", 0)
    if current_iterations >= MAX_GRAPH_ITERATIONS:
        logger.error(
            "Risk assessment node loop guard triggered: %d >= %d",
            current_iterations,
            MAX_GRAPH_ITERATIONS,
        )
        return {
            "halted_for_escalation": True,
            "escalation_reason": "Loop guard exceeded maximum iterations",
            "errors": [
                f"Loop guard exceeded {MAX_GRAPH_ITERATIONS} iterations in risk_assessment_node"
            ],
            "iteration_count": current_iterations + 1,
        }

    configurable = (config or {}).get("configurable", {})
    agent_fn = configurable.get("risk_assessment_callable", default_risk_assessment_callable)

    context: dict[str, Any] = {
        "tenant_id": state["tenant_id"],
        "facility_id": state["facility_id"],
        "trigger": state["trigger"],
        "asset_graph": state.get("asset_graph"),
    }

    try:
        result = await execute_agent_with_retry(
            "risk_assessment", RiskAssessmentOutput, agent_fn, context
        )
        validated: RiskAssessmentOutput = result.output

        return {
            "risk_scores": [a.model_dump(mode="json") for a in validated.ranked_assets],
            "iteration_count": current_iterations + 1,
        }
    except AgentEscalationRequired as exc:
        logger.error(
            "Risk assessment failed schema validation after retry. Halting branch for escalation."
        )
        return {
            "halted_for_escalation": True,
            "escalation_reason": str(exc),
            "validation_errors": list(exc.validation_errors),
            "errors": [f"Risk assessment validation escalation: {exc}"],
            "iteration_count": current_iterations + 1,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in risk_assessment_node: %s", exc)
        return {
            "halted_for_escalation": True,
            "escalation_reason": f"Unhandled exception in risk_assessment_node: {exc}",
            "errors": [f"Risk assessment node runtime error: {exc}"],
            "iteration_count": current_iterations + 1,
        }
