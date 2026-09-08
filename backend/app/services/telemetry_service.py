import uuid

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sensor_reading import SensorReading
from app.schemas.requests.telemetry import SensorReadingIn

MAX_TELEMETRY_LIMIT = 500
DEFAULT_TELEMETRY_LIMIT = 200


async def ingest_readings(
    session: AsyncSession, *, tenant_id: uuid.UUID, readings: list[SensorReadingIn]
) -> int:
    """Bulk-inserts validated sensor readings for the caller's tenant.

    Uses a single Core `insert` rather than per-row ORM `add()`: this is an append-only
    ingest path with no need for identity-map tracking, and a bulk statement is the only
    way to keep a large batch to one round trip.
    """
    rows = [
        {
            "tenant_id": tenant_id,
            "asset_id": reading.asset_id,
            "sensor_type": reading.sensor_type,
            "value": reading.value,
            "unit": reading.unit,
            "timestamp": reading.timestamp,
        }
        for reading in readings
    ]

    await session.execute(insert(SensorReading), rows)
    await session.commit()
    return len(rows)


async def get_asset_telemetry(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    asset_id: uuid.UUID,
    sensor_type: str | None = None,
    limit: int = DEFAULT_TELEMETRY_LIMIT,
) -> list[SensorReading]:
    """Most-recent-first readings for one asset, for the detail-panel telemetry chart.

    `tenant_id` is filtered explicitly here (defense-in-depth alongside RLS), same
    pattern as `asset_service`/`facility_map_service` - see repo rule 2. `limit` is
    capped server-side so a client can't force an unbounded scan of a hypertable.
    """
    capped_limit = min(limit, MAX_TELEMETRY_LIMIT)
    stmt = (
        select(SensorReading)
        .where(SensorReading.tenant_id == tenant_id, SensorReading.asset_id == asset_id)
        .order_by(SensorReading.timestamp.desc())
        .limit(capped_limit)
    )
    if sensor_type is not None:
        stmt = stmt.where(SensorReading.sensor_type == sensor_type)

    result = await session.execute(stmt)
    return list(result.scalars().all())
