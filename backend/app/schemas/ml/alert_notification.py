import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AlertNotification(BaseModel):
    """The payload published to a tenant's Redis alert channel and forwarded verbatim
    to every WebSocket client subscribed to it (issue 3.6).

    Built only from a `DebounceDecision` with `should_trigger_light_rescore=True` -
    a suppressed (cooldown-active) decision never reaches this schema, so a client
    can treat "an AlertNotification arrived" as "a new, debounced anomaly", not just
    "a reading was flagged". Validated before it's serialized for `PUBLISH`, same
    "never push unvalidated data" rule the root CLAUDE.md applies to agent output -
    extended here to this non-agent real-time path.
    """

    model_config = ConfigDict(frozen=True)

    asset_id: uuid.UUID
    sensor_type: str
    value: float
    z_score: float | None
    triggered_at: datetime
