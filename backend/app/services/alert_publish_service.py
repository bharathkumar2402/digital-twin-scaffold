import uuid

from redis.asyncio import Redis

from app.schemas.ml.alert_notification import AlertNotification

# Mirrors alert_debounce_service's "namespace every key/channel by tenant_id, not just
# asset_id" convention (see that module's cross-tenant test) - a WebSocket subscribed
# to one tenant's channel structurally cannot receive another tenant's PUBLISH, since
# Redis pub/sub only delivers a message to subscribers of the exact channel it was
# published on.
_ALERT_CHANNEL = "alerts:{tenant_id}"


def alert_channel(tenant_id: uuid.UUID) -> str:
    return _ALERT_CHANNEL.format(tenant_id=tenant_id)


async def publish_alert(
    redis: Redis, *, tenant_id: uuid.UUID, notification: AlertNotification
) -> None:
    """Publishes one already-validated `AlertNotification` to the caller's tenant
    channel. Callers only ever construct `AlertNotification` from a `DebounceDecision`
    with `should_trigger_light_rescore=True` (see that schema's docstring) - a
    cooldown-suppressed anomaly never reaches this function, so debounce logic isn't
    duplicated here.

    Fire-and-forget: `PUBLISH` to a channel with zero current subscribers is not an
    error (Redis just reports zero receivers) - a tenant with nobody currently viewing
    the map loses nothing but the toast, the alert itself was already durably decided
    by `evaluate_anomaly_batch` before this call.
    """
    await redis.publish(alert_channel(tenant_id), notification.model_dump_json())
