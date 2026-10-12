"""Automatic agent analysis in the worker (Phase L task L.7; root CLAUDE.md rules 4 and 1).

The scheduled scoring pass (`scheduled_scoring_service`) decides WHEN an analysis is worth running:
a machine newly crossing into high risk, or a facility that has never been analysed. This module
decides whether the run is allowed and performs it:

* never inside an API request - it is only called from the Celery task in
  `app/workers/auto_analysis.py`;
* a per-facility Redis lock, so two runs never overlap;
* a per-facility floor (`SET NX EX`), so a burst of triggers collapses to at most one run per
  `auto_analysis_min_interval_seconds` (default 10 min). The floor is claimed before the run and
  is NOT cleared when the run fails: a failing pipeline must not be retried every minute;
* the existing validated pipeline (`run_pipeline_for_facility`), unchanged - an invalid agent
  output is retried once and then halts the branch, and every outcome, failures included, is
  recorded in `agent_runs` with the reason in `trigger`.

Nothing here is wired to a raw sensor threshold (rule 4).
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.tenant_context import scope_session_to_tenant
from app.services.agent_run_service import run_pipeline_for_facility
from app.services.scheduled_scoring_service import acquire_lock, release_lock

logger = logging.getLogger(__name__)

AnalysisReason = Literal["auto_high_risk", "auto_startup"]
AnalysisOutcome = Literal["ran", "failed", "busy", "too_soon"]

# Same convention as the scoring keys: the tenant id is part of every key.
_LOCK_KEY = "analysis-lock:{tenant_id}:{facility_id}"
_FLOOR_KEY = "analysis-last:{tenant_id}:{facility_id}"

PipelineFn = Callable[..., Awaitable[Any]]


async def run_auto_analysis(
    *,
    main_factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    reason: AnalysisReason,
    pipeline: PipelineFn = run_pipeline_for_facility,
) -> AnalysisOutcome:
    """One automatic analysis attempt. Never raises: the outcome says what happened."""
    token = await acquire_lock(
        redis,
        tenant_id=tenant_id,
        facility_id=facility_id,
        ttl_seconds=settings.auto_analysis_lock_ttl_seconds,
        key_template=_LOCK_KEY,
    )
    if token is None:
        return "busy"
    try:
        claimed = await redis.set(
            _FLOOR_KEY.format(tenant_id=tenant_id, facility_id=facility_id),
            reason,
            nx=True,
            ex=settings.auto_analysis_min_interval_seconds,
        )
        if not claimed:
            return "too_soon"
        try:
            async with main_factory() as session:
                await scope_session_to_tenant(session, tenant_id)
                await pipeline(
                    session,
                    tenant_id=tenant_id,
                    facility_id=facility_id,
                    trigger="scheduled",
                    recorded_trigger=reason,
                )
        except Exception:  # the pipeline already recorded a `failed` run before re-raising
            logger.exception("automatic analysis failed for facility %s", facility_id)
            return "failed"
        return "ran"
    finally:
        await release_lock(
            redis,
            tenant_id=tenant_id,
            facility_id=facility_id,
            token=token,
            key_template=_LOCK_KEY,
        )
