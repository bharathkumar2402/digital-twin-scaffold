from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_context import TenantContext, get_tenant_context, get_tenant_scoped_session
from app.schemas.requests.facilities import FacilityResponse
from app.services.facility_service import list_facilities

router = APIRouter(tags=["facilities"])


@router.get("/facilities", response_model=list[FacilityResponse])
async def list_my_facilities(
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> list[FacilityResponse]:
    """The caller's tenant's facilities (any role may read), for the facility picker."""
    rows = await list_facilities(session, tenant_id=context.tenant_id)
    return [
        FacilityResponse(
            id=facility.id,
            name=facility.name,
            latest_map_upload_id=upload_id,
            asset_count=asset_count,
        )
        for facility, upload_id, asset_count in rows
    ]
