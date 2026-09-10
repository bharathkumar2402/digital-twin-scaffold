import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AnomalyCheckResult(BaseModel):
    """One reading's live rolling-Z-score anomaly check (issue 3.4), validated before
    it's returned from `POST /telemetry` - the same "never return unvalidated data"
    rule from the root CLAUDE.md, extended to this non-agent ML output (same reasoning
    as `RiskScoreResult`). This is a *live, per-reading* check against a rolling window
    of prior readings for the same asset/sensor_type, distinct from
    `SensorWindowStats.anomaly_count` (3.1's window-local outlier *count*, a training
    feature) - see that schema's docstring.

    `is_anomaly` is only ever `True` when `sample_count` meets the caller's configured
    minimum: too little history to compute a meaningful stddev must never be reported
    as an anomaly.
    """

    model_config = ConfigDict(frozen=True)

    asset_id: uuid.UUID
    sensor_type: str
    value: float
    timestamp: datetime
    rolling_mean: float | None
    rolling_stddev: float | None
    sample_count: int = Field(ge=0)
    z_score: float | None
    is_anomaly: bool
