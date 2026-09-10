"""Unit tests for the batching/cooldown decision engine (issue 3.5), per
`PROJECT_PLAN.md` §7.1. Run against `fakeredis` (async), not a real Redis server - fast
and deterministic, and this module's Redis usage is limited to `SET NX EX`/`INCR`/
`EXPIRE`, all of which `fakeredis` implements faithfully. A real-`redis:7-alpine`
container pass was also run manually (see the CLAUDE.md tracker note for this task) to
confirm the atomic `SET NX` race behavior isn't a fakeredis-only illusion.

Cooldown *expiry* is simulated by deleting the cooldown key directly rather than
sleeping past `COOLDOWN_SECONDS` - `fakeredis` has no clock fast-forward, and a real
120-second sleep in a unit test would be its own bug. Deleting the key is the same
externally-observable state as the TTL having elapsed: the next `SET NX` call has
nothing to collide with.
"""

import uuid
from datetime import UTC, datetime

from fakeredis import aioredis as fakeredis

from app.schemas.ml.anomaly import AnomalyCheckResult
from app.services.alert_debounce_service import (
    COOLDOWN_SECONDS,
    _cooldown_key,
    evaluate_anomaly,
    evaluate_anomaly_batch,
)

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
ASSET_A = uuid.uuid4()
ASSET_B = uuid.uuid4()
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _anomaly(
    *, asset_id: uuid.UUID = ASSET_A, sensor_type: str = "vibration"
) -> AnomalyCheckResult:
    return AnomalyCheckResult(
        asset_id=asset_id,
        sensor_type=sensor_type,
        value=999.0,
        timestamp=NOW,
        rolling_mean=20.0,
        rolling_stddev=2.0,
        sample_count=50,
        z_score=489.5,
        is_anomaly=True,
    )


async def _redis() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


async def test_first_anomaly_for_an_asset_triggers() -> None:
    redis = await _redis()
    decision = await evaluate_anomaly(redis, tenant_id=TENANT_A, anomaly=_anomaly(), now=NOW)
    assert decision.should_trigger_light_rescore is True
    assert decision.cooldown_active is False
    await redis.aclose()


async def test_repeat_anomaly_within_cooldown_is_suppressed() -> None:
    redis = await _redis()
    first = await evaluate_anomaly(redis, tenant_id=TENANT_A, anomaly=_anomaly(), now=NOW)
    second = await evaluate_anomaly(redis, tenant_id=TENANT_A, anomaly=_anomaly(), now=NOW)
    assert first.should_trigger_light_rescore is True
    assert second.should_trigger_light_rescore is False
    assert second.cooldown_active is True
    await redis.aclose()


async def test_rapid_repeated_anomalies_do_not_flood_triggers() -> None:
    """The phase plan's own DoD wording: 'rapid repeated anomalies on one asset do NOT
    produce a flood of triggers.' Fire 20 anomalies for the same asset back-to-back and
    confirm exactly one of them triggers."""
    redis = await _redis()
    decisions = [
        await evaluate_anomaly(redis, tenant_id=TENANT_A, anomaly=_anomaly(), now=NOW)
        for _ in range(20)
    ]
    triggered = [d for d in decisions if d.should_trigger_light_rescore]
    assert len(triggered) == 1
    await redis.aclose()


async def test_triggers_again_after_cooldown_expires() -> None:
    redis = await _redis()
    first = await evaluate_anomaly(redis, tenant_id=TENANT_A, anomaly=_anomaly(), now=NOW)
    assert first.should_trigger_light_rescore is True

    await redis.delete(_cooldown_key(TENANT_A, ASSET_A))

    second = await evaluate_anomaly(redis, tenant_id=TENANT_A, anomaly=_anomaly(), now=NOW)
    assert second.should_trigger_light_rescore is True
    await redis.aclose()


async def test_different_assets_do_not_share_cooldown_state() -> None:
    redis = await _redis()
    anomaly_a = _anomaly(asset_id=ASSET_A)
    anomaly_b = _anomaly(asset_id=ASSET_B)
    a = await evaluate_anomaly(redis, tenant_id=TENANT_A, anomaly=anomaly_a, now=NOW)
    b = await evaluate_anomaly(redis, tenant_id=TENANT_A, anomaly=anomaly_b, now=NOW)
    assert a.should_trigger_light_rescore is True
    assert b.should_trigger_light_rescore is True
    await redis.aclose()


async def test_different_tenants_with_same_asset_id_do_not_share_cooldown_state() -> None:
    """Cooldown keys are namespaced by tenant_id, not just asset_id - two tenants
    happening to reuse the same asset UUID (never possible for Postgres rows per
    the RLS-scoped tables, but Redis has no such constraint) must not let one
    tenant's trigger suppress another tenant's alert."""
    redis = await _redis()
    anomaly = _anomaly(asset_id=ASSET_A)
    a = await evaluate_anomaly(redis, tenant_id=TENANT_A, anomaly=anomaly, now=NOW)
    await evaluate_anomaly(redis, tenant_id=TENANT_A, anomaly=anomaly, now=NOW)
    b = await evaluate_anomaly(redis, tenant_id=TENANT_B, anomaly=anomaly, now=NOW)
    assert a.should_trigger_light_rescore is True
    assert b.should_trigger_light_rescore is True
    await redis.aclose()


async def test_batch_window_count_increments_across_anomalies_in_the_same_bucket() -> None:
    redis = await _redis()
    anomaly_a = _anomaly(asset_id=ASSET_A)
    anomaly_b = _anomaly(asset_id=ASSET_B)
    first = await evaluate_anomaly(redis, tenant_id=TENANT_A, anomaly=anomaly_a, now=NOW)
    second = await evaluate_anomaly(redis, tenant_id=TENANT_A, anomaly=anomaly_b, now=NOW)
    assert first.batch_window_count == 1
    assert second.batch_window_count == 2
    await redis.aclose()


async def test_evaluate_anomaly_batch_skips_non_anomalous_readings() -> None:
    redis = await _redis()
    non_anomalous = _anomaly().model_copy(update={"is_anomaly": False})
    decisions = await evaluate_anomaly_batch(
        redis, tenant_id=TENANT_A, anomalies=[non_anomalous], now=NOW
    )
    assert decisions == []
    # No cooldown key was ever created for the skipped reading.
    assert await redis.exists(_cooldown_key(TENANT_A, ASSET_A)) == 0
    await redis.aclose()


async def test_evaluate_anomaly_batch_only_evaluates_flagged_readings() -> None:
    redis = await _redis()
    non_anomalous = _anomaly(asset_id=ASSET_B).model_copy(update={"is_anomaly": False})
    anomalous = _anomaly(asset_id=ASSET_A)
    decisions = await evaluate_anomaly_batch(
        redis, tenant_id=TENANT_A, anomalies=[non_anomalous, anomalous], now=NOW
    )
    assert len(decisions) == 1
    assert decisions[0].asset_id == ASSET_A
    await redis.aclose()


def test_cooldown_seconds_within_documented_range() -> None:
    """Sanity guard, not a behavioral test: catches an accidental edit turning the
    cooldown into something too short to matter or absurdly long."""
    assert 30 <= COOLDOWN_SECONDS <= 600
