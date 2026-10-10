import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.ml.rul_features import RUL_CAP


class RulEstimate(BaseModel):
    """One asset's Remaining Useful Life estimate, validated before it can reach
    `risk_scores.rul_cycles` or an agent's output (CLAUDE.md rule 1 applies to ML tool
    output too). `rul_cycles` is bounded to the trained label cap so a scaling bug or a
    corrupt model fails loudly here rather than writing a nonsense value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    asset_id: uuid.UUID
    rul_cycles: float = Field(ge=0.0, le=float(RUL_CAP * 4))
    model_version: str = Field(min_length=1, max_length=255)
    model_subset: str = Field(min_length=1, max_length=16)
    dataset_name: str = Field(min_length=1, max_length=255)
    dataset_version: str = Field(min_length=1, max_length=255)
    cycle: int = Field(ge=1, description="Asset age in complete telemetry snapshots")
    window_len: int = Field(ge=1, description="Snapshots actually used in the feature window")
    computed_at: datetime
