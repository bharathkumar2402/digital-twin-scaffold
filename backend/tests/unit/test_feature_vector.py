"""Unit tests for app.ml.feature_vector - the fixed-shape input contract shared by
training (3.2) and inference (3.3)."""

import uuid
from datetime import UTC, datetime

import numpy as np

from app.ml.feature_vector import FEATURE_NAMES, SENSOR_TYPES, WINDOW_DAYS, vectorize
from app.schemas.ml.asset_features import AssetFeatureSet, SensorWindowStats


def _base_features(**overrides: object) -> AssetFeatureSet:
    defaults: dict[str, object] = dict(
        asset_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        facility_id=uuid.uuid4(),
        computed_at=datetime.now(UTC),
        asset_status="operational",
        asset_age_days=100,
        dependency_neighbor_count=2,
        dependency_neighbor_offline_count=1,
        dependency_neighbor_maintenance_count=0,
        windows={},
    )
    defaults.update(overrides)
    return AssetFeatureSet(**defaults)  # type: ignore[arg-type]


def test_vector_length_matches_feature_names() -> None:
    vector = vectorize(_base_features())
    assert vector.shape == (len(FEATURE_NAMES),)


def test_missing_windows_are_zero_filled_not_omitted() -> None:
    vector = vectorize(_base_features(windows={}))
    # Every window/sensor stat column should be exactly zero when no data exists.
    non_window_count = 7  # 3 status one-hot + age + 3 dependency counts
    assert np.all(vector[non_window_count:] == 0.0)


def test_asset_status_one_hot_is_mutually_exclusive() -> None:
    vector = vectorize(_base_features(asset_status="offline"))
    status_names = [n for n in FEATURE_NAMES if n.startswith("asset_status_")]
    status_values = vector[: len(status_names)]
    assert list(status_values) == [0.0, 0.0, 1.0]  # operational, maintenance, offline


def test_asset_age_none_becomes_zero() -> None:
    vector = vectorize(_base_features(asset_age_days=None))
    age_index = FEATURE_NAMES.index("asset_age_days")
    assert vector[age_index] == 0.0


def test_present_window_stats_populate_correct_columns() -> None:
    stats = SensorWindowStats(
        sensor_type="vibration_mm_s",
        window_days=30,
        count=10,
        mean=5.0,
        stddev=1.0,
        min=2.0,
        max=8.0,
        latest_value=6.0,
        anomaly_count=3,
    )
    vector = vectorize(_base_features(windows={30: {"vibration_mm_s": stats}}))

    mean_index = FEATURE_NAMES.index("w30_vibration_mm_s_mean")
    anomaly_index = FEATURE_NAMES.index("w30_vibration_mm_s_anomaly_count")
    assert vector[mean_index] == 5.0
    assert vector[anomaly_index] == 3.0

    # A sensor type with no data in that window stays zero.
    other_mean_index = FEATURE_NAMES.index("w30_temperature_c_mean")
    assert vector[other_mean_index] == 0.0


def test_feature_names_cover_every_window_and_sensor_combo() -> None:
    for window_days in WINDOW_DAYS:
        for sensor_type in SENSOR_TYPES:
            assert f"w{window_days}_{sensor_type}_mean" in FEATURE_NAMES
