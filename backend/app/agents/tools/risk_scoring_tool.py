"""Risk Scoring Tool for Agent 2 — Risk Assessment (PROJECT_PLAN.md §4.3).

Wraps Phase 3's inference service (`score_facility`) as an agent tool to generate
predictive failure risk scores (0–100) and telemetry factors across facility assets.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.risk_inference_service import score_facility

logger = logging.getLogger("agents.tools.risk_scoring")


async def score_facility_assets_tool(
    main_session: AsyncSession,
    timescale_session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    model_version: str | None = None,
) -> list[dict[str, Any]]:
    """Runs Phase 3 inference service against TimescaleDB telemetry and XGBoost model.

    Returns a list of scored asset dictionaries containing:
    asset_id, risk_score, factors, and model_version.
    """
    logger.info(
        "Invoking score_facility tool: tenant_id=%s, facility_id=%s, model_version=%s",
        tenant_id,
        facility_id,
        model_version,
    )
    score_rows = await score_facility(
        main_session,
        timescale_session,
        tenant_id=tenant_id,
        facility_id=facility_id,
        model_version=model_version,
    )

    results: list[dict[str, Any]] = []
    for row in score_rows:
        results.append(
            {
                "asset_id": row.asset_id,
                "risk_score": float(row.score),
                "factors": dict(row.factors_json or {}),
                "model_version": row.model_version,
                "computed_at": row.computed_at or datetime.now(UTC),
            }
        )
    return results


def score_assets_from_records_tool(
    asset_records: list[dict[str, Any]],
    *,
    model_version: str = "xgboost-ai4i-v1.0",
) -> list[dict[str, Any]]:
    """Simulates/evaluates risk scores directly from asset records for tests or standalone mode.

    Generates realistic predictive scores and factors context when live DB sessions
    are not attached.
    """
    results: list[dict[str, Any]] = []
    now = datetime.now(UTC)

    for record in asset_records:
        asset_id_raw = record.get("id") or record.get("asset_id") or uuid.uuid4()
        asset_id = (
            uuid.UUID(str(asset_id_raw)) if isinstance(asset_id_raw, str) else asset_id_raw
        )
        base_score = float(record.get("risk_score", 0.0))
        factors = dict(record.get("factors") or {})

        # If risk_score not explicitly given, derive from status or anomaly count
        if base_score <= 0.0:
            status_val = str(record.get("status", "operational")).lower()
            anomaly_count = int(record.get("telemetry_anomaly_count_30d", 0))
            if status_val == "offline":
                base_score = 92.5
            elif status_val == "maintenance":
                base_score = 55.0
            elif anomaly_count >= 3:
                base_score = 78.4
            elif anomaly_count >= 1:
                base_score = 42.0
            else:
                base_score = 15.0

        if not factors:
            factors = {
                "asset_status": record.get("status", "operational"),
                "air_temperature_30d_anomaly_count": record.get("air_temp_anomalies", 0),
                "torque_30d_anomaly_count": record.get("torque_anomalies", 0),
                "tool_wear_30d_anomaly_count": record.get("tool_wear_anomalies", 0),
                "rotational_speed_30d_latest_value": record.get("speed_rpm", 1500.0),
                "temperature_difference_30d_latest_value": record.get("temp_diff_k", 10.0),
            }

        results.append(
            {
                "asset_id": asset_id,
                "risk_score": round(max(0.0, min(100.0, base_score)), 1),
                "factors": factors,
                "model_version": record.get("model_version", model_version),
                "computed_at": now,
            }
        )

    return results
