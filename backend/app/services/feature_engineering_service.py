import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ml.feature_vector import FEATURE_NAMES, WINDOW_DAYS, vectorize
from app.models.asset import Asset
from app.models.asset_dependency import AssetDependency
from app.models.facility import Facility
from app.schemas.ml.asset_features import AssetFeatureSet, SensorWindowStats
from app.services.ml.dataset_mapping import MappedMachineRecord

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


def _normalize_dt(dt: datetime) -> datetime:
    """Normalize datetime to timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def compute_window_stats_from_readings(
    readings: Sequence[Any],
    window_days: int,
    *,
    now: datetime | None = None,
    anomaly_threshold: float = ANOMALY_Z_SCORE_THRESHOLD,
) -> dict[str, SensorWindowStats]:
    """Computes rolling-window aggregate statistics across a sequence of sensor readings.

    Implements the identical statistical logic as TimescaleDB's `_WINDOW_STATS_SQL`:
    - count: number of readings in the window [now - window_days, now]
    - mean: arithmetic mean (avg(value))
    - stddev: sample standard deviation (stddev_samp(value), ddof=1; None if count <= 1)
    - min / max: min_value and max_value in the window
    - latest_value: value from the reading with the latest timestamp in the window
    - anomaly_count: count of readings where abs(value - mean) > anomaly_threshold * stddev
      (0 if stddev is None or stddev == 0.0)

    This ensures complete mathematical and structural parity between in-memory dataset
    feature extraction for offline training and TimescaleDB rolling-window queries for
    live inference, eliminating training/serving skew.
    """
    if not readings:
        return {}

    extracted: list[tuple[str, float, datetime]] = []
    for r in readings:
        if isinstance(r, dict):
            s_type = str(r["sensor_type"])
            val = float(r["value"])
            ts = _normalize_dt(r["timestamp"])
        else:
            s_type = str(r.sensor_type)
            val = float(r.value)
            ts = _normalize_dt(r.timestamp)
        extracted.append((s_type, val, ts))

    if now is None:
        ref_now = max(ts for _, _, ts in extracted)
    else:
        ref_now = _normalize_dt(now)

    window_start = ref_now - timedelta(days=window_days)

    by_sensor: dict[str, list[tuple[float, datetime]]] = {}
    for s_type, val, ts in extracted:
        if window_start <= ts <= ref_now:
            by_sensor.setdefault(s_type, []).append((val, ts))

    result: dict[str, SensorWindowStats] = {}
    for s_type, s_data in by_sensor.items():
        cnt = len(s_data)
        vals = [v for v, _ in s_data]
        mean_val = float(np.mean(vals))
        min_val = float(np.min(vals))
        max_val = float(np.max(vals))

        if cnt > 1:
            std_val: float | None = float(np.std(vals, ddof=1))
        else:
            std_val = None

        latest_reading = max(s_data, key=lambda pair: pair[1])
        latest_val: float | None = latest_reading[0]

        if std_val is not None and std_val > 0.0:
            anomaly_cnt = sum(
                1 for v in vals if abs(v - mean_val) > (anomaly_threshold * std_val)
            )
        else:
            anomaly_cnt = 0

        result[s_type] = SensorWindowStats(
            sensor_type=s_type,
            window_days=window_days,
            count=cnt,
            mean=mean_val,
            stddev=std_val,
            min=min_val,
            max=max_val,
            latest_value=latest_val,
            anomaly_count=anomaly_cnt,
        )

    return result


def build_asset_features_from_readings(
    asset_id: uuid.UUID,
    readings: Sequence[Any],
    *,
    tenant_id: uuid.UUID | None = None,
    facility_id: uuid.UUID | None = None,
    computed_at: datetime | None = None,
    asset_status: str = "operational",
    asset_age_days: int | None = None,
    dependency_neighbor_count: int = 0,
    dependency_neighbor_offline_count: int = 0,
    dependency_neighbor_maintenance_count: int = 0,
    anomaly_threshold: float = ANOMALY_Z_SCORE_THRESHOLD,
) -> AssetFeatureSet:
    """Builds an AssetFeatureSet from an in-memory sequence of sensor readings.

    Aggregates rolling windows (30, 90, 365 days) via `compute_window_stats_from_readings`.
    """
    if computed_at is None:
        if readings:
            extracted_ts = [
                _normalize_dt(r["timestamp"] if isinstance(r, dict) else r.timestamp)
                for r in readings
            ]
            computed_at = max(extracted_ts)
        else:
            computed_at = datetime.now(UTC)
    else:
        computed_at = _normalize_dt(computed_at)

    resolved_tenant_id = tenant_id or uuid.UUID("00000000-0000-0000-0000-000000000000")
    resolved_facility_id = facility_id or uuid.UUID("00000000-0000-0000-0000-000000000000")

    windows: dict[int, dict[str, SensorWindowStats]] = {}
    for window_days in WINDOW_DAYS:
        windows[window_days] = compute_window_stats_from_readings(
            readings,
            window_days=window_days,
            now=computed_at,
            anomaly_threshold=anomaly_threshold,
        )

    return AssetFeatureSet(
        asset_id=asset_id,
        tenant_id=resolved_tenant_id,
        facility_id=resolved_facility_id,
        computed_at=computed_at,
        asset_status=asset_status,
        asset_age_days=asset_age_days,
        dependency_neighbor_count=dependency_neighbor_count,
        dependency_neighbor_offline_count=dependency_neighbor_offline_count,
        dependency_neighbor_maintenance_count=dependency_neighbor_maintenance_count,
        windows=windows,
    )


def build_asset_features_from_mapped_record(
    record: MappedMachineRecord,
    *,
    tenant_id: uuid.UUID | None = None,
    facility_id: uuid.UUID | None = None,
    asset_status: str = "operational",
    asset_age_days: int | None = None,
    dependency_neighbor_count: int = 0,
    dependency_neighbor_offline_count: int = 0,
    dependency_neighbor_maintenance_count: int = 0,
    anomaly_threshold: float = ANOMALY_Z_SCORE_THRESHOLD,
) -> AssetFeatureSet:
    """Builds an AssetFeatureSet from a MappedMachineRecord (AI4I 2020 dataset)."""
    return build_asset_features_from_readings(
        asset_id=record.asset_id,
        readings=record.readings,
        tenant_id=tenant_id,
        facility_id=facility_id,
        computed_at=record.timestamp,
        asset_status=asset_status,
        asset_age_days=asset_age_days,
        dependency_neighbor_count=dependency_neighbor_count,
        dependency_neighbor_offline_count=dependency_neighbor_offline_count,
        dependency_neighbor_maintenance_count=dependency_neighbor_maintenance_count,
        anomaly_threshold=anomaly_threshold,
    )


def build_training_dataset_from_mapped_records(
    records: Sequence[MappedMachineRecord],
    *,
    tenant_id: uuid.UUID | None = None,
    facility_id: uuid.UUID | None = None,
    asset_status: str = "operational",
    asset_age_days: int | None = None,
    dependency_neighbor_count: int = 0,
    dependency_neighbor_offline_count: int = 0,
    dependency_neighbor_maintenance_count: int = 0,
    anomaly_threshold: float = ANOMALY_Z_SCORE_THRESHOLD,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Builds the complete training feature matrix X, target labels y, and feature_names
    from a collection of mapped machine records.

    Returns:
        X: 2D numpy array of shape (N, len(FEATURE_NAMES)), float64.
        y: 1D numpy array of shape (N,), int64 binary failure indicators (1=failure, 0=normal).
        feature_names: List of column names aligned positionally with X.
    """
    feature_sets = [
        build_asset_features_from_mapped_record(
            record,
            tenant_id=tenant_id,
            facility_id=facility_id,
            asset_status=asset_status,
            asset_age_days=asset_age_days,
            dependency_neighbor_count=dependency_neighbor_count,
            dependency_neighbor_offline_count=dependency_neighbor_offline_count,
            dependency_neighbor_maintenance_count=dependency_neighbor_maintenance_count,
            anomaly_threshold=anomaly_threshold,
        )
        for record in records
    ]

    X = np.stack([vectorize(fs) for fs in feature_sets])
    y = np.array([1 if record.is_failure else 0 for record in records], dtype=np.int64)

    return X, y, list(FEATURE_NAMES)


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
    the "adjacent asset failures" signal from PROJECT_PLAN.md A 4.3, built from data that
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
