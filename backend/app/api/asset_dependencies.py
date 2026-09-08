import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import require_roles
from app.core.tenant_context import TenantContext, get_tenant_context, get_tenant_scoped_session
from app.models.user import Role
from app.schemas.requests.asset_dependencies import (
    AssetDependencyCreateRequest,
    AssetDependencyResponse,
)
from app.services.asset_dependency_service import (
    create_dependency,
    delete_dependency,
    list_dependencies,
)

router = APIRouter(tags=["asset-dependencies"])

# Same write/read split as assets.py - linking/unlinking assets edits the facility's
# graph, so it's restricted the same way asset placement is.
WRITE_ROLES = (Role.TENANT_ADMIN, Role.FACILITY_MANAGER, Role.SUPERADMIN)


@router.post(
    "/facilities/{facility_id}/asset-dependencies",
    response_model=AssetDependencyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_facility_asset_dependency(
    facility_id: uuid.UUID,
    body: AssetDependencyCreateRequest,
    context: TenantContext = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> AssetDependencyResponse:
    dependency = await create_dependency(
        session,
        tenant_id=context.tenant_id,
        facility_id=facility_id,
        parent_asset_id=body.parent_asset_id,
        child_asset_id=body.child_asset_id,
    )
    return AssetDependencyResponse.model_validate(dependency)


@router.get(
    "/facilities/{facility_id}/asset-dependencies", response_model=list[AssetDependencyResponse]
)
async def list_facility_asset_dependencies(
    facility_id: uuid.UUID,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> list[AssetDependencyResponse]:
    dependencies = await list_dependencies(
        session, tenant_id=context.tenant_id, facility_id=facility_id
    )
    return [AssetDependencyResponse.model_validate(dep) for dep in dependencies]


@router.delete(
    "/facilities/{facility_id}/asset-dependencies/{dependency_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_facility_asset_dependency(
    facility_id: uuid.UUID,
    dependency_id: uuid.UUID,
    context: TenantContext = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> None:
    await delete_dependency(
        session, tenant_id=context.tenant_id, facility_id=facility_id, dependency_id=dependency_id
    )
