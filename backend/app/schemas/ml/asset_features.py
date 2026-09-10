import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SensorWindowStats(BaseModel):
    """Rolling-window aggregate stats for one sensor_type on one asset.

    `anomaly_count` is a window-local z-score count (readings more than
    `ANOMALY_Z_SCORE_THRESHOLD` std devs from this same window's mean) - a feature
    signal for the risk model, not the same thing as task 3.4's live rolling-Z-score
    anomaly detector, which scores each new reading as it's ingested.
    """

    model_config = ConfigDict(frozen=True)

    sensor_type: str
    window_days: int
    count: int
    mean: float | None
    stddev: float | None
    min: float | None
    max: float | None
    latest_value: float | None
    anomaly_count: int


class AssetFeatureSet(BaseModel):
    """The feature vector for one asset, as of `computed_at` - input to the risk model
    (XGBoost training/inference land in tasks 3.2/3.3).

    Deliberately omits "last maintenance date" and "failure rate for that class" from
    PROJECT_PLAN.md §4.3's Agent 2 description: no `maintenance_records` table (or any
    historical-failure table) exists in this repo yet, and building one isn't scoped to
    any Phase 1-3 task. `dependency_neighbor_*` counts are used as the "adjacent asset
    failures" signal instead, from data that does exist (`asset_dependencies` + each
    neighbor's current `status`).
    """

    model_config = ConfigDict(frozen=True)

    asset_id: uuid.UUID
    tenant_id: uuid.UUID
    facility_id: uuid.UUID
    computed_at: datetime
    asset_status: str
    asset_age_days: int | None
    dependency_neighbor_count: int
    dependency_neighbor_offline_count: int
    dependency_neighbor_maintenance_count: int
    # window_days -> sensor_type -> stats
    windows: dict[int, dict[str, SensorWindowStats]]
