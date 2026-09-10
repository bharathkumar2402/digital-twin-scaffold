import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import require_roles
from app.core.tenant_context import TenantContext, get_tenant_context, get_tenant_scoped_session
from app.models.user import Role
from app.schemas.requests.risk_scores import (
    RiskScoreComputeAcceptedResponse,
    RiskScoreResponse,
)
from app.services.risk_inference_service import get_latest_risk_scores
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
