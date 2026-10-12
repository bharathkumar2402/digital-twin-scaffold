import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import require_roles
from app.core.redis_client import get_redis
from app.core.tenant_context import (
    TenantContext,
    get_tenant_context,
    get_tenant_scoped_session,
    get_timescale_scoped_session,
)
from app.models.user import Role
from app.schemas.ml.alert_notification import AlertNotification
from app.schemas.requests.telemetry import (
    AssetFreshness,
    TelemetryFreshnessResponse,
    TelemetryIngestRequest,
    TelemetryIngestResponse,
    TelemetryReadingResponse,
)
from app.services.alert_debounce_service import evaluate_anomaly_batch
from app.services.alert_publish_service import publish_alert
from app.services.asset_service import list_assets
from app.services.telemetry_service import (
    DEFAULT_TELEMETRY_LIMIT,
    MAX_TELEMETRY_LIMIT,
    get_asset_telemetry,
    get_last_reading_times,
    ingest_readings,
)

router = APIRouter(tags=["telemetry"])

# Writing sensor data is restricted: a viewer is read-only (and self-registration creates
# viewers, so without this anyone who signs up could write telemetry). Real devices will need
# per-device API keys; that is out of scope (docs/LIVE_DATA_PLAN.md D7).
INGEST_ROLES = (Role.TECHNICIAN, Role.FACILITY_MANAGER, Role.TENANT_ADMIN, Role.SUPERADMIN)


@router.post(
    "/telemetry", response_model=TelemetryIngestResponse, status_code=status.HTTP_201_CREATED
)
async def ingest_telemetry(
    body: TelemetryIngestRequest,
    context: TenantContext = Depends(require_roles(*INGEST_ROLES)),
    session: AsyncSession = Depends(get_timescale_scoped_session),
    redis: Redis = Depends(get_redis),
) -> TelemetryIngestResponse:
    accepted, anomalies = await ingest_readings(
        session, tenant_id=context.tenant_id, readings=body.readings
    )

    # Issue 3.6: route the same batch's anomalies through 3.5's debounce decision
    # engine, then publish only the decisions it says should actually fire (issue
    # 3.5's docstrings are explicit that the service itself never does this - wiring
    # a `True` decision to something downstream is this task's job). Readings-by-value
    # is looked up from `anomalies` (not re-queried) since it's the exact same
    # `AnomalyCheckResult` the debounce engine just evaluated.
    anomalies_by_key = {(a.asset_id, a.sensor_type): a for a in anomalies}
    decisions = await evaluate_anomaly_batch(
        redis, tenant_id=context.tenant_id, anomalies=anomalies
    )
    for decision in decisions:
        if not decision.should_trigger_light_rescore:
            continue
        anomaly = anomalies_by_key[(decision.asset_id, decision.sensor_type)]
        notification = AlertNotification(
            asset_id=anomaly.asset_id,
            sensor_type=anomaly.sensor_type,
            value=anomaly.value,
            z_score=anomaly.z_score,
            triggered_at=decision.evaluated_at,
        )
        await publish_alert(redis, tenant_id=context.tenant_id, notification=notification)

    return TelemetryIngestResponse(accepted=accepted, anomalies=anomalies)


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


@router.get(
    "/facilities/{facility_id}/telemetry-freshness",
    response_model=TelemetryFreshnessResponse,
)
async def get_facility_telemetry_freshness(
    facility_id: uuid.UUID,
    context: TenantContext = Depends(get_tenant_context),
    main_session: AsyncSession = Depends(get_tenant_scoped_session),
    session: AsyncSession = Depends(get_timescale_scoped_session),
) -> TelemetryFreshnessResponse:
    """When each of the facility's assets last reported, for the "No recent data" status.

    The asset list comes from the main database (404 for a facility that is not the caller's
    tenant's); the readings live on the separate TimescaleDB, so the ids are passed across.
    Open to any authenticated tenant member, like the other read-only routes.
    """
    assets = await list_assets(main_session, tenant_id=context.tenant_id, facility_id=facility_id)
    as_of, last = await get_last_reading_times(
        session, tenant_id=context.tenant_id, asset_ids=[a.id for a in assets]
    )
    if as_of is None:  # no assets: nothing was queried, so take the clock here
        as_of = datetime.now(UTC)
    return TelemetryFreshnessResponse(
        as_of=as_of,
        assets=[AssetFreshness(asset_id=a.id, last_reading_at=last.get(a.id)) for a in assets],
    )
