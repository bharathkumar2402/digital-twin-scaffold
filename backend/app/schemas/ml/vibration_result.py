import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VibrationAssessment(BaseModel):
    """One asset channel's vibration anomaly verdict, validated before an agent uses it
    (CLAUDE.md rule 1 applies to ML tool output too). Model and dataset identity are stored
    with every result (rule 8).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    asset_id: uuid.UUID
    channel: int = Field(ge=0, description="Accelerometer channel index on the asset")
    alarm: bool = Field(description="True when the last k snapshots all exceeded the threshold")
    latest_score: float = Field(ge=0.0, le=1.0, description="Anomaly score of the newest snapshot")
    max_recent_score: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    consecutive_exceedances: int = Field(
        ge=0, description="Trailing run of snapshots over threshold"
    )
    k_consecutive: int = Field(ge=1)
    n_baseline_snapshots: int = Field(ge=1)
    n_scored_snapshots: int = Field(ge=1)
    model_version: str = Field(min_length=1, max_length=255)
    dataset_name: str = Field(min_length=1, max_length=255)
    dataset_version: str = Field(min_length=1, max_length=255)
    computed_at: datetime

    @model_validator(mode="after")
    def _alarm_matches_run(self) -> "VibrationAssessment":
        if self.alarm != (self.consecutive_exceedances >= self.k_consecutive):
            raise ValueError("alarm flag inconsistent with consecutive_exceedances / k")
        if self.consecutive_exceedances > self.n_scored_snapshots:
            raise ValueError("consecutive_exceedances cannot exceed scored snapshots")
        return self
