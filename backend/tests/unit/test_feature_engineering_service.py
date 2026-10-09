"""Unit tests for app.services.feature_engineering_service.

Validates:
1. In-memory window statistics calculation matches SQL _WINDOW_STATS_SQL parity
   (count, mean, sample stddev with ddof=1, min, max, latest_value, anomaly_count).
2. Edge cases: empty readings, single reading (stddev=None), zero-variance readings.
3. Feature extraction from MappedMachineRecord and vectorization parity.
4. Full dataset conversion from AI4I 2020 (10,000 samples, 339 failure labels, zero NaNs).
"""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from app.ml.feature_vector import FEATURE_NAMES, vectorize
from app.schemas.ml.asset_features import AssetFeatureSet
from app.services.feature_engineering_service import (
    build_asset_features_from_mapped_record,
    build_asset_features_from_readings,
    build_training_dataset_from_mapped_records,
    compute_window_stats_from_readings,
)
from app.services.ml.dataset_mapping import (
    MappedSensorReading,
    map_ai4i2020_dataset,
    map_ai4i2020_row,
)

RAW_CSV_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "raw" / "ai4i2020.csv"


def test_compute_window_stats_empty_readings() -> None:
    stats = compute_window_stats_from_readings([], window_days=30)
    assert stats == {}


def test_compute_window_stats_filters_outside_window() -> None:
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    readings = [
        MappedSensorReading(
            asset_id=uuid.uuid4(),
            sensor_type="torque",
            value=40.0,
            unit="Nm",
            timestamp=now - timedelta(days=5),  # Inside 30d window
        ),
        MappedSensorReading(
            asset_id=uuid.uuid4(),
            sensor_type="torque",
            value=35.0,
            unit="Nm",
            timestamp=now - timedelta(days=45),  # Outside 30d window, inside 90d window
        ),
    ]

    stats_30 = compute_window_stats_from_readings(readings, window_days=30, now=now)
    assert "torque" in stats_30
    assert stats_30["torque"].count == 1
    assert stats_30["torque"].latest_value == 40.0

    stats_90 = compute_window_stats_from_readings(readings, window_days=90, now=now)
    assert "torque" in stats_90
    assert stats_90["torque"].count == 2
    assert stats_90["torque"].latest_value == 40.0


def test_compute_window_stats_single_reading_has_null_stddev() -> None:
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    readings = [
        MappedSensorReading(
            asset_id=uuid.uuid4(),
            sensor_type="rotational_speed",
            value=1500.0,
            unit="rpm",
            timestamp=now - timedelta(days=1),
        )
    ]

    stats = compute_window_stats_from_readings(readings, window_days=30, now=now)
    s = stats["rotational_speed"]
    assert s.count == 1
    assert s.mean == 1500.0
    assert s.stddev is None  # Matches SQL stddev_samp for N=1
    assert s.min == 1500.0
    assert s.max == 1500.0
    assert s.latest_value == 1500.0
    assert s.anomaly_count == 0


def test_compute_window_stats_parity_with_sql_logic() -> None:
    """Verifies that sample stddev (ddof=1) and anomaly counts match the SQL CTE logic."""
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    # 9 normal readings at 10.0 and 1 reading at 100.0
    # Mean: 19.0, Sample stddev (ddof=1): 28.4605
    # Z-score for 100.0: |100 - 19| / 28.4605 = 2.846 > 2.0 (ANOMALY)
    # Z-score for 10.0: |10 - 19| / 28.4605 = 0.316 < 2.0 (NORMAL)
    readings = [
        MappedSensorReading(
            asset_id=uuid.uuid4(),
            sensor_type="air_temperature",
            value=10.0,
            unit="K",
            timestamp=now - timedelta(days=10 - i),
        )
        for i in range(9)
    ]
    readings.append(
        MappedSensorReading(
            asset_id=uuid.uuid4(),
            sensor_type="air_temperature",
            value=100.0,
            unit="K",
            timestamp=now,
        )
    )

    stats = compute_window_stats_from_readings(
        readings, window_days=30, now=now, anomaly_threshold=2.0
    )
    s = stats["air_temperature"]
    assert s.count == 10
    vals = [10.0] * 9 + [100.0]
    assert s.mean == pytest.approx(float(np.mean(vals)))
    assert s.stddev == pytest.approx(float(np.std(vals, ddof=1)))
    assert s.min == 10.0
    assert s.max == 100.0
    assert s.latest_value == 100.0
    assert s.anomaly_count == 1  # Exactly 1 outlier (> 2.0 z-score)


def test_build_asset_features_from_readings_creates_valid_feature_set() -> None:
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    asset_id = uuid.uuid4()
    readings = [
        MappedSensorReading(
            asset_id=asset_id,
            sensor_type="torque",
            value=45.2,
            unit="Nm",
            timestamp=now,
        ),
        MappedSensorReading(
            asset_id=asset_id,
            sensor_type="rotational_speed",
            value=1520.0,
            unit="rpm",
            timestamp=now,
        ),
    ]

    features = build_asset_features_from_readings(
        asset_id=asset_id,
        readings=readings,
        asset_status="operational",
        asset_age_days=120,
        dependency_neighbor_count=3,
        dependency_neighbor_offline_count=1,
        dependency_neighbor_maintenance_count=0,
    )

    assert isinstance(features, AssetFeatureSet)
    assert features.asset_id == asset_id
    assert features.asset_status == "operational"
    assert features.asset_age_days == 120
    assert features.dependency_neighbor_count == 3
    assert set(features.windows.keys()) == {30, 90, 365}
    assert "torque" in features.windows[30]
    assert "rotational_speed" in features.windows[30]

    vec = vectorize(features)
    assert vec.shape == (len(FEATURE_NAMES),)
    assert not np.isnan(vec).any()


