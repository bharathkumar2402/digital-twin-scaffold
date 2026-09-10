"""Flattens an `AssetFeatureSet` into a fixed-length, fixed-order numeric vector.

This is the single source of truth for the risk model's input shape - both the
training script (3.2) and the inference service (3.3) must vectorize through
`vectorize()` so a model trained here is guaranteed to see the same column layout
at inference time. Sensor types are hardcoded to the four produced by
`backend/scripts/iot_data_generator.py` (this repo's only real telemetry source) -
an asset missing a sensor type or window entirely gets zero-filled stats rather than
a variable-length vector, which XGBoost can't accept.
"""

import numpy as np

from app.schemas.ml.asset_features import AssetFeatureSet, SensorWindowStats

SENSOR_TYPES: tuple[str, ...] = (
    "temperature_c",
    "pressure_kpa",
    "vibration_mm_s",
    "humidity_pct",
)

WINDOW_DAYS: tuple[int, ...] = (30, 90, 365)

ASSET_STATUSES: tuple[str, ...] = ("operational", "maintenance", "offline")

_STAT_FIELDS: tuple[str, ...] = (
    "count",
    "mean",
    "stddev",
    "min",
    "max",
    "latest_value",
    "anomaly_count",
)

_ZERO_STATS = {field: 0.0 for field in _STAT_FIELDS}


def _build_feature_names() -> list[str]:
    names = [f"asset_status_{status}" for status in ASSET_STATUSES]
    names += [
        "asset_age_days",
        "dependency_neighbor_count",
        "dependency_neighbor_offline_count",
        "dependency_neighbor_maintenance_count",
    ]
    for window_days in WINDOW_DAYS:
        for sensor_type in SENSOR_TYPES:
            for field in _STAT_FIELDS:
                names.append(f"w{window_days}_{sensor_type}_{field}")
    return names


FEATURE_NAMES: list[str] = _build_feature_names()


def _stat_values(stats: SensorWindowStats | None) -> dict[str, float]:
    if stats is None:
        return _ZERO_STATS
    return {
        "count": float(stats.count),
        "mean": stats.mean if stats.mean is not None else 0.0,
        "stddev": stats.stddev if stats.stddev is not None else 0.0,
        "min": stats.min if stats.min is not None else 0.0,
        "max": stats.max if stats.max is not None else 0.0,
        "latest_value": stats.latest_value if stats.latest_value is not None else 0.0,
        "anomaly_count": float(stats.anomaly_count),
    }


def vectorize(features: AssetFeatureSet) -> np.ndarray:
    """Returns a 1-D float64 array whose entries line up positionally with
    `FEATURE_NAMES` - never reorder one without the other."""
    values: list[float] = [
        1.0 if features.asset_status == status else 0.0 for status in ASSET_STATUSES
    ]
    values.append(float(features.asset_age_days) if features.asset_age_days is not None else 0.0)
    values.append(float(features.dependency_neighbor_count))
    values.append(float(features.dependency_neighbor_offline_count))
    values.append(float(features.dependency_neighbor_maintenance_count))

    for window_days in WINDOW_DAYS:
        window = features.windows.get(window_days, {})
        for sensor_type in SENSOR_TYPES:
            stat_values = _stat_values(window.get(sensor_type))
            for field in _STAT_FIELDS:
                values.append(stat_values[field])

    return np.array(values, dtype=np.float64)
