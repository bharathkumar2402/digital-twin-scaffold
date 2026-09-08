import uuid

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.rbac import require_roles
from app.core.tenant_context import TenantContext, get_tenant_context, get_tenant_scoped_session
from app.models.facility_map_upload import UploadStatus
from app.models.user import Role
from app.schemas.requests.facility_maps import (
    FacilityMapUploadResponse,
    FacilityMapUploadStatusResponse,
)
from app.services.facility_map_service import create_map_upload, get_map_upload

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


@router.get(
    "/facilities/{facility_id}/map/{upload_id}",
    response_model=FacilityMapUploadStatusResponse,
)
async def get_facility_map_upload(
    facility_id: uuid.UUID,
    upload_id: uuid.UUID,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> FacilityMapUploadStatusResponse:
    upload = await get_map_upload(
        session,
        tenant_id=context.tenant_id,
        facility_id=facility_id,
        upload_id=upload_id,
    )
    tile_url_template = (
        settings.tile_url_template(upload.tile_prefix)
        if upload.status == UploadStatus.TILED and upload.tile_prefix
        else None
    )
    return FacilityMapUploadStatusResponse(
        id=upload.id,
        facility_id=upload.facility_id,
        original_filename=upload.original_filename,
        format=upload.format,
        status=upload.status.value,
        tile_prefix=upload.tile_prefix,
        tile_url_template=tile_url_template,
    )
