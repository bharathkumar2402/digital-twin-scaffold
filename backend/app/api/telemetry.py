from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_context import (
    TenantContext,
    get_tenant_context,
    get_timescale_scoped_session,
)
from app.schemas.requests.telemetry import TelemetryIngestRequest, TelemetryIngestResponse
from app.services.telemetry_service import ingest_readings

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
