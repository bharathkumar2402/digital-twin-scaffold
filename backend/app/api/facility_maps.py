import uuid

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import require_roles
from app.core.tenant_context import TenantContext, get_tenant_scoped_session
from app.models.user import Role
from app.schemas.requests.facility_maps import FacilityMapUploadResponse
from app.services.facility_map_service import create_map_upload

router = APIRouter(tags=["facility-maps"])


@router.post(
    "/facilities/{facility_id}/map",
    response_model=FacilityMapUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_facility_map(
    facility_id: uuid.UUID,
    file: UploadFile = File(...),
    context: TenantContext = Depends(require_roles(Role.TENANT_ADMIN, Role.SUPERADMIN)),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> FacilityMapUploadResponse:
    content = await file.read()
    upload = await create_map_upload(
        session,
        tenant_id=context.tenant_id,
        facility_id=facility_id,
        filename=file.filename or "upload",
        content=content,
    )
    return FacilityMapUploadResponse.model_validate(upload)
