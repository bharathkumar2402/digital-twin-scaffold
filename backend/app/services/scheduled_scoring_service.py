"""Scheduled risk scoring for every facility of every tenant (Phase L task L.4).

Celery beat runs `score_all_facilities` (app/workers/scheduled_scoring.py) every minute. Per
facility it:

1. takes a Redis lock so two runs never score the same facility at once (a run can outlast the
   interval once the readings table is large - L.0 measured 9-14 s for 44 assets);
2. skips the facility when no sensor reading is newer than its last scoring attempt (scoring
   writes one `risk_scores` row per asset, so scoring an idle facility would only grow history);
3. calls the existing `score_facility` (model-validated, `RiskScoreResult` rules unchanged);
4. publishes a `ScoresUpdated` event on that tenant's channel.

Isolation: every tenant, and every facility within it, gets its own RLS-scoped sessions. A failure
in one facility (or one tenant) is logged and counted and never stops the others. Nothing here
touches the raw anomaly path (root CLAUDE.md rule 4): this is a fixed-cadence batch, not a
threshold-triggered pipeline run.
"""

import logging
import secrets
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import WatchError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.tenant_context import scope_session_to_tenant
from app.models.asset import Asset
from app.models.facility import Facility
from app.models.risk_score import RiskScore
from app.models.tenant import Tenant
from app.schemas.ml.scores_updated import ScoresUpdated
from app.services.alert_publish_service import publish_scores_updated
from app.services.risk_inference_service import score_facility
from app.services.telemetry_service import get_last_reading_times

logger = logging.getLogger(__name__)

# One lock and one "last attempt" marker per facility. The facility id is a UUID and unique across
# tenants, but the tenant id is part of the key anyway (same convention as the alert keys).
_LOCK_KEY = "score-lock:{tenant_id}:{facility_id}"
_LAST_ATTEMPT_KEY = "score-last:{tenant_id}:{facility_id}"
_LAST_ATTEMPT_TTL_SECONDS = 30 * 24 * 3600

ScoreFn = Callable[..., Awaitable[list[RiskScore]]]


@dataclass
class ScoringRunSummary:
    scored: int = 0
    skipped_no_new_data: int = 0
    skipped_busy: int = 0
    failed: int = 0
    tenants_failed: int = 0
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, int]:
        return {
            "scored": self.scored,
            "skipped_no_new_data": self.skipped_no_new_data,
            "skipped_busy": self.skipped_busy,
            "failed": self.failed,
            "tenants_failed": self.tenants_failed,
        }


async def acquire_lock(
    redis: Redis, *, tenant_id: uuid.UUID, facility_id: uuid.UUID, ttl_seconds: int
) -> str | None:
    """`SET key token NX EX ttl`: the token if this caller now holds the lock, None if another run
    does. The lock expires on its own, so a run that dies cannot block the facility for good."""
    token = secrets.token_hex(16)
    key = _LOCK_KEY.format(tenant_id=tenant_id, facility_id=facility_id)
    got = await redis.set(key, token, nx=True, ex=ttl_seconds)
    return token if got else None


async def release_lock(
    redis: Redis, *, tenant_id: uuid.UUID, facility_id: uuid.UUID, token: str
) -> bool:
    """Deletes the lock only if it still holds `token`. A run that outlived its TTL must not
    delete the lock a newer run took over. Optimistic WATCH/MULTI rather than a Lua script, so it
    behaves the same on a real Redis and on the in-memory fake the tests use."""
    key = _LOCK_KEY.format(tenant_id=tenant_id, facility_id=facility_id)
    async with redis.pipeline() as pipe:
        try:
            await pipe.watch(key)
            if await pipe.get(key) != token:
                await pipe.unwatch()
                return False
            pipe.multi()
            pipe.delete(key)
            await pipe.execute()
            return True
        except WatchError:
            return False


async def _last_attempt(
    redis: Redis, *, tenant_id: uuid.UUID, facility_id: uuid.UUID
) -> datetime | None:
    raw = await redis.get(_LAST_ATTEMPT_KEY.format(tenant_id=tenant_id, facility_id=facility_id))
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None  # a corrupt marker means "unknown": score rather than skip forever


