import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis

from app.core.redis_client import get_redis_client
from app.schemas.ml.anomaly import AnomalyCheckResult
from app.schemas.ml.debounce import DebounceDecision

__all__ = [
    "BATCH_WINDOW_SECONDS",
    "COOLDOWN_SECONDS",
    "evaluate_anomaly",
    "evaluate_anomaly_batch",
    "get_redis_client",
]

# Within PROJECT_PLAN.md §7.1's documented 10-30s range for batching anomaly events
# before a (future) pipeline trigger. A tenant-wide bucket, not per-asset: §7.1's intent
# is collapsing a burst of anomalies *across a facility* into one pipeline run, not
# tracking each asset's own window separately (that's what the cooldown below does).
BATCH_WINDOW_SECONDS = 15

# Once an asset triggers, further anomalies on that same asset are suppressed for this
# long - "repeated anomalies on the same asset within a cooldown period don't re-trigger
# a full run" (§7.1). 2 minutes: long enough that a flapping sensor can't flood triggers,
# short enough that a genuinely new problem on the same asset is still caught soon.
COOLDOWN_SECONDS = 120

_COOLDOWN_KEY = "debounce:cooldown:{tenant_id}:{asset_id}"
_WINDOW_KEY = "debounce:window:{tenant_id}:{bucket}"


def _cooldown_key(tenant_id: uuid.UUID, asset_id: uuid.UUID) -> str:
    return _COOLDOWN_KEY.format(tenant_id=tenant_id, asset_id=asset_id)


def _window_key(tenant_id: uuid.UUID, now: datetime) -> str:
    bucket = int(now.timestamp() // BATCH_WINDOW_SECONDS)
    return _WINDOW_KEY.format(tenant_id=tenant_id, bucket=bucket)


async def evaluate_anomaly(
    redis: Redis,
    *,
    tenant_id: uuid.UUID,
    anomaly: AnomalyCheckResult,
    now: datetime | None = None,
) -> DebounceDecision:
    """Decides whether one flagged anomaly should trigger a lightweight risk re-score,
    per the batching/cooldown rules in `PROJECT_PLAN.md` §7.1.

    Only ever called for `anomaly.is_anomaly is True` readings - see
    `evaluate_anomaly_batch`, which filters non-anomalous readings out before this
    function's Redis calls ever run, so a normal reading never touches debounce state.

    The cooldown check uses `SET NX EX` (`set(..., nx=True, ex=COOLDOWN_SECONDS)`),
    which is atomic in Redis - two near-simultaneous anomalies for the same asset (e.g.
    two sensor_types both spiking in the same ingest batch, or two concurrent requests)
    can't both see "cooldown not active" and both trigger; exactly one wins the `SET NX`
    race. This is what actually guarantees "no flood of triggers", not just the 2-minute
    TTL by itself.

    Deliberately does not enqueue anything downstream (no Celery call, no Redis
    pub/sub) - see this module's docstring and `DebounceDecision`'s.
    """
    now = now or datetime.now(UTC)

    cooldown_key = _cooldown_key(tenant_id, anomaly.asset_id)
    # SET NX returns True only if this call actually created the key, i.e. the
    # cooldown was NOT already active - that's exactly "should this trigger".
    won_cooldown = await redis.set(cooldown_key, "1", nx=True, ex=COOLDOWN_SECONDS)
    cooldown_active = not won_cooldown

    window_key = _window_key(tenant_id, now)
    batch_window_count = await redis.incr(window_key)
    if batch_window_count == 1:
        # First member of this bucket - set the bucket's own expiry once, so the
        # counter doesn't outlive the window it's counting (a couple seconds of
        # slack past the window boundary is harmless; this key is never read after
        # its bucket has passed).
        await redis.expire(window_key, BATCH_WINDOW_SECONDS + 5)

    return DebounceDecision(
        asset_id=anomaly.asset_id,
        sensor_type=anomaly.sensor_type,
        should_trigger_light_rescore=won_cooldown is True,
        cooldown_active=cooldown_active,
        batch_window_count=batch_window_count,
        evaluated_at=now,
    )


async def evaluate_anomaly_batch(
    redis: Redis,
    *,
    tenant_id: uuid.UUID,
    anomalies: list[AnomalyCheckResult],
    now: datetime | None = None,
) -> list[DebounceDecision]:
    """Runs `evaluate_anomaly` over only the flagged readings in a batch.

    Non-anomalous readings (`is_anomaly=False`) are skipped entirely - they never
    create or touch a cooldown/window key, so a facility with zero real anomalies
    leaves zero debounce state in Redis.
    """
    now = now or datetime.now(UTC)
    return [
        await evaluate_anomaly(redis, tenant_id=tenant_id, anomaly=anomaly, now=now)
        for anomaly in anomalies
        if anomaly.is_anomaly
    ]
