import uuid

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sensor_reading import SensorReading
from app.schemas.requests.telemetry import SensorReadingIn


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
