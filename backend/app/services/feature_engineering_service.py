import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_dependency import AssetDependency
from app.models.facility import Facility
from app.schemas.ml.asset_features import AssetFeatureSet, SensorWindowStats

# Matches PROJECT_PLAN.md §4.3's "TimescaleDB telemetry query (last 30/90/365 days)"
# tool description for the Risk Assessment agent.
WINDOW_DAYS = (30, 90, 365)

# Threshold for the window-local anomaly count in SensorWindowStats - see that
# schema's docstring for why this is a separate concept from task 3.4's live detector.
ANOMALY_Z_SCORE_THRESHOLD = 2.0

# One CTE-based query per window: `stats` aggregates the window, `latest` finds the
# most recent reading per sensor_type in the window, `anomalies` re-scans the window
# using `stats`' own mean/stddev to count outliers. Fully parameterized - no
# string-interpolated values, only the trusted, hardcoded table/column names.
_WINDOW_STATS_SQL = text(
    """
    WITH stats AS (
        SELECT
            sensor_type,
            count(*) AS cnt,
            avg(value) AS mean,
            stddev_samp(value) AS stddev,
            min(value) AS min_value,
            max(value) AS max_value
        FROM sensor_readings
        WHERE tenant_id = :tenant_id
          AND asset_id = :asset_id
          AND timestamp >= :window_start
        GROUP BY sensor_type
    ),
    latest AS (
        SELECT DISTINCT ON (sensor_type) sensor_type, value AS latest_value
        FROM sensor_readings
        WHERE tenant_id = :tenant_id
          AND asset_id = :asset_id
          AND timestamp >= :window_start
        ORDER BY sensor_type, timestamp DESC
    ),
    anomalies AS (
        SELECT r.sensor_type, count(*) AS anomaly_count
        FROM sensor_readings r
        JOIN stats s ON s.sensor_type = r.sensor_type
        WHERE r.tenant_id = :tenant_id
          AND r.asset_id = :asset_id
          AND r.timestamp >= :window_start
          AND s.stddev IS NOT NULL
          AND s.stddev > 0
          AND abs(r.value - s.mean) > :anomaly_threshold * s.stddev
        GROUP BY r.sensor_type
    )
    SELECT
        stats.sensor_type,
        stats.cnt,
        stats.mean,
        stats.stddev,
        stats.min_value,
        stats.max_value,
        latest.latest_value,
        COALESCE(anomalies.anomaly_count, 0) AS anomaly_count
    FROM stats
    LEFT JOIN latest ON latest.sensor_type = stats.sensor_type
    LEFT JOIN anomalies ON anomalies.sensor_type = stats.sensor_type
    """
)


async def _get_facility_or_404(
    main_session: AsyncSession, *, tenant_id: uuid.UUID, facility_id: uuid.UUID
) -> Facility:
    result = await main_session.execute(
        select(Facility).where(Facility.id == facility_id, Facility.tenant_id == tenant_id)
    )
    facility = result.scalar_one_or_none()
    if facility is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Facility not found")
    return facility


async def _get_asset_or_404(
    main_session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    asset_id: uuid.UUID,
) -> Asset:
    result = await main_session.execute(
        select(Asset).where(
            Asset.id == asset_id,
            Asset.facility_id == facility_id,
            Asset.tenant_id == tenant_id,
        )
    )
    asset = result.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    return asset


async def _dependency_neighbor_counts(
    main_session: AsyncSession, *, tenant_id: uuid.UUID, facility_id: uuid.UUID, asset_id: uuid.UUID
) -> tuple[int, int, int]:
    """Counts of directly-connected assets (either edge direction) by current status -
    the "adjacent asset failures" signal from PROJECT_PLAN.md §4.3, built from data that
    actually exists in this repo (see AssetFeatureSet's docstring)."""
    edges = await main_session.execute(
        select(AssetDependency.parent_asset_id, AssetDependency.child_asset_id).where(
            AssetDependency.tenant_id == tenant_id,
            AssetDependency.facility_id == facility_id,
            (AssetDependency.parent_asset_id == asset_id)
            | (AssetDependency.child_asset_id == asset_id),
        )
    )
    neighbor_ids = {
        other_id
        for parent_id, child_id in edges.all()
        for other_id in (parent_id, child_id)
        if other_id != asset_id
    }
    if not neighbor_ids:
        return 0, 0, 0

    neighbors = await main_session.execute(
        select(Asset.status).where(
            Asset.tenant_id == tenant_id,
            Asset.facility_id == facility_id,
            Asset.id.in_(neighbor_ids),
        )
    )
    statuses = [row[0] for row in neighbors.all()]
    offline = sum(1 for s in statuses if s == "offline")
    maintenance = sum(1 for s in statuses if s == "maintenance")
    return len(neighbor_ids), offline, maintenance


