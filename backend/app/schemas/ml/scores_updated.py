import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ScoresUpdated(BaseModel):
    """Published to a tenant's channel after the scheduled job wrote fresh risk scores for one
    facility (Phase L task L.4). Ids and a count only - no scores and no sensor data - so the
    browser refetches through the normal, authorized routes instead of trusting the push.

    Validated before it is serialized for `PUBLISH`, like `AlertNotification`. The `type` field is
    how a client tells it apart from an alert frame on the same channel (alerts carry none).
    """

    model_config = ConfigDict(frozen=True)

    type: Literal["scores_updated"] = "scores_updated"
    facility_id: uuid.UUID
    scored: int
    computed_at: datetime
