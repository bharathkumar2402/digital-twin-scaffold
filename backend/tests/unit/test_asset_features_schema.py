import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.ml.asset_features import AssetFeatureSet, SensorWindowStats
from app.services.feature_engineering_service import ANOMALY_Z_SCORE_THRESHOLD, WINDOW_DAYS

VALID_WINDOW_STATS = {
    "sensor_type": "temperature",
    "window_days": 30,
    "count": 12,
    "mean": 40.1,
    "stddev": 1.2,
    "min": 38.0,
    "max": 42.0,
    "latest_value": 41.0,
    "anomaly_count": 1,
}


def _feature_set(**overrides) -> dict:
    base = {
        "asset_id": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "facility_id": str(uuid.uuid4()),
        "computed_at": datetime.now(UTC).isoformat(),
        "asset_status": "operational",
        "asset_age_days": 400,
        "dependency_neighbor_count": 2,
        "dependency_neighbor_offline_count": 0,
        "dependency_neighbor_maintenance_count": 1,
        "windows": {30: {"temperature": VALID_WINDOW_STATS}},
    }
    base.update(overrides)
    return base


def test_valid_window_stats_parses() -> None:
    stats = SensorWindowStats.model_validate(VALID_WINDOW_STATS)
    assert stats.window_days == 30
    assert stats.anomaly_count == 1


def test_window_stats_allows_null_aggregates_for_empty_window() -> None:
    """An asset with no readings in a window still gets a stats row (count=0, nulls
    for mean/stddev/min/max/latest) rather than the window being omitted entirely -
    the risk model needs a consistent shape to featurize "no data" as a signal."""
    stats = SensorWindowStats.model_validate(
        {
            **VALID_WINDOW_STATS,
            "count": 0,
            "mean": None,
            "stddev": None,
            "min": None,
            "max": None,
            "latest_value": None,
            "anomaly_count": 0,
        }
    )
    assert stats.count == 0
    assert stats.mean is None


def test_window_stats_is_frozen() -> None:
    stats = SensorWindowStats.model_validate(VALID_WINDOW_STATS)
    with pytest.raises(ValidationError):
        stats.count = 99  # type: ignore[misc]


def test_valid_feature_set_parses() -> None:
    feature_set = AssetFeatureSet.model_validate(_feature_set())
    assert feature_set.windows[30]["temperature"].sensor_type == "temperature"
    assert feature_set.asset_age_days == 400


def test_feature_set_allows_null_asset_age_when_no_installed_date() -> None:
    feature_set = AssetFeatureSet.model_validate(_feature_set(asset_age_days=None))
    assert feature_set.asset_age_days is None


def test_feature_set_missing_windows_rejected() -> None:
    payload = _feature_set()
    del payload["windows"]
    with pytest.raises(ValidationError):
        AssetFeatureSet.model_validate(payload)


def test_window_days_matches_project_plan_spec() -> None:
    """PROJECT_PLAN.md §4.3: "TimescaleDB telemetry query (last 30/90/365 days)"."""
    assert WINDOW_DAYS == (30, 90, 365)


def test_anomaly_threshold_is_positive() -> None:
    assert ANOMALY_Z_SCORE_THRESHOLD > 0
