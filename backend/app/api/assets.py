import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import require_roles
from app.core.tenant_context import TenantContext, get_tenant_context, get_tenant_scoped_session
from app.models.user import Role
from app.schemas.requests.assets import AssetCreateRequest, AssetResponse, AssetUpdateRequest
from app.services.asset_service import (
    create_asset,
    delete_asset,
    get_asset,
    list_assets,
    update_asset,
)

router = APIRouter(tags=["assets"])

# Placement/editing changes the facility's map - restricted the same way map uploads
# are (facility_maps.py); technician/viewer stay read-only.
WRITE_ROLES = (Role.TENANT_ADMIN, Role.FACILITY_MANAGER, Role.SUPERADMIN)


@router.post(
    "/facilities/{facility_id}/assets",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_facility_asset(
    facility_id: uuid.UUID,
    body: AssetCreateRequest,
    context: TenantContext = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> AssetResponse:
    asset = await create_asset(
        session,
        tenant_id=context.tenant_id,
        facility_id=facility_id,
        name=body.name,
        type=body.type,
        x=body.x,
        y=body.y,
        status_=body.status,
        installed_date=body.installed_date,
        manufacturer=body.manufacturer,
        model=body.model,
    )
    return AssetResponse.model_validate(asset)


@router.get("/facilities/{facility_id}/assets", response_model=list[AssetResponse])
async def list_facility_assets(
    facility_id: uuid.UUID,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> list[AssetResponse]:
    assets = await list_assets(session, tenant_id=context.tenant_id, facility_id=facility_id)
    return [AssetResponse.model_validate(asset) for asset in assets]


@router.get("/facilities/{facility_id}/assets/{asset_id}", response_model=AssetResponse)
async def get_facility_asset(
    facility_id: uuid.UUID,
    asset_id: uuid.UUID,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> AssetResponse:
    asset = await get_asset(
        session, tenant_id=context.tenant_id, facility_id=facility_id, asset_id=asset_id
    )
    return AssetResponse.model_validate(asset)


@router.patch("/facilities/{facility_id}/assets/{asset_id}", response_model=AssetResponse)
async def update_facility_asset(
    facility_id: uuid.UUID,
    asset_id: uuid.UUID,
    body: AssetUpdateRequest,
    context: TenantContext = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> AssetResponse:
    updates = body.model_dump(exclude_unset=True)
    asset = await update_asset(
        session,
        tenant_id=context.tenant_id,
        facility_id=facility_id,
        asset_id=asset_id,
        updates=updates,
    )
    return AssetResponse.model_validate(asset)


@router.delete(
    "/facilities/{facility_id}/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_facility_asset(
    facility_id: uuid.UUID,
    asset_id: uuid.UUID,
    context: TenantContext = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> None:
    await delete_asset(
        session, tenant_id=context.tenant_id, facility_id=facility_id, asset_id=asset_id
    )