async def facility_has_new_readings(
    main_session: AsyncSession,
    timescale_session: AsyncSession,
    redis: Redis,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
) -> bool:
    """True when some asset of the facility has a reading newer than the last scoring attempt.

    "Last attempt" is the later of the newest stored `risk_scores.computed_at` and a Redis marker
    set at every attempt. The marker matters for a facility whose assets cannot be scored (no
    coherent snapshot): it has no score rows, and without the marker it would be rescored every
    minute forever. A facility with no readings at all is never scored.
    """
    asset_ids = list(
        (
            await main_session.execute(
                select(Asset.id).where(
                    Asset.tenant_id == tenant_id, Asset.facility_id == facility_id
                )
            )
        ).scalars()
    )
    if not asset_ids:
        return False
    _, last_by_asset = await get_last_reading_times(
        timescale_session, tenant_id=tenant_id, asset_ids=asset_ids
    )
    newest_reading = max((ts for ts in last_by_asset.values() if ts is not None), default=None)
    if newest_reading is None:
        return False

    last_scored = (
        await main_session.execute(
            select(func.max(RiskScore.computed_at)).where(
                RiskScore.tenant_id == tenant_id, RiskScore.facility_id == facility_id
            )
        )
    ).scalar_one_or_none()
    marker = await _last_attempt(redis, tenant_id=tenant_id, facility_id=facility_id)
    reference = max((t for t in (last_scored, marker) if t is not None), default=None)
    return reference is None or newest_reading > reference


async def _score_one_facility(
    *,
    main_factory: async_sessionmaker[AsyncSession],
    timescale_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    score: ScoreFn,
    summary: ScoringRunSummary,
) -> None:
    token = await acquire_lock(
        redis,
        tenant_id=tenant_id,
        facility_id=facility_id,
        ttl_seconds=settings.scoring_lock_ttl_seconds,
    )
    if token is None:
        summary.skipped_busy += 1
        return
    try:
        # A fresh pair of sessions per facility: a failure rolls back and discards only this one.
        async with main_factory() as main_session, timescale_factory() as timescale_session:
            await scope_session_to_tenant(main_session, tenant_id)
            await scope_session_to_tenant(timescale_session, tenant_id)

            if not await facility_has_new_readings(
                main_session, timescale_session, redis, tenant_id=tenant_id, facility_id=facility_id
            ):
                summary.skipped_no_new_data += 1
                return

            now = datetime.now(UTC)
            saved = await score(
                main_session,
                timescale_session,
                tenant_id=tenant_id,
                facility_id=facility_id,
                now=now,
            )
        await redis.set(
            _LAST_ATTEMPT_KEY.format(tenant_id=tenant_id, facility_id=facility_id),
            now.isoformat(),
            ex=_LAST_ATTEMPT_TTL_SECONDS,
        )
        summary.scored += 1
        if saved:
            try:
                await publish_scores_updated(
                    redis,
                    tenant_id=tenant_id,
                    event=ScoresUpdated(
                        facility_id=facility_id, scored=len(saved), computed_at=now
                    ),
                )
            except Exception:  # a lost push is harmless: the UI also polls
                logger.exception("could not publish scores_updated for facility %s", facility_id)
    except Exception as exc:
        summary.failed += 1
        summary.failures.append(f"{facility_id}: {type(exc).__name__}")
        logger.exception("scheduled scoring failed for facility %s", facility_id)
    finally:
        await release_lock(redis, tenant_id=tenant_id, facility_id=facility_id, token=token)


async def score_all_facilities(
    *,
    main_factory: async_sessionmaker[AsyncSession],
    timescale_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    score: ScoreFn = score_facility,
) -> ScoringRunSummary:
    """One scheduled pass over every tenant's facilities. Never raises for a single bad tenant or
    facility; the summary says what happened."""
    summary = ScoringRunSummary()
    # `tenants` has no RLS (it is the tenant-identity table), so the runtime role can list it.
    async with main_factory() as session:
        tenant_ids = list((await session.execute(select(Tenant.id))).scalars())

    for tenant_id in tenant_ids:
        try:
            async with main_factory() as session:
                await scope_session_to_tenant(session, tenant_id)
                facility_ids = list(
                    (
                        await session.execute(
                            select(Facility.id).where(Facility.tenant_id == tenant_id)
                        )
                    ).scalars()
                )
        except Exception:
            summary.tenants_failed += 1
            logger.exception("could not list facilities for tenant %s", tenant_id)
            continue
        for facility_id in facility_ids:
            await _score_one_facility(
                main_factory=main_factory,
                timescale_factory=timescale_factory,
                redis=redis,
                tenant_id=tenant_id,
                facility_id=facility_id,
                score=score,
                summary=summary,
            )
    return summary
