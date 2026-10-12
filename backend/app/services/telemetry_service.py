import uuid
from datetime import datetime

from sqlalchemy import bindparam, insert, select, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sensor_reading import SensorReading
from app.schemas.ml.anomaly import AnomalyCheckResult
from app.schemas.requests.telemetry import SensorReadingIn
from app.services.anomaly_detection_service import check_readings_for_anomalies

MAX_TELEMETRY_LIMIT = 500
DEFAULT_TELEMETRY_LIMIT = 200


async def ingest_readings(
    session: AsyncSession, *, tenant_id: uuid.UUID, readings: list[SensorReadingIn]
) -> tuple[int, list[AnomalyCheckResult]]:
    """Bulk-inserts validated sensor readings for the caller's tenant, then runs the
    live rolling-Z-score anomaly check (issue 3.4) on the same batch.

    Uses a single Core `insert` rather than per-row ORM `add()`: this is an append-only
    ingest path with no need for identity-map tracking, and a bulk statement is the only
    way to keep a large batch to one round trip. The anomaly check runs *before* commit,
    in the same transaction as the insert: `scope_session_to_tenant`'s GUC is set via
    `SET LOCAL`, which only lives for the current transaction (see
    `app/core/tenant_context.py`) - committing first and querying after would run the
    baseline query with no GUC set, which RLS fails closed on (zero rows, not an error),
    silently breaking every anomaly check. This is safe: the baseline query's own
    `timestamp < cutoff` predicate already excludes this batch's own rows from its
    baseline (see `anomaly_detection_service`), regardless of transaction visibility.
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
    anomalies = await check_readings_for_anomalies(
        session, tenant_id=tenant_id, readings=readings
    )
    await session.commit()
    return len(rows), anomalies


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


# Newest reading time per asset. LATERAL ... ORDER BY timestamp DESC LIMIT 1 per asset (served by
# ix_sensor_readings_asset_sensor_time's leading (asset_id, ...) columns), never DISTINCT /
# DISTINCT ON over sensor_readings: TimescaleDB SkipScan crashes under RLS (L.2b).
_LAST_READING_SQL = text(
    """
    SELECT g.asset_id, l.ts, now() AS as_of
    FROM unnest(:asset_ids) AS g(asset_id)
    LEFT JOIN LATERAL (
        SELECT r.timestamp AS ts
        FROM sensor_readings r
        WHERE r.tenant_id = :tenant_id AND r.asset_id = g.asset_id
        ORDER BY r.timestamp DESC
        LIMIT 1
    ) AS l ON true
    """
).bindparams(bindparam("asset_ids", type_=ARRAY(UUID(as_uuid=True))))


async def get_last_reading_times(
    session: AsyncSession, *, tenant_id: uuid.UUID, asset_ids: list[uuid.UUID]
) -> tuple[datetime | None, dict[uuid.UUID, datetime | None]]:
    """(server now, {asset_id: newest reading time or None}) for the given assets.

    The asset ids come from the caller's own tenant-checked asset list; `tenant_id` is filtered
    explicitly on top of RLS (repo rule 2), so an id from another tenant yields None.
    """
    if not asset_ids:
        return None, {}
    result = await session.execute(
        _LAST_READING_SQL, {"asset_ids": asset_ids, "tenant_id": tenant_id}
    )
    rows = result.all()
    return rows[0].as_of, {row.asset_id: row.ts for row in rows}
