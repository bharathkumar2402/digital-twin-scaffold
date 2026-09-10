import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RiskScoreResult(BaseModel):
    """One asset's risk-inference output, validated before it ever reaches
    `risk_scores` (issue 3.3) - not an agent-output schema (Phase 4's Agent 2 wraps
    this service as a tool later), but the same "never write unvalidated data" rule
    from the root CLAUDE.md applies to this ML output too. `score` is bounded 0-100
    via Pydantic rather than trusted from the raw model output, since a bug in the
    scaling/clamping step should fail loudly here instead of writing an out-of-range
    value the `risk_scores.score` CHECK constraint would otherwise have to catch.
    """

    model_config = ConfigDict(frozen=True)

    asset_id: uuid.UUID
    tenant_id: uuid.UUID
    facility_id: uuid.UUID
    score: float = Field(ge=0.0, le=100.0)
    model_version: str
    factors: dict[str, float | int | str | None]
    computed_at: datetime
