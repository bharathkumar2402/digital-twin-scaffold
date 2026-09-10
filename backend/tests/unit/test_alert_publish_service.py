"""Unit tests for the Redis pub/sub publish step of real-time alert delivery
(issue 3.6). Run against `fakeredis` (async), same rationale as
`test_alert_debounce_service.py` - `PUBLISH`/`SUBSCRIBE` are faithfully implemented
there and a real Redis server isn't needed to prove channel routing.
"""

import uuid
from datetime import UTC, datetime

from fakeredis import aioredis as fakeredis

from app.schemas.ml.alert_notification import AlertNotification
from app.services.alert_publish_service import alert_channel, publish_alert

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
ASSET_A = uuid.uuid4()
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _notification() -> AlertNotification:
    return AlertNotification(
        asset_id=ASSET_A, sensor_type="vibration", value=999.0, z_score=489.5, triggered_at=NOW
    )


async def _redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


async def test_publish_delivers_to_a_subscriber_of_the_same_tenant_channel() -> None:
    redis = await _redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(alert_channel(TENANT_A))
    await pubsub.get_message(timeout=1)  # discard the "subscribe" confirmation message

    await publish_alert(redis, tenant_id=TENANT_A, notification=_notification())

    message = await pubsub.get_message(timeout=1)
    assert message is not None
    assert message["type"] == "message"
    payload = AlertNotification.model_validate_json(message["data"])
    assert payload == _notification()

    await pubsub.aclose()
    await redis.aclose()


async def test_publish_does_not_reach_a_subscriber_of_a_different_tenant_channel() -> None:
    """Cross-tenant isolation for the pub/sub layer: a subscriber on tenant B's
    channel must see nothing when tenant A's alert is published - Redis pub/sub
    routes strictly by exact channel name, so this also guards against a future
    accidental channel-naming collision (e.g. dropping the tenant_id from the
    template)."""
    redis = await _redis()
    pubsub_b = redis.pubsub()
    await pubsub_b.subscribe(alert_channel(TENANT_B))
    await pubsub_b.get_message(timeout=1)

    await publish_alert(redis, tenant_id=TENANT_A, notification=_notification())

    message = await pubsub_b.get_message(timeout=0.2)
    assert message is None

    await pubsub_b.aclose()
    await redis.aclose()


def test_alert_channel_is_namespaced_by_tenant_id() -> None:
    assert alert_channel(TENANT_A) != alert_channel(TENANT_B)
    assert str(TENANT_A) in alert_channel(TENANT_A)
