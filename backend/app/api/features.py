import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_context import (
    TenantContext,
    get_tenant_context,
    get_tenant_scoped_session,
    get_timescale_scoped_session,
)
from app.schemas.ml.asset_features import AssetFeatureSet
from app.services.feature_engineering_service import build_asset_features

router = APIRouter(tags=["features"])


@router.get("/facilities/{facility_id}/assets/{asset_id}/features", response_model=AssetFeatureSet)
async def get_asset_features(
    facility_id: uuid.UUID,
    asset_id: uuid.UUID,
    context: TenantContext = Depends(get_tenant_context),
    main_session: AsyncSession = Depends(get_tenant_scoped_session),
    timescale_session: AsyncSession = Depends(get_timescale_scoped_session),
) -> AssetFeatureSet:
    """The current feature vector for one asset - read-only, any authenticated tenant
    member (same role pattern as the telemetry route). Exists both to verify the
    feature-engineering pipeline end to end and for 3.3's inference Celery task to
    reuse via `feature_engineering_service.build_asset_features` directly rather than
    going back through HTTP.
    """
    return await build_asset_features(
        main_session,
        timescale_session,
        tenant_id=context.tenant_id,
        facility_id=facility_id,
        asset_id=asset_id,
    )