async def _asset_sensor_windows(
    timescale_session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    asset_id: uuid.UUID,
    now: datetime,
) -> dict[int, dict[str, SensorWindowStats]]:
    windows: dict[int, dict[str, SensorWindowStats]] = {}
    for window_days in WINDOW_DAYS:
        window_start = now - timedelta(days=window_days)
        result = await timescale_session.execute(
            _WINDOW_STATS_SQL,
            {
                "tenant_id": tenant_id,
                "asset_id": asset_id,
                "window_start": window_start,
                "anomaly_threshold": ANOMALY_Z_SCORE_THRESHOLD,
            },
        )
        windows[window_days] = {
            row.sensor_type: SensorWindowStats(
                sensor_type=row.sensor_type,
                window_days=window_days,
                count=row.cnt,
                mean=row.mean,
                stddev=row.stddev,
                min=row.min_value,
                max=row.max_value,
                latest_value=row.latest_value,
                anomaly_count=row.anomaly_count,
            )
            for row in result.all()
        }
    return windows


async def build_asset_features(
    main_session: AsyncSession,
    timescale_session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    asset_id: uuid.UUID,
    now: datetime | None = None,
) -> AssetFeatureSet:
    """Builds one asset's feature vector: rolling sensor-window stats from TimescaleDB
    (`main_session`/`timescale_session` are two separate physical databases - see
    `app/models/sensor_reading.py`) plus asset age and dependency-neighbor status from
    the main database. Both sessions must already be RLS-scoped to `tenant_id` (via
    `get_tenant_scoped_session`/`get_timescale_scoped_session`) - the explicit
    `tenant_id` filters here are defense-in-depth, same pattern as every other
    tenant-scoped service in this repo.
    """
    now = now or datetime.now(UTC)

    asset = await _get_asset_or_404(
        main_session, tenant_id=tenant_id, facility_id=facility_id, asset_id=asset_id
    )
    neighbor_count, offline_count, maintenance_count = await _dependency_neighbor_counts(
        main_session, tenant_id=tenant_id, facility_id=facility_id, asset_id=asset_id
    )
    windows = await _asset_sensor_windows(
        timescale_session, tenant_id=tenant_id, asset_id=asset_id, now=now
    )

    asset_age_days = (now.date() - asset.installed_date).days if asset.installed_date else None

    return AssetFeatureSet(
        asset_id=asset.id,
        tenant_id=tenant_id,
        facility_id=facility_id,
        computed_at=now,
        asset_status=asset.status,
        asset_age_days=asset_age_days,
        dependency_neighbor_count=neighbor_count,
        dependency_neighbor_offline_count=offline_count,
        dependency_neighbor_maintenance_count=maintenance_count,
        windows=windows,
    )


async def build_facility_features(
    main_session: AsyncSession,
    timescale_session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    now: datetime | None = None,
) -> list[AssetFeatureSet]:
    """Feature vectors for every asset in a facility - the shape 3.2's training script
    and 3.3's inference task will pull from."""
    await _get_facility_or_404(main_session, tenant_id=tenant_id, facility_id=facility_id)

    result = await main_session.execute(
        select(Asset.id).where(Asset.facility_id == facility_id, Asset.tenant_id == tenant_id)
    )
    asset_ids = [row[0] for row in result.all()]

    return [
        await build_asset_features(
            main_session,
            timescale_session,
            tenant_id=tenant_id,
            facility_id=facility_id,
            asset_id=asset_id,
            now=now,
        )
        for asset_id in asset_ids
    ]
