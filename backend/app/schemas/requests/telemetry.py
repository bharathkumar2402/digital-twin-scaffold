import math
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


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
