"""Unit tests for the pure z-score/flagging logic in `anomaly_detection_service`
(issue 3.4) - no database required, since `_z_score_and_flag` is split out from the
DB-querying `check_readings_for_anomalies` specifically to be testable this way.
"""

import pytest

from app.services.anomaly_detection_service import (
    MIN_SAMPLE_COUNT,
    Z_SCORE_THRESHOLD,
    _z_score_and_flag,
)


def test_value_far_from_mean_is_flagged() -> None:
    z_score, is_anomaly = _z_score_and_flag(
        value=100.0, mean=20.0, stddev=2.0, sample_count=50
    )
    assert z_score == pytest.approx(40.0)
    assert is_anomaly is True


def test_value_within_threshold_is_not_flagged() -> None:
    z_score, is_anomaly = _z_score_and_flag(
        value=21.0, mean=20.0, stddev=2.0, sample_count=50
    )
    assert z_score == pytest.approx(0.5)
    assert is_anomaly is False


def test_value_exactly_at_threshold_boundary_is_not_flagged() -> None:
    """Threshold is a strict `>`, matching 3.1's `ANOMALY_Z_SCORE_THRESHOLD` comparison
    style - a reading exactly on the boundary isn't an outlier yet."""
    mean, stddev = 20.0, 2.0
    boundary_value = mean + Z_SCORE_THRESHOLD * stddev
    _z_score, is_anomaly = _z_score_and_flag(
        value=boundary_value, mean=mean, stddev=stddev, sample_count=50
    )
    assert is_anomaly is False


def test_negative_z_score_beyond_threshold_is_flagged() -> None:
    z_score, is_anomaly = _z_score_and_flag(
        value=0.0, mean=20.0, stddev=2.0, sample_count=50
    )
    assert z_score == pytest.approx(-10.0)
    assert is_anomaly is True


def test_below_minimum_sample_count_never_flags_regardless_of_value() -> None:
    z_score, is_anomaly = _z_score_and_flag(
        value=1000.0, mean=20.0, stddev=1.0, sample_count=MIN_SAMPLE_COUNT - 1
    )
    assert z_score is None
    assert is_anomaly is False


def test_zero_stddev_never_flags_and_never_divides_by_zero() -> None:
    """A perfectly flat history (stddev=0) must not raise ZeroDivisionError and must
    not be treated as every deviation being infinitely anomalous."""
    z_score, is_anomaly = _z_score_and_flag(
        value=999.0, mean=20.0, stddev=0.0, sample_count=50
    )
    assert z_score is None
    assert is_anomaly is False


def test_none_mean_or_stddev_never_flags() -> None:
    z_score, is_anomaly = _z_score_and_flag(
        value=999.0, mean=None, stddev=None, sample_count=50
    )
    assert z_score is None
    assert is_anomaly is False
