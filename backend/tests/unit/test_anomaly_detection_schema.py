"""Unit tests for AnomalyCheckResult (issue 3.4) - the Pydantic validation gate every
live anomaly check result must pass before `POST /telemetry` returns it, per root
CLAUDE.md's "every agent/ML output is validated before it's written or returned" rule.
"""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.ml.anomaly import AnomalyCheckResult


def _base_kwargs() -> dict:
    return {
        "asset_id": uuid.uuid4(),
        "sensor_type": "temperature",
        "value": 42.5,
        "timestamp": datetime.now(UTC),
    }


def test_valid_anomaly_result_is_accepted() -> None:
    result = AnomalyCheckResult(
        **_base_kwargs(),
        rolling_mean=20.0,
        rolling_stddev=2.0,
        sample_count=50,
        z_score=11.25,
        is_anomaly=True,
    )
    assert result.is_anomaly is True
    assert result.z_score == 11.25


def test_insufficient_history_result_with_null_stats_is_accepted() -> None:
    """No baseline yet: mean/stddev/z_score are all None, is_anomaly must be False -
    this must validate cleanly, not be treated as a malformed output."""
    result = AnomalyCheckResult(
        **_base_kwargs(),
        rolling_mean=None,
        rolling_stddev=None,
        sample_count=3,
        z_score=None,
        is_anomaly=False,
    )
    assert result.z_score is None
    assert result.is_anomaly is False


def test_negative_sample_count_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AnomalyCheckResult(
            **_base_kwargs(),
            rolling_mean=None,
            rolling_stddev=None,
            sample_count=-1,
            z_score=None,
            is_anomaly=False,
        )


def test_result_is_immutable() -> None:
    result = AnomalyCheckResult(
        **_base_kwargs(),
        rolling_mean=20.0,
        rolling_stddev=2.0,
        sample_count=50,
        z_score=1.0,
        is_anomaly=False,
    )
    with pytest.raises(ValidationError):
        result.is_anomaly = True  # type: ignore[misc]
