"""Unit tests for RiskScoreResult (issue 3.3) - the Pydantic validation gate every
computed score must pass before it's ever written to `risk_scores`, per root
CLAUDE.md's "every agent/ML output is validated before it touches the DB" rule.
"""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.ml.risk_score import RiskScoreResult


def _base_kwargs() -> dict:
    return {
        "asset_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "facility_id": uuid.uuid4(),
        "model_version": "20260101T000000Z-abcd1234",
        "factors": {"asset_age_days": 100},
        "computed_at": datetime.now(UTC),
    }


def test_valid_score_in_range_is_accepted() -> None:
    result = RiskScoreResult(score=42.5, **_base_kwargs())
    assert result.score == 42.5


@pytest.mark.parametrize("boundary_score", [0.0, 100.0])
def test_boundary_scores_are_accepted(boundary_score: float) -> None:
    result = RiskScoreResult(score=boundary_score, **_base_kwargs())
    assert result.score == boundary_score


@pytest.mark.parametrize("bad_score", [-0.01, 100.01, -50.0, 1000.0])
def test_out_of_range_score_is_rejected(bad_score: float) -> None:
    """A scaling/clamping bug upstream should fail loudly here, not silently write an
    out-of-range value that only the DB's CHECK constraint would otherwise catch."""
    with pytest.raises(ValidationError):
        RiskScoreResult(score=bad_score, **_base_kwargs())


def test_result_is_immutable() -> None:
    result = RiskScoreResult(score=10.0, **_base_kwargs())
    with pytest.raises(ValidationError):
        result.score = 20.0  # type: ignore[misc]
