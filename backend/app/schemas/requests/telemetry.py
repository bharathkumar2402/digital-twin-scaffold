import math
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.ml.anomaly import AnomalyCheckResult


class SensorReadingIn(BaseModel):
    asset_id: uuid.UUID
    sensor_type: str = Field(min_length=1, max_length=100)
    value: float
    unit: str = Field(min_length=1, max_length=50)
    timestamp: datetime

    @field_validator("value")
    @classmethod
    def value_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("value must be finite (not NaN or infinite)")
        return value


class TelemetryIngestRequest(BaseModel):
    readings: list[SensorReadingIn] = Field(min_length=1, max_length=1000)


class TelemetryIngestResponse(BaseModel):
    accepted: int
    # Live rolling-Z-score check on this same batch (issue 3.4) - one result per
    # ingested reading. Debounced triggers derived from these are published to the
    # tenant's WebSocket alert channel by the route handler (issues 3.5/3.6); this
    # response field itself stays the raw per-reading check, unfiltered.
    anomalies: list[AnomalyCheckResult]


class TelemetryReadingResponse(BaseModel):
    model_config = {"from_attributes": True}

    sensor_type: str
    value: float
    unit: str
    timestamp: datetime
