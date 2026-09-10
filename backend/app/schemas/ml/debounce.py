import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DebounceDecision(BaseModel):
    """One asset's debounce/cooldown decision for a single anomalous reading (issue
    3.5), per `PROJECT_PLAN.md` §7.1 - validated before it's returned, same
    "never return unvalidated data" rule as `AnomalyCheckResult`/`RiskScoreResult`.

    `should_trigger_light_rescore` is the only field a caller should act on: it is
    `True` exactly when this asset's per-asset cooldown was *not* already active,
    i.e. this is the first anomaly for this asset since the last trigger (or ever).
    Every anomaly for the same asset within `COOLDOWN_SECONDS` of a trigger reports
    `False` here - that's the debounce actually working, not a bug. Deliberately
    does not itself enqueue anything (no Celery call, no Redis pub/sub) - this task
    is the decision engine only, wiring a `True` decision to an actual lightweight
    risk re-score or to `PROJECT_PLAN.md` §7.1's batched full-pipeline trigger is a
    later task's job.

    `batch_window_count` is informational only right now (how many anomalies, across
    the whole tenant, landed in the current `BATCH_WINDOW_SECONDS` bucket) - reserved
    for Phase 4's full-pipeline batching, not acted on by anything yet.
    """

    model_config = ConfigDict(frozen=True)

    asset_id: uuid.UUID
    sensor_type: str
    should_trigger_light_rescore: bool
    cooldown_active: bool
    batch_window_count: int = Field(ge=1)
    evaluated_at: datetime
