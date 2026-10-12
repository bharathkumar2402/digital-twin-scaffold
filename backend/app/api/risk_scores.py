import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import require_roles
from app.core.tenant_context import TenantContext, get_tenant_context, get_tenant_scoped_session
from app.models.user import Role
from app.schemas.requests.risk_scores import (
    RiskHistoryPoint,
    RiskHistorySeries,
    RiskScoreComputeAcceptedResponse,
    RiskScoreResponse,
)
from app.services.risk_inference_service import (
    HistoryBucket,
    get_latest_risk_scores,
    get_risk_history,
)
from app.workers.risk_tasks import compute_facility_risk_scores

router = APIRouter(tags=["risk-scores"])

# Triggering a scoring run consumes compute/model resources, same write-role split as
# asset placement/linking; reading scores is open to any tenant member, matching the
# telemetry/features routes.
COMPUTE_ROLES = (Role.TENANT_ADMIN, Role.FACILITY_MANAGER, Role.SUPERADMIN)


@router.post(
    "/facilities/{facility_id}/risk-scores/compute",
    response_model=RiskScoreComputeAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_facility_risk_scoring(
    facility_id: uuid.UUID,
    context: TenantContext = Depends(require_roles(*COMPUTE_ROLES)),
) -> RiskScoreComputeAcceptedResponse:
    """Enqueues `compute_facility_risk_scores` on the main Celery worker. Doesn't
    check facility ownership here - the Celery task itself 404s via
    `risk_inference_service.score_facility`'s facility lookup, scoped to the caller's
    own `tenant_id` from the JWT (never a raw request parameter, per backend/CLAUDE.md)
    - a caller can't use this to enqueue scoring against another tenant's facility_id.
    """
    result = compute_facility_risk_scores.delay(str(context.tenant_id), str(facility_id))
    return RiskScoreComputeAcceptedResponse(task_id=result.id)


@router.get("/facilities/{facility_id}/risk-scores", response_model=list[RiskScoreResponse])
async def list_facility_risk_scores(
    facility_id: uuid.UUID,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> list[RiskScoreResponse]:
    scores = await get_latest_risk_scores(
        session, tenant_id=context.tenant_id, facility_id=facility_id
    )
    return [RiskScoreResponse.model_validate(score) for score in scores]


@router.get("/facilities/{facility_id}/risk-history", response_model=list[RiskHistorySeries])
async def list_facility_risk_history(
    facility_id: uuid.UUID,
    limit_per_asset: int = Query(default=10, ge=1, le=50),
    bucket: HistoryBucket | None = Query(default=None),
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> list[RiskHistorySeries]:
    """Each asset's last `limit_per_asset` scores (oldest first) for trend lines and
    "changed since the previous score" badges. Read-only and bounded (1-50 per asset); open to
    any tenant member like the latest-scores list. The tenant comes from the JWT, never from
    the request.

    `bucket=hour|day` (UTC) returns the newest score of each hour or day instead, and then
    `limit_per_asset` counts buckets, so a multi-day trend survives scoring every minute."""
    history = await get_risk_history(
        session,
        tenant_id=context.tenant_id,
        facility_id=facility_id,
        limit_per_asset=limit_per_asset,
        bucket=bucket,
    )
    return [
        RiskHistorySeries(
            asset_id=asset_id,
            points=[
                RiskHistoryPoint(computed_at=at, score=score, rul_cycles=rul)
                for at, score, rul in points
            ],
        )
        for asset_id, points in history.items()
    ]
