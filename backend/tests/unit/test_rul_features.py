"""Tests for RUL feature extraction, labels, scaling, mapping, and the serving schema."""

from datetime import UTC, datetime

import numpy as np
import pytest
from pydantic import ValidationError

from app.ml.cmapss_profile import COLUMNS, SENSOR_COLUMNS, parse_cmapss_table, parse_rul_vector
from app.ml.rul_dataset import build_test_dataset, build_train_dataset
from app.ml.rul_features import RulFeatureConfig, fit_config, window_features
from app.schemas.ml.rul_features import RulSnapshot, RulWindowIn
from app.services.ml.dataset_mapping import CMAPSS_SENSOR_MAP, map_cmapss_rows_to_readings
from scripts.acquire_cmapss import DEFAULT_OUTPUT_DIR


def _engine(unit: int, n: int, seed: int, s1: float = 0.0, s3: float = 100.0) -> np.ndarray:
    """Synthetic engine: sensor_2 degrades linearly, sensor_3 is noise, sensor_1 is constant."""
    rng = np.random.default_rng(seed)
    rows = np.zeros((n, len(COLUMNS)))
    rows[:, 0] = unit
    rows[:, 1] = np.arange(1, n + 1)
    rows[:, 2] = s1
    rows[:, 4] = s3
    rows[:, 5] = 518.67
    rows[:, 6] = 640 + np.arange(n) * 0.5 + rng.normal(0, 0.1, n)
    rows[:, 7] = rng.normal(1500, 3, n)
    for c in range(8, 26):
        rows[:, c] = rng.normal(10 * c, 1, n)
    return rows


@pytest.fixture
def train_table() -> np.ndarray:
    return np.vstack([_engine(1, 60, 1), _engine(2, 80, 2), _engine(3, 45, 3)])


@pytest.fixture
def config(train_table: np.ndarray) -> RulFeatureConfig:
    return fit_config(train_table)


def test_constant_sensor_dropped(config: RulFeatureConfig) -> None:
    assert "sensor_1" not in config.sensors
    assert "sensor_2" in config.sensors
    assert len(config.feature_names) == 5 * len(config.sensors) + 2


def test_training_row_equals_serving_window_features(
    train_table: np.ndarray, config: RulFeatureConfig
) -> None:
    """Skew test: the training matrix row for (engine, cycle) is exactly what serving computes."""
    data = build_train_dataset(train_table, config)
    engine = train_table[train_table[:, 0] == 2]
    for cycle in (1, 7, 30, 31, 55, 80):
        served = window_features(engine[:cycle], config)  # serving passes the full history
        idx = int(np.where((data.units == 2) & (data.cycles == cycle))[0][0])
        np.testing.assert_array_equal(data.X[idx], served)


def test_no_future_leakage(train_table: np.ndarray, config: RulFeatureConfig) -> None:
    engine = train_table[train_table[:, 0] == 2]
    before = window_features(engine[:40], config)
    altered = engine.copy()
    altered[40:, 6:] += 1e6  # corrupt everything after cycle 40
    np.testing.assert_array_equal(before, window_features(altered[:40], config))
    np.testing.assert_array_equal(
        build_train_dataset(altered, config).X[:40], build_train_dataset(engine, config).X[:40]
    )


def test_train_labels_capped_monotonic_and_end_at_zero(
    train_table: np.ndarray, config: RulFeatureConfig
) -> None:
    data = build_train_dataset(train_table, config)
    assert data.y.max() == 79  # shorter than the cap, so no clipping here
    assert data.y_true.max() == 79  # engine 2 has 80 cycles
    for unit in (1, 2, 3):
        y = data.y_true[data.units == unit]
        assert y[-1] == 0
        assert (np.diff(y) == -1).all()
        assert (np.diff(data.y[data.units == unit]) <= 0).all()


def test_cap_is_applied_with_small_cap(train_table: np.ndarray) -> None:
    cfg = fit_config(train_table, rul_cap=10)
    data = build_train_dataset(train_table, cfg)
    assert data.y.max() == 10
    assert data.y_true.max() > 10


def test_test_labels_come_from_rul_file_not_trajectory(config: RulFeatureConfig) -> None:
    test_table = np.vstack([_engine(1, 50, 4), _engine(2, 33, 5)])
    data = build_test_dataset(test_table, np.array([300.0, 12.0]), config)
    assert data.y.tolist() == [config.rul_cap, 12.0]
    assert data.y_true.tolist() == [300.0, 12.0]
    assert data.units.tolist() == [1, 2]
    assert data.cycles.tolist() == [50, 33]
    with pytest.raises(ValueError, match="RUL file has 3 entries but test table has 2"):
        build_test_dataset(test_table, np.array([1.0, 2.0, 3.0]), config)


def test_scaler_fit_uses_training_data_only(
    train_table: np.ndarray, config: RulFeatureConfig
) -> None:
    assert fit_config(train_table.copy()) == config
    # Building a test set with wildly different values must not alter the fitted config.
    test_table = _engine(1, 40, 9)
    test_table[:, 6:] *= 5
    build_test_dataset(test_table, np.array([20.0]), config)
    assert fit_config(train_table) == config


def test_short_history_gives_finite_features(config: RulFeatureConfig) -> None:
    engine = _engine(1, 3, 6)
    one = window_features(engine[:1], config)
    three = window_features(engine, config)
    assert np.isfinite(one).all()
    assert np.isfinite(three).all()
    assert one[-1] == 1
    assert three[-1] == 3  # window_len feature


def test_long_history_is_trimmed_to_window(config: RulFeatureConfig) -> None:
    engine = _engine(1, 200, 7)
    feats = window_features(engine, config)
    assert feats[-1] == config.long_window
    np.testing.assert_array_equal(feats, window_features(engine[-config.long_window :], config))


