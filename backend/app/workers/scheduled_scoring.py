"""Celery task for the scheduled scoring pass (Phase L task L.4).

Run by Celery beat every `settings.scoring_interval_seconds` (`beat_schedule` in
app/core/celery_app.py) on the default queue, on the main worker (it has DB + MinIO credentials).
All the logic lives in `app/services/scheduled_scoring_service.py`; this is only the entry point.

Each run opens its own database engines and disposes them at the end. The task is a sync function
that calls `asyncio.run`, so every run has a NEW event loop; the module-level engines in
`app.core.db` keep pooled connections bound to the previous (closed) loop, which breaks the second
run of a job that fires every minute.
"""

import asyncio
import uuid

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.redis_client import get_redis_client
from app.services.scheduled_scoring_service import score_all_facilities
from app.workers.auto_analysis import run_scheduled_analysis

TASK_NAME = "score_all_facilities"


@celery_app.task(name=TASK_NAME)
def run_scheduled_scoring() -> dict[str, int]:
    """Scores every facility that has new readings; returns the run's counts."""
    return asyncio.run(_run())


def _build_engines() -> tuple[AsyncEngine, AsyncEngine]:
    return (
        create_async_engine(settings.app_database_url, poolclass=NullPool),
        create_async_engine(settings.app_timescale_url, poolclass=NullPool),
    )


def _enqueue_analysis(tenant_id: uuid.UUID, facility_id: uuid.UUID, reason: str) -> None:
    """Hands the analysis to its own task; the scoring pass never runs the pipeline itself."""
    run_scheduled_analysis.delay(str(tenant_id), str(facility_id), reason)


async def _run() -> dict[str, int]:
    main_engine, timescale_engine = _build_engines()
    redis = get_redis_client()
    try:
        summary = await score_all_facilities(
            main_factory=async_sessionmaker(main_engine, expire_on_commit=False),
            timescale_factory=async_sessionmaker(timescale_engine, expire_on_commit=False),
            redis=redis,
            enqueue_analysis=_enqueue_analysis,
        )
    finally:
        await redis.aclose()
        await main_engine.dispose()
        await timescale_engine.dispose()
    return summary.as_dict()
