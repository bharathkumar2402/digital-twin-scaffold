import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RiskScoreResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    facility_id: uuid.UUID
    asset_id: uuid.UUID
    score: float
    # Remaining useful life in cycles (Phase 3b); null when the asset has no usable RUL
    # telemetry or no RUL model is published. Its model/dataset live in `factors_json`.
    rul_cycles: float | None = None
    model_version: str
    factors_json: dict[str, float | int | str | None]
    computed_at: datetime


class RiskScoreComputeAcceptedResponse(BaseModel):
    """Returned immediately on enqueue - the Celery task runs asynchronously, so this
    is not the scored result itself. Poll `GET /facilities/{facility_id}/risk-scores`
    for results, same async-job pattern as `facility_maps.py`'s upload status polling.
    """

    task_id: str
    status: str = "queued"