def test_degrading_sensor_has_positive_slope(config: RulFeatureConfig) -> None:
    feats = window_features(_engine(1, 60, 1), config)
    assert feats[config.feature_names.index("sensor_2_slope_long")] > 0


def test_unseen_operating_condition_rejected(config: RulFeatureConfig) -> None:
    alien = _engine(1, 10, 8, s1=35.0, s3=60.0)
    with pytest.raises(ValueError, match="operating condition not seen in training data"):
        window_features(alien, config)


def test_nan_and_bad_shape_rejected(config: RulFeatureConfig) -> None:
    engine = _engine(1, 10, 8)
    engine[4, 7] = np.nan
    with pytest.raises(ValueError, match="NaN or infinite"):
        window_features(engine, config)
    with pytest.raises(ValueError, match="non-empty"):
        window_features(np.zeros((0, len(COLUMNS))), config)
    with pytest.raises(ValueError, match="expected an"):
        fit_config(np.zeros((5, 3)))


def test_deterministic_and_config_round_trip(
    train_table: np.ndarray, config: RulFeatureConfig
) -> None:
    restored = RulFeatureConfig.from_dict(config.to_dict())
    assert restored == config
    engine = train_table[train_table[:, 0] == 1]
    np.testing.assert_array_equal(
        window_features(engine, config), window_features(engine, restored)
    )


def test_multi_regime_scaling_removes_regime_offset() -> None:
    """Two regimes at very different sensor levels both normalize to ~0 mean."""
    a = _engine(1, 50, 1, s1=0.0, s3=100.0)
    b = _engine(2, 50, 2, s1=25.0, s3=60.0)
    b[:, 7] += 500.0
    cfg = fit_config(np.vstack([a, b]))
    assert len(cfg.regime_centers) == 2
    idx = cfg.sensors.index("sensor_3")
    fa = window_features(a[:30], cfg)[5 * idx + 2]  # mean_long
    fb = window_features(b[:30], cfg)[5 * idx + 2]
    assert abs(fa) < 1.0
    assert abs(fb) < 1.0


def test_sensor_map_covers_all_sensors_with_units() -> None:
    assert list(CMAPSS_SENSOR_MAP) == list(SENSOR_COLUMNS)
    assert CMAPSS_SENSOR_MAP["sensor_2"].symbol == "T24"
    assert CMAPSS_SENSOR_MAP["sensor_2"].sensor_type == "cmapss_s2"
    assert all(info.unit for info in CMAPSS_SENSOR_MAP.values())


def test_mapping_produces_schema_valid_readings_and_rejects_nan() -> None:
    rows = _engine(7, 3, 1)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    readings = map_cmapss_rows_to_readings("FD001", rows, start)
    assert len(readings) == 3 * 21
    assert readings[0].to_schema_in().sensor_type == "cmapss_s1"
    assert readings[-1].timestamp > readings[0].timestamp
    assert len({r.asset_id for r in readings}) == 1
    rows[1, 10] = np.nan
    with pytest.raises(ValueError, match="non-finite"):
        map_cmapss_rows_to_readings("FD001", rows, start)


def _snapshot(cycle: int, **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "cycle": cycle,
        "op_setting_1": 0.0,
        "op_setting_2": 0.0,
        "op_setting_3": 100.0,
        "sensors": {s: 1.0 for s in SENSOR_COLUMNS},
    }
    base.update(over)
    return base


def test_serving_schema_round_trip_and_rejections() -> None:
    window = RulWindowIn.model_validate({"snapshots": [_snapshot(1), _snapshot(2)]})
    table = window.to_table()
    assert table.shape == (2, len(COLUMNS))
    assert table[:, 1].tolist() == [1.0, 2.0]
    with pytest.raises(ValidationError, match="strictly increasing"):
        RulWindowIn.model_validate({"snapshots": [_snapshot(2), _snapshot(2)]})
    with pytest.raises(ValidationError, match="exactly sensor_1"):
        RulSnapshot.model_validate(_snapshot(1, sensors={"sensor_1": 1.0}))
    bad = {s: 1.0 for s in SENSOR_COLUMNS} | {"sensor_4": float("nan")}
    with pytest.raises(ValidationError, match="finite"):
        RulSnapshot.model_validate(_snapshot(1, sensors=bad))
    with pytest.raises(ValidationError):
        RulWindowIn.model_validate({"snapshots": []})


_have_data = pytest.mark.skipif(
    not (DEFAULT_OUTPUT_DIR / "train_FD001.txt").is_file(),
    reason="C-MAPSS not downloaded; run `python -m scripts.acquire_cmapss`",
)


@_have_data
def test_real_fd001_pipeline_end_to_end() -> None:
    train = parse_cmapss_table((DEFAULT_OUTPUT_DIR / "train_FD001.txt").read_text())
    test = parse_cmapss_table((DEFAULT_OUTPUT_DIR / "test_FD001.txt").read_text())
    rul = parse_rul_vector((DEFAULT_OUTPUT_DIR / "RUL_FD001.txt").read_text())
    cfg = fit_config(train)
    assert len(cfg.sensors) == 15  # 21 minus the 6 constant sensors in the profile
    assert len(cfg.regime_centers) == 1
    tr = build_train_dataset(train, cfg)
    te = build_test_dataset(test, rul, cfg)
    assert tr.X.shape == (20631, len(cfg.feature_names))
    assert te.X.shape == (100, len(cfg.feature_names))
    assert np.isfinite(tr.X).all()
    assert np.isfinite(te.X).all()
    assert tr.y.max() == cfg.rul_cap
    assert te.y_true.tolist() == rul.tolist()