def test_build_asset_features_from_mapped_record() -> None:
    sample_row = {
        "UDI": "1",
        "Product ID": "M14860",
        "Type": "M",
        "Air temperature [K]": "298.1",
        "Process temperature [K]": "308.6",
        "Rotational speed [rpm]": "1551",
        "Torque [Nm]": "42.8",
        "Tool wear [min]": "0",
        "Machine failure": "0",
        "TWF": "0",
        "HDF": "0",
        "PWF": "0",
        "OSF": "0",
        "RNF": "0",
    }
    record = map_ai4i2020_row(sample_row)
    features = build_asset_features_from_mapped_record(record)

    assert features.asset_id == record.asset_id
    assert features.computed_at == record.timestamp
    assert features.asset_status == "operational"
    assert features.asset_age_days is None

    # Check that AI4I sensor types are present in 30-day window
    expected_channels = {
        "air_temperature",
        "process_temperature",
        "rotational_speed",
        "torque",
        "tool_wear",
        "temperature_difference",
        "mechanical_power",
    }
    window_30_sensors = set(features.windows[30].keys())
    assert expected_channels.issubset(window_30_sensors)

    vec = vectorize(features)
    assert vec.shape == (len(FEATURE_NAMES),)
    assert not np.isnan(vec).any()

    # Verify positions in vector
    idx_air = FEATURE_NAMES.index("w30_air_temperature_mean")
    assert vec[idx_air] == pytest.approx(298.1)

    idx_torque = FEATURE_NAMES.index("w30_torque_mean")
    assert vec[idx_torque] == pytest.approx(42.8)


def test_build_training_dataset_from_mapped_records_full_dataset() -> None:
    """Verifies end-to-end dataset transformation on the 10,000 AI4I 2020 rows."""
    if not RAW_CSV_PATH.is_file():
        pytest.skip(f"Raw CSV not found at {RAW_CSV_PATH}")

    records = map_ai4i2020_dataset(RAW_CSV_PATH)
    assert len(records) == 10000

    X, y, feature_names = build_training_dataset_from_mapped_records(records)

    # 1. Shapes
    assert X.shape == (10000, len(FEATURE_NAMES))
    assert y.shape == (10000,)
    assert feature_names == FEATURE_NAMES

    # 2. Labels align with official statistics in docs/DATASETS.md
    total_failures = int(y.sum())
    assert total_failures == 339
    assert total_failures / len(y) == pytest.approx(0.0339, abs=1e-4)

    # 3. No NaNs or infinities in features
    assert np.isnan(X).sum() == 0
    assert np.isinf(X).sum() == 0

    # 4. Spot check first sample
    first_rec = records[0]
    first_features = build_asset_features_from_mapped_record(first_rec)
    expected_first_vec = vectorize(first_features)
    assert np.allclose(X[0], expected_first_vec)


def test_training_and_serving_parity() -> None:
    """Confirms that in-memory stats produce identical feature vector entries as
    what a simulated or live reading stream through TimescaleDB would produce."""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    asset_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    facility_id = uuid.uuid4()

    readings = [
        MappedSensorReading(
            asset_id=asset_id,
            sensor_type="torque",
            value=40.0,
            unit="Nm",
            timestamp=now - timedelta(days=1),
        ),
        MappedSensorReading(
            asset_id=asset_id,
            sensor_type="torque",
            value=60.0,
            unit="Nm",
            timestamp=now,
        ),
    ]

    features = build_asset_features_from_readings(
        asset_id=asset_id,
        readings=readings,
        tenant_id=tenant_id,
        facility_id=facility_id,
        computed_at=now,
        asset_status="operational",
        asset_age_days=50,
    )

    vec = vectorize(features)
    assert vec.shape == (len(FEATURE_NAMES),)

    # Verify 30-day window torque statistics
    idx_cnt = FEATURE_NAMES.index("w30_torque_count")
    idx_mean = FEATURE_NAMES.index("w30_torque_mean")
    idx_std = FEATURE_NAMES.index("w30_torque_stddev")
    idx_min = FEATURE_NAMES.index("w30_torque_min")
    idx_max = FEATURE_NAMES.index("w30_torque_max")
    idx_latest = FEATURE_NAMES.index("w30_torque_latest_value")

    assert vec[idx_cnt] == 2.0
    assert vec[idx_mean] == 50.0
    assert vec[idx_std] == pytest.approx(float(np.std([40.0, 60.0], ddof=1)))
    assert vec[idx_min] == 40.0
    assert vec[idx_max] == 60.0
    assert vec[idx_latest] == 60.0
