"""Agent 2 — Risk Assessment Agent LangGraph node (PROJECT_PLAN.md §4.3).

Executes ML risk inference and prioritization over facility assets. Validates all outputs
against RiskAssessmentOutput before updating state.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig

from app.agents.state import MAX_GRAPH_ITERATIONS, FacilityTwinState
from app.agents.tools.risk_scoring_tool import (
    score_assets_from_records_tool,
    score_facility_assets_tool,
)
from app.agents.validation import AgentEscalationRequired, execute_agent_with_retry
from app.schemas.agent_outputs.risk_assessment import (
    RiskAssessmentOutput,
    ScoredAsset,
)
from app.services.ml.dataset_mapping import calculate_mechanical_power

logger = logging.getLogger("agents.risk_assessment")


def _diagnose_failure_mode(
    risk_score: float,
    factors: dict[str, Any],
) -> tuple[str | None, list[str]]:
    """Diagnoses likely failure mode and explanatory factors from telemetry signals."""
    if risk_score < 34.0:
        return None, ["Sensor telemetry operating within normal empirical baselines."]

    reasons: list[str] = []
    temp_diff = float(factors.get("temperature_difference_30d_latest_value", 10.0))
    air_temp_anom = int(factors.get("air_temperature_30d_anomaly_count", 0))
    torque_anom = int(factors.get("torque_30d_anomaly_count", 0))
    tool_wear_anom = int(factors.get("tool_wear_30d_anomaly_count", 0))
    speed_rpm = float(factors.get("rotational_speed_30d_latest_value", 1500.0))
    torque_nm = float(factors.get("torque_30d_latest_value", 40.0))
    offline_neighbors = int(factors.get("dependency_neighbor_offline_count", 0))

    # Calculate mechanical power in Watts (P = torque [Nm] * speed [rad/s])
    power_w = float(
        factors.get(
            "mechanical_power_30d_latest_value",
            factors.get("mechanical_power", calculate_mechanical_power(torque_nm, speed_rpm)),
        )
    )

    predicted_mode: str | None = None

    if temp_diff < 8.6 or air_temp_anom >= 2:
        predicted_mode = "HDF"
        reasons.append(
            f"Heat dissipation deficit detected (temp delta={temp_diff:.1f}K < 8.6K limit)."
        )
    elif tool_wear_anom >= 2 or float(factors.get("tool_wear_30d_latest_value", 0.0)) > 200.0:
        predicted_mode = "TWF"
        reasons.append("Tool wear accumulated past scheduled replacement threshold.")
    elif torque_anom >= 2 or torque_nm > 60.0:
        predicted_mode = "OSF"
        reasons.append(f"Mechanical torque stress ({torque_nm:.1f}Nm) approaching strain limits.")
    elif power_w < 3500.0 or power_w > 9000.0:
        predicted_mode = "PWF"
        reasons.append(
            f"Mechanical power draw abnormal ({power_w:.0f}W outside operating envelope)."
        )
    else:
        predicted_mode = "RNF"
        reasons.append("Anomalous multi-sensor variance identified across rolling window.")

    if offline_neighbors > 0:
        reasons.append(
            f"Compounded risk: {offline_neighbors} upstream dependency neighbor(s) offline."
        )

    return predicted_mode, reasons


def prioritize_and_explain_risks(
    *,
    facility_id: uuid.UUID,
    raw_scores: list[dict[str, Any]],
    model_version: str = "xgboost-ai4i-v1.0",
    assessed_at: datetime | None = None,
) -> RiskAssessmentOutput:
    """Ranks assets by urgency, classifies risk bands, and diagnoses failure modes."""
    now = assessed_at or datetime.now(UTC)

    if not raw_scores:
        return RiskAssessmentOutput(
            assessment_id=uuid.uuid4(),
            facility_id=facility_id,
            model_version=model_version,
            assessed_at=now,
            ranked_assets=[],
            high_risk_count=0,
            medium_risk_count=0,
            low_risk_count=0,
            executive_summary="No facility assets evaluated in risk assessment.",
            confidence=0.90,
        )

    # Sort descending by risk score
    sorted_raw = sorted(
        raw_scores,
        key=lambda item: float(item.get("risk_score", 0.0)),
        reverse=True,
    )

    ranked_assets: list[ScoredAsset] = []
    for rank_idx, record in enumerate(sorted_raw, start=1):
        asset_id_raw = record.get("asset_id") or record.get("id")
        if isinstance(asset_id_raw, uuid.UUID):
            asset_id = asset_id_raw
        elif isinstance(asset_id_raw, str):
            asset_id = uuid.UUID(asset_id_raw)
        else:
            asset_id = uuid.uuid4()

        score = float(record.get("risk_score", 0.0))
        factors = dict(record.get("factors") or {})

        # Risk tier banding
        tier: Literal["low", "medium", "high", "critical"]
        if score >= 67.0:
            tier = "critical"
            recommended = "Immediate operational intervention and cooling/bearing diagnostic."
        elif score >= 50.0:
            tier = "high"
            recommended = "Schedule prioritized inspection and preventive overhaul within 48 hours."
        elif score >= 34.0:
            tier = "medium"
            recommended = (
                "Monitor telemetry deviations closely during next 14-day shift inspection."
            )
        else:
            tier = "low"
            recommended = "Normal operational parameters; maintain standard preventive maintenance."

        pred_mode, primary_factors = _diagnose_failure_mode(score, factors)

        # Anomaly count calculation
        anomaly_count = sum(
            int(v)
            for k, v in factors.items()
            if "anomaly_count" in k and isinstance(v, (int, float))
        )

        ranked_assets.append(
            ScoredAsset(
                asset_id=asset_id,
                risk_score=round(score, 1),
                urgency_rank=rank_idx,
                risk_tier=tier,
                predicted_failure_mode=pred_mode,
                primary_risk_factors=primary_factors,
                telemetry_anomaly_count_30d=anomaly_count,
                recommended_action=recommended,
            )
        )

    high_count = sum(1 for a in ranked_assets if a.risk_score >= 67.0)
    medium_count = sum(1 for a in ranked_assets if 34.0 <= a.risk_score < 67.0)
    low_count = sum(1 for a in ranked_assets if a.risk_score < 34.0)

    # Synthesize natural language executive summary
    if high_count > 0:
        lead_asset = ranked_assets[0]
        exec_summary = (
            f"Urgent attention required: {high_count} asset(s) in critical risk tier (>= 67). "
            f"Highest priority is Asset {lead_asset.asset_id} with risk score "
            f"{lead_asset.risk_score} and diagnosed "
            f"{lead_asset.predicted_failure_mode or 'telemetry'} failure mode. "
            f"{medium_count} medium-risk and {low_count} low-risk assets evaluated."
        )
    elif medium_count > 0:
        exec_summary = (
            f"Moderate facility risk: {medium_count} asset(s) in medium risk tier (34-66). "
            f"No critical failures detected. Routine maintenance scheduling recommended."
        )
    else:
        exec_summary = (
            f"Facility telemetry stable: all {low_count} asset(s) "
            "operating within low risk bounds (< 34)."
        )

    return RiskAssessmentOutput(
        assessment_id=uuid.uuid4(),
        facility_id=facility_id,
        model_version=model_version,
        assessed_at=now,
        ranked_assets=ranked_assets,
        high_risk_count=high_count,
        medium_risk_count=medium_count,
        low_risk_count=low_count,
        executive_summary=exec_summary,
        confidence=0.94,
    )


async def real_risk_assessment_callable(context: dict[str, Any]) -> dict[str, Any]:
    """Production callable for Risk Assessment Agent: wraps inference service and explains risks."""
    facility_id_raw = context.get("facility_id", str(uuid.uuid4()))
    facility_id = (
        uuid.UUID(facility_id_raw) if isinstance(facility_id_raw, str) else facility_id_raw
    )
    tenant_id_raw = context.get("tenant_id", str(uuid.uuid4()))
    tenant_id = (
        uuid.UUID(tenant_id_raw) if isinstance(tenant_id_raw, str) else tenant_id_raw
    )
    model_version = str(context.get("model_version", "xgboost-ai4i-v1.0"))

    main_session = context.get("main_session")
    timescale_session = context.get("timescale_session")

    if main_session is not None and timescale_session is not None:
        raw_scores = await score_facility_assets_tool(
            main_session,
            timescale_session,
            tenant_id=tenant_id,
            facility_id=facility_id,
            model_version=model_version,
        )
    elif context.get("scored_assets") is not None:
        raw_scores = context.get("scored_assets", [])
    elif context.get("assets") is not None:
        raw_scores = score_assets_from_records_tool(
            context.get("assets", []),
            model_version=model_version,
        )
    else:
        # Default mock asset scoring for standalone pipeline execution
        mock_asset_1 = uuid.uuid4()
        raw_scores = score_assets_from_records_tool(
            [
                {
                    "id": mock_asset_1,
                    "name": "Feedwater Pump 1",
                    "status": "operational",
                    "risk_score": 78.4,
                    "air_temp_anomalies": 3,
                    "temp_diff_k": 7.8,
                },
            ],
            model_version=model_version,
        )

    output = prioritize_and_explain_risks(
        facility_id=facility_id,
        raw_scores=raw_scores,
        model_version=model_version,
    )
    return output.model_dump(mode="json")


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
    agent_fn = configurable.get("risk_assessment_callable", real_risk_assessment_callable)

    context: dict[str, Any] = {
        "tenant_id": state["tenant_id"],
        "facility_id": state["facility_id"],
        "trigger": state["trigger"],
        "asset_graph": state.get("asset_graph"),
        "main_session": configurable.get("main_session"),
        "timescale_session": configurable.get("timescale_session"),
        "scored_assets": configurable.get("scored_assets"),
        "assets": configurable.get("assets"),
        "model_version": configurable.get("model_version", "xgboost-ai4i-v1.0"),
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
