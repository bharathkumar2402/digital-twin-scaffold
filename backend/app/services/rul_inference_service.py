"""Remaining Useful Life inference over live TimescaleDB telemetry (Phase 3b, task 3b.7a).

For every asset that reports the 24 C-MAPSS channels (21 sensors + 3 operating settings,
see `dataset_mapping.CMAPSS_TELEMETRY_TYPES`), assemble the asset's most recent complete
snapshots into a window, build features with the same `window_features` used in training,
and predict RUL with the versioned XGBoost model from MinIO.

Design rules:
  * A "cycle" is one complete snapshot (all 24 channels at one timestamp). The `cycle`
    feature is the asset's age in complete snapshots, counted in the database, not the
    index inside the window - the model was trained on engine age.
  * Fails closed per asset: no telemetry, an incomplete window, an unseen operating regime
    or invalid values yield *no* RUL plus a reason, never a guessed number. A missing model
    degrades to "no RUL for anyone" so risk scoring itself keeps working. A model/feature
    layout mismatch is a deployment error and raises.
  * Output is a validated `RulEstimate` (bounded, versioned) before it is used anywhere.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
from minio.error import S3Error
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from urllib3.exceptions import HTTPError as Urllib3HTTPError
from xgboost import XGBRegressor

from app.core.config import settings
from app.ml.cmapss_profile import SENSOR_COLUMNS
from app.ml.rul_features import RulFeatureConfig, window_features
from app.ml.rul_train import load_rul_model
from app.schemas.ml.rul_features import RulSnapshot, RulWindowIn
from app.schemas.ml.rul_result import RulEstimate
from app.services.ml.dataset_mapping import (
    CMAPSS_OP_SETTING_TYPES,
    CMAPSS_SENSOR_MAP,
    CMAPSS_TELEMETRY_TYPES,
)

logger = logging.getLogger("services.rul_inference")

N_CHANNELS = len(CMAPSS_TELEMETRY_TYPES)  # 3 operating settings + 21 sensors
# Rows fetched per asset: enough for every complete snapshot in the window even if a few
# snapshots are missing channels.
_FETCH_SLACK_CYCLES = 20

SKIP_NO_TELEMETRY = "no_cmapss_telemetry"
SKIP_INCOMPLETE = "no_complete_snapshot"
SKIP_INVALID = "invalid_telemetry"
SKIP_UNSEEN_REGIME = "unseen_operating_condition"
SKIP_MODEL_UNAVAILABLE = "rul_model_unavailable"

_SNAPSHOT_ROWS_SQL = text(
    """
    SELECT timestamp, sensor_type, value
    FROM sensor_readings
    WHERE tenant_id = :tenant_id
      AND asset_id = :asset_id
      AND sensor_type = ANY(:types)
    ORDER BY timestamp DESC
    LIMIT :row_limit
    """
)

_CYCLE_COUNT_SQL = text(
    """
    SELECT count(*) AS n_cycles FROM (
        SELECT timestamp
        FROM sensor_readings
        WHERE tenant_id = :tenant_id
          AND asset_id = :asset_id
          AND sensor_type = ANY(:types)
        GROUP BY timestamp
        HAVING count(DISTINCT sensor_type) = :n_channels
    ) AS complete_snapshots
    """
)


class RulModelUnavailable(RuntimeError):
    """No RUL model has been published (or it cannot be read) for the configured subset."""


@dataclass(frozen=True)
class RulOutcome:
    """Either a validated estimate or the reason there is none - never a guess."""

    estimate: RulEstimate | None
    skip_reason: str | None = None


@dataclass(frozen=True)
class LoadedRulModel:
    model: XGBRegressor
    config: RulFeatureConfig
    version: str
    subset: str
    dataset_name: str = "unknown"
    dataset_version: str = "unknown"


_MODEL_CACHE: dict[tuple[str, str], LoadedRulModel] = {}


def load_rul_model_cached(subset: str | None = None, version: str | None = None) -> LoadedRulModel:
    """Load (and cache by resolved version) the RUL model; raises `RulModelUnavailable`."""
    subset = subset or settings.rul_model_subset
    if version is not None and (subset, version) in _MODEL_CACHE:
        return _MODEL_CACHE[(subset, version)]
    try:
        model, config, _metrics, metadata = load_rul_model(subset, version)
    except (S3Error, Urllib3HTTPError, OSError, KeyError, ValueError) as exc:
        raise RulModelUnavailable(f"no readable RUL model for subset {subset}: {exc}") from exc
    resolved = str(metadata.get("model_version") or version or "")
    if not resolved:
        raise RulModelUnavailable("stored RUL model has no model_version in its metadata")
    if int(model.n_features_in_) != len(config.feature_names):
        raise ValueError(
            "stored RUL model and its feature config disagree on the feature count - "
            "refusing to predict with a mismatched layout"
        )
    loaded = LoadedRulModel(
        model=model,
        config=config,
        version=resolved,
        subset=subset,
        dataset_name=str(metadata.get("dataset_name") or "unknown"),
        dataset_version=str(metadata.get("dataset_version") or "unknown"),
    )
    _MODEL_CACHE[(subset, resolved)] = loaded
    return loaded


def assemble_snapshots(
    rows: Sequence[tuple[datetime, str, float]], max_cycles: int
) -> list[dict[str, Any]]:
    """Pivot (timestamp, sensor_type, value) rows into complete snapshots, oldest first.

    A snapshot is complete only if all 24 channels are present at that timestamp. Returns the
    most recent `max_cycles` complete snapshots as dicts of channel -> value (plus
    `timestamp`). Duplicate (timestamp, channel) readings keep the last one seen.
    """
    by_time: dict[datetime, dict[str, float]] = defaultdict(dict)
    for stamp, sensor_type, value in rows:
        by_time[stamp][sensor_type] = float(value)
    wanted = set(CMAPSS_TELEMETRY_TYPES)
    complete = [
        {"timestamp": stamp, **channels}
        for stamp, channels in sorted(by_time.items())
        if wanted <= channels.keys()
    ]
    return complete[-max_cycles:]


def build_window(snapshots: Sequence[dict[str, Any]], total_cycles: int) -> RulWindowIn:
    """Validated model input; the last snapshot's cycle is the asset's age (`total_cycles`)."""
    if not snapshots:
        raise ValueError("no snapshots to build a window from")
    if total_cycles < len(snapshots):
        raise ValueError("total cycle count cannot be smaller than the window")
    first_cycle = total_cycles - len(snapshots) + 1
    built = []
    for offset, snap in enumerate(snapshots):
        built.append(
            RulSnapshot(
                cycle=first_cycle + offset,
                op_setting_1=snap[CMAPSS_OP_SETTING_TYPES["op_setting_1"]],
                op_setting_2=snap[CMAPSS_OP_SETTING_TYPES["op_setting_2"]],
                op_setting_3=snap[CMAPSS_OP_SETTING_TYPES["op_setting_3"]],
                sensors={col: snap[CMAPSS_SENSOR_MAP[col].sensor_type] for col in SENSOR_COLUMNS},
            )
        )
    return RulWindowIn(snapshots=built)


def predict_from_window(
    window: RulWindowIn, loaded: LoadedRulModel, *, asset_id: uuid.UUID, now: datetime
) -> RulEstimate:
    """Features -> model -> clipped, validated estimate."""
    table = window.to_table()
    features = window_features(table, loaded.config).reshape(1, -1)
    raw = float(loaded.model.predict(features)[0])
    if not np.isfinite(raw):
        raise ValueError("RUL model returned a non-finite prediction")
    rul = float(np.clip(raw, 0.0, float(loaded.config.rul_cap)))
    return RulEstimate(
        asset_id=asset_id,
        rul_cycles=round(rul, 2),
        model_version=loaded.version,
        model_subset=loaded.subset,
        dataset_name=loaded.dataset_name,
        dataset_version=loaded.dataset_version,
        cycle=int(table[-1, 1]),
        window_len=int(min(len(window.snapshots), loaded.config.long_window)),
        computed_at=now,
    )


async def predict_asset_rul(
    timescale_session: AsyncSession,
    loaded: LoadedRulModel,
    *,
    tenant_id: uuid.UUID,
    asset_id: uuid.UUID,
    now: datetime,
) -> RulOutcome:
    """RUL for one asset from its live telemetry, or the reason there is none."""
    types = list(CMAPSS_TELEMETRY_TYPES)
    window_cycles = loaded.config.long_window
    result = await timescale_session.execute(
        _SNAPSHOT_ROWS_SQL,
        {
            "tenant_id": tenant_id,
            "asset_id": asset_id,
            "types": types,
            "row_limit": N_CHANNELS * (window_cycles + _FETCH_SLACK_CYCLES),
        },
    )
    rows = [(r.timestamp, r.sensor_type, r.value) for r in result.all()]
    if not rows:
        return RulOutcome(None, SKIP_NO_TELEMETRY)

    snapshots = assemble_snapshots(rows, window_cycles)
    if not snapshots:
        return RulOutcome(None, SKIP_INCOMPLETE)

    count = await timescale_session.execute(
        _CYCLE_COUNT_SQL,
        {"tenant_id": tenant_id, "asset_id": asset_id, "types": types, "n_channels": N_CHANNELS},
    )
    total_cycles = max(int(count.scalar_one()), len(snapshots))

    try:
        window = build_window(snapshots, total_cycles)
        estimate = predict_from_window(window, loaded, asset_id=asset_id, now=now)
    except ValidationError as exc:
        logger.warning(
            "RUL skipped for asset %s: invalid telemetry (%s)", asset_id, exc.error_count()
        )
        return RulOutcome(None, SKIP_INVALID)
    except ValueError as exc:
        reason = SKIP_UNSEEN_REGIME if "operating condition" in str(exc) else SKIP_INVALID
        logger.warning("RUL skipped for asset %s: %s", asset_id, exc)
        return RulOutcome(None, reason)
    return RulOutcome(estimate, None)


async def predict_facility_rul(
    timescale_session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    asset_ids: Sequence[uuid.UUID],
    now: datetime | None = None,
    subset: str | None = None,
    model_version: str | None = None,
) -> dict[uuid.UUID, RulOutcome]:
    """RUL outcomes for many assets; a missing model yields a reasoned skip for every asset."""
    now = now or datetime.now(UTC)
    try:
        loaded = load_rul_model_cached(subset, model_version)
    except RulModelUnavailable as exc:
        logger.warning("RUL unavailable: %s", exc)
        return {a: RulOutcome(None, SKIP_MODEL_UNAVAILABLE) for a in asset_ids}
    return {
        asset_id: await predict_asset_rul(
            timescale_session, loaded, tenant_id=tenant_id, asset_id=asset_id, now=now
        )
        for asset_id in asset_ids
    }
