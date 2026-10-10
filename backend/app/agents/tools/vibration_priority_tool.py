"""Agent 3 tool: fold vibration anomaly alarms (and RUL) into maintenance urgency.

The scheduler maps `risk_score` to a priority window. A sustained vibration alarm is
independent evidence of bearing degradation that the telemetry-based risk score may not
see, so it lifts the asset's *effective* score to a floor:

  * alarm                          -> at least HIGH   (50)
  * alarm and RUL <= LOW_RUL_CYCLES -> at least CRITICAL (67)

Scores are only ever raised, never lowered; inputs are not mutated; malformed assessments
are validated (`VibrationAssessment`) and dropped with a warning rather than trusted.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from pydantic import ValidationError

from app.schemas.ml.vibration_result import VibrationAssessment

logger = logging.getLogger("agents.tools.vibration_priority")

HIGH_FLOOR = 50.0
CRITICAL_FLOOR = 67.0
LOW_RUL_CYCLES = 30.0


def _alarmed_assets(assessments: list[dict[str, Any]]) -> set[uuid.UUID]:
    alarmed: set[uuid.UUID] = set()
    for raw in assessments:
        try:
            parsed = VibrationAssessment.model_validate(raw)
        except ValidationError as exc:
            logger.warning("dropping invalid vibration assessment: %s", exc.error_count())
            continue
        if parsed.alarm:
            alarmed.add(parsed.asset_id)
    return alarmed


def apply_vibration_signals(
    scored_assets: list[dict[str, Any]], assessments: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Copy of `scored_assets` with risk raised for assets with a vibration alarm."""
    if not assessments:
        return scored_assets
    alarmed = _alarmed_assets(assessments)
    out: list[dict[str, Any]] = []
    for asset in scored_assets:
        raw_id = asset.get("asset_id") or asset.get("id")
        try:
            asset_id = raw_id if isinstance(raw_id, uuid.UUID) else uuid.UUID(str(raw_id))
        except ValueError:
            out.append(asset)
            continue
        if asset_id not in alarmed:
            out.append(asset)
            continue
        rul = asset.get("rul_cycles")
        floor = CRITICAL_FLOOR if rul is not None and float(rul) <= LOW_RUL_CYCLES else HIGH_FLOOR
        score = float(asset.get("risk_score", 0.0))
        out.append(
            {
                **asset,
                "risk_score": max(score, floor),
                "vibration_alarm": True,
                "original_risk_score": score,
            }
        )
    return out
