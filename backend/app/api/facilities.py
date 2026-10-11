from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_context import TenantContext, get_tenant_context, get_tenant_scoped_session
from app.schemas.requests.facilities import FacilityResponse, FloorResponse
from app.services.facility_service import list_facilities

router = APIRouter(tags=["facilities"])


@router.get("/facilities", response_model=list[FacilityResponse])
async def list_my_facilities(
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> list[FacilityResponse]:
    """The caller's tenant's facilities (any role may read), for the facility picker."""
    infos = await list_facilities(session, tenant_id=context.tenant_id)
    return [
        FacilityResponse(
            id=info.facility.id,
            name=info.facility.name,
            latest_map_upload_id=info.latest_map_upload_id,
            asset_count=info.asset_count,
            floors=[
                FloorResponse(
                    level=f.level,
                    name=f.name,
                    map_upload_id=f.map_upload_id,
                    asset_count=f.asset_count,
                )
                for f in info.floors
            ],
        )
        for info in infos
    ]
