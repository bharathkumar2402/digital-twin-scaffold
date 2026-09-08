import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_context import (
    TenantContext,
    get_tenant_context,
    get_timescale_scoped_session,
)
from app.schemas.requests.telemetry import (
    TelemetryIngestRequest,
    TelemetryIngestResponse,
    TelemetryReadingResponse,
)
from app.services.telemetry_service import (
    DEFAULT_TELEMETRY_LIMIT,
    MAX_TELEMETRY_LIMIT,
    get_asset_telemetry,
    ingest_readings,
)

router = APIRouter(tags=["telemetry"])


@router.post(
    "/telemetry", response_model=TelemetryIngestResponse, status_code=status.HTTP_201_CREATED
)
async def ingest_telemetry(
    body: TelemetryIngestRequest,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_timescale_scoped_session),
) -> TelemetryIngestResponse:
    accepted = await ingest_readings(session, tenant_id=context.tenant_id, readings=body.readings)
    return TelemetryIngestResponse(accepted=accepted)


@router.get(
    "/facilities/{facility_id}/assets/{asset_id}/telemetry",
    response_model=list[TelemetryReadingResponse],
)
async def get_facility_asset_telemetry(
    facility_id: uuid.UUID,  # not cross-checked against the reading, see docstring below
    asset_id: uuid.UUID,
    sensor_type: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_TELEMETRY_LIMIT, ge=1, le=MAX_TELEMETRY_LIMIT),
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_timescale_scoped_session),
) -> list[TelemetryReadingResponse]:
    """Recent readings for one asset, for the detail-panel telemetry chart.

    `facility_id` is part of the URL for symmetry with the assets routes but isn't
    cross-checked against the reading: `sensor_readings` lives on a physically separate
    TimescaleDB instance with no cross-database FK to `assets` (see
    `app/models/sensor_reading.py`), so isolation here comes from RLS plus the explicit
    `tenant_id` filter in `get_asset_telemetry` - the same pattern `POST /telemetry`
    already uses. Open to any authenticated tenant member, matching the read-only asset
    routes.
    """
    readings = await get_asset_telemetry(
        session,
        tenant_id=context.tenant_id,
        asset_id=asset_id,
        sensor_type=sensor_type,
        limit=limit,
    )
    return [TelemetryReadingResponse.model_validate(reading) for reading in readings]
