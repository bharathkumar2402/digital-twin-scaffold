"""Risk model is served on the same single-snapshot features it was trained on.

The defect this guards against: training saw one-reading windows (count=1, stddev=0,
anomaly_count=0) while serving fed 30/90/365-day windows of many readings.
"""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from app.ml import train as train_module
from app.ml.ai4i_replay import heldout_rows, pick_row, replay_readings
from app.ml.feature_vector import FEATURE_NAMES, vectorize
from app.services.feature_engineering_service import (
    SNAPSHOT_INCOHERENT,
    SNAPSHOT_INVALID,
    SNAPSHOT_MAX_SPREAD,
    SNAPSHOT_MISSING_SENSORS,
    SNAPSHOT_RAW_SENSORS,
    build_asset_features_from_mapped_record,
    build_asset_features_from_readings,
    snapshot_readings,
)
from app.services.ml.dataset_mapping import map_ai4i2020_dataset

CSV = Path(__file__).resolve().parents[2] / "data" / "raw" / "ai4i2020.csv"
pytestmark = pytest.mark.skipif(not CSV.exists(), reason="AI4I dataset not present")

T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def records() -> list:
    return map_ai4i2020_dataset(CSV)


def _latest(record) -> dict[str, tuple[float, datetime]]:
    return {r.sensor_type: (r.value, r.timestamp) for r in record.readings}


def _serving_vector(record) -> np.ndarray:
    built = snapshot_readings(_latest(record))
    assert not isinstance(built, str), built
    readings, at = built
    return vectorize(build_asset_features_from_readings(record.asset_id, readings, computed_at=at))


def test_serving_vector_equals_training_vector_for_real_rows(records: list) -> None:
    """The strongest guard: same record -> bit-identical model input on both paths."""
    failures = [r for r in records if r.is_failure]
    sample = [*failures, *records[:1500]]
    assert len(failures) == 339
    for record in sample:
        training = vectorize(build_asset_features_from_mapped_record(record))
        np.testing.assert_array_equal(_serving_vector(record), training)


def test_training_windows_are_single_snapshots_and_serving_matches(records: list) -> None:
    v = _serving_vector(records[10])
    for window in (30, 90, 365):
        for field, expected in (("count", 1.0), ("stddev", 0.0), ("anomaly_count", 0.0)):
            assert v[FEATURE_NAMES.index(f"w{window}_torque_{field}")] == expected


def test_derived_channels_are_recomputed_not_trusted(records: list) -> None:
    record = records[5]
    latest = _latest(record)
    honest = _serving_vector(record)
    # A feed reporting inconsistent derived channels must not change the model input.
    latest["temperature_difference"] = (9999.0, T0)
    latest["mechanical_power"] = (-5.0, T0)
    built = snapshot_readings(latest)
    assert not isinstance(built, str)
    readings, at = built
    spoofed = vectorize(
        build_asset_features_from_readings(record.asset_id, readings, computed_at=at)
    )
    np.testing.assert_array_equal(spoofed, honest)


@pytest.mark.parametrize("dropped", SNAPSHOT_RAW_SENSORS)
def test_any_missing_raw_sensor_means_no_score(records: list, dropped: str) -> None:
    latest = _latest(records[3])
    del latest[dropped]
    reason = snapshot_readings(latest)
    assert isinstance(reason, str) and reason.startswith(SNAPSHOT_MISSING_SENSORS)
    assert dropped in reason


def test_asset_with_no_ai4i_sensors_gets_no_score() -> None:
    reason = snapshot_readings({"cmapss_s1": (1.0, T0)})
    assert isinstance(reason, str) and reason.startswith(SNAPSHOT_MISSING_SENSORS)
    assert isinstance(snapshot_readings({}), str)


def test_snapshot_must_be_simultaneous(records: list) -> None:
    latest = _latest(records[3])
    ok_edge = dict(latest)
    ok_edge["torque"] = (latest["torque"][0], T0 - SNAPSHOT_MAX_SPREAD)
    ok_edge = {
        k: (v, T0 if k != "torque" else T0 - SNAPSHOT_MAX_SPREAD) for k, (v, _) in ok_edge.items()
    }
    assert not isinstance(snapshot_readings(ok_edge), str)  # exactly at the limit is fine
    too_far = dict(ok_edge)
    too_far["torque"] = (too_far["torque"][0], T0 - SNAPSHOT_MAX_SPREAD - timedelta(seconds=1))
    assert snapshot_readings(too_far) == SNAPSHOT_INCOHERENT


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_value_means_no_score(records: list, bad: float) -> None:
    latest = _latest(records[3])
    latest["tool_wear"] = (bad, latest["tool_wear"][1])
    assert snapshot_readings(latest) == SNAPSHOT_INVALID


def test_naive_and_aware_timestamps_are_handled(records: list) -> None:
    latest = {k: (v, ts.replace(tzinfo=None)) for k, (v, ts) in _latest(records[3]).items()}
    assert not isinstance(snapshot_readings(latest), str)


# ---- replay of held-out real rows -----------------------------------------------------------


def test_replayed_heldout_rows_are_valid_snapshots_and_match_training(records: list) -> None:
    rows = heldout_rows(CSV)
    assert len(rows) == 2000
    for failure in (True, False):
        row = pick_row(rows, failure=failure, nth=1)
        asset = uuid.uuid4()
        readings = replay_readings(row, asset, T0)
        assert {r.timestamp for r in readings} == {T0} and {r.asset_id for r in readings} == {asset}
        latest = {r.sensor_type: (r.value, r.timestamp) for r in readings}
        built = snapshot_readings(latest)
        assert not isinstance(built, str)
        record = next(r for r in records if r.udi == int(row["UDI"]))
        np.testing.assert_array_equal(
            vectorize(build_asset_features_from_readings(asset, built[0], computed_at=built[1])),
            vectorize(build_asset_features_from_mapped_record(record)),
        )
    with pytest.raises(IndexError):
        pick_row(rows, failure=True, nth=10_000)


def test_heldout_rows_are_exactly_the_rows_training_set_aside(
    records: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Couples the replay split to the real `train_risk_model` split, so 'held-out' stays true."""
    n = len(records)
    y = np.array([int(r.is_failure) for r in records])
    X = np.arange(n, dtype=float).reshape(-1, 1)  # the single feature IS the row index
    captured: dict[str, np.ndarray] = {}
    real_split = train_module.train_test_split

    def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        out = real_split(*args, **kwargs)
        captured["test_X"] = out[1]
        return out

    monkeypatch.setattr(train_module, "train_test_split", spy)
    train_module.train_risk_model(X, y, seed=0)
    trained_test_idx = {int(i) for i in captured["test_X"].ravel()}
    replay_idx = {int(r["UDI"]) - 1 for r in heldout_rows(CSV)}
    assert replay_idx == trained_test_idx
