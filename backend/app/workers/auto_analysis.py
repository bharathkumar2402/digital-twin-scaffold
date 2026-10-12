"""Celery task for automatic agent analysis (Phase L task L.7).

Enqueued by the scheduled scoring pass (`app/workers/scheduled_scoring.py`) when a machine newly
enters high risk, or when a facility has never been analysed. Runs on the worker, never in an API
request (root CLAUDE.md rule 4). The lock, the 10-minute floor and the pipeline call live in
`app/services/auto_analysis_service.py`; this is only the entry point.

Same engine-per-run pattern as the scoring task: `asyncio.run` makes a new event loop per run, so
pooled module-level engines would be bound to a closed loop.
"""

import asyncio
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.redis_client import get_redis_client
from app.services.auto_analysis_service import AnalysisReason, run_auto_analysis

TASK_NAME = "run_scheduled_analysis"


@celery_app.task(name=TASK_NAME)
def run_scheduled_analysis(tenant_id: str, facility_id: str, reason: str) -> str:
    """Runs one automatic analysis; returns the outcome (ran / failed / busy / too_soon)."""
    if reason == "auto_high_risk":
        checked: AnalysisReason = "auto_high_risk"
    elif reason == "auto_startup":
        checked = "auto_startup"
    else:
        raise ValueError(f"unknown analysis reason: {reason!r}")
    return asyncio.run(_run(uuid.UUID(tenant_id), uuid.UUID(facility_id), checked))


async def _run(tenant_id: uuid.UUID, facility_id: uuid.UUID, reason: AnalysisReason) -> str:
    engine = create_async_engine(settings.app_database_url, poolclass=NullPool)
    redis = get_redis_client()
    try:
        return await run_auto_analysis(
            main_factory=async_sessionmaker(engine, expire_on_commit=False),
            redis=redis,
            tenant_id=tenant_id,
            facility_id=facility_id,
            reason=reason,
        )
    finally:
        await redis.aclose()
        await engine.dispose()
