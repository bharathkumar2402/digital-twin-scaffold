"""Celery task wrapping `risk_inference_service.score_facility` (issue 3.3).

Runs on the main app's `celery-worker` (has DB + MinIO credentials), on the default
queue - not the sandbox worker, and not on `sandbox-results` (that queue is reserved
for `app/workers/callback_tasks.py`'s narrow sandbox-result writes). Triggered
on-demand via `POST /facilities/{facility_id}/risk-scores/compute`
(`app/api/risk_scores.py`), not wired to a raw sensor-threshold breach - that
anomaly-triggered, debounced path is task 3.5, a separate and deliberately
distinct trigger mechanism (root CLAUDE.md rule 4).
"""

import asyncio
import uuid

from app.core.celery_app import celery_app
from app.core.db import async_session_factory, timescale_session_factory
from app.core.tenant_context import scope_session_to_tenant
from app.services.risk_inference_service import score_facility


@celery_app.task(name="compute_facility_risk_scores")
def compute_facility_risk_scores(tenant_id: str, facility_id: str) -> int:
    """Scores every asset in a facility and persists the results.

    Returns the number of assets scored. Raises (failing the Celery task) rather than
    writing a partial/garbage result if the model can't be loaded, an asset's feature
    vector doesn't match the model's expected columns, or a computed score fails
    `RiskScoreResult`'s validation - never silently writes unvalidated data, per the
    root CLAUDE.md's non-negotiable rule 1.
    """
    return asyncio.run(
        _compute_facility_risk_scores_async(uuid.UUID(tenant_id), uuid.UUID(facility_id))
    )


async def _compute_facility_risk_scores_async(tenant_id: uuid.UUID, facility_id: uuid.UUID) -> int:
    async with (
        async_session_factory() as main_session,
        timescale_session_factory() as timescale_session,
    ):
        await scope_session_to_tenant(main_session, tenant_id)
        await scope_session_to_tenant(timescale_session, tenant_id)

        saved = await score_facility(
            main_session,
            timescale_session,
            tenant_id=tenant_id,
            facility_id=facility_id,
        )
        return len(saved)
