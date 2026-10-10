"""Phase 3b task 3b.7a: RUL flows through the Risk Assessment agent and its tool."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from app.agents.risk_assessment import prioritize_and_explain_risks
from app.agents.tools import risk_scoring_tool
from app.agents.tools.risk_scoring_tool import score_facility_assets_tool
from app.schemas.agent_outputs.risk_assessment import RiskAssessmentOutput, ScoredAsset
from app.schemas.ml.risk_score import RiskScoreResult

FACILITY = uuid.uuid4()


def _record(score: float, rul: float | None, **factors: Any) -> dict[str, Any]:
    return {
        "asset_id": uuid.uuid4(),
        "risk_score": score,
        "rul_cycles": rul,
        "factors": {"rul_model_version": "v-42", **factors} if rul is not None else factors,
    }


def test_rul_is_carried_into_the_validated_agent_output_with_its_model_version() -> None:
    out = prioritize_and_explain_risks(
        facility_id=FACILITY, raw_scores=[_record(80.0, 12.34), _record(20.0, None)]
    )
    RiskAssessmentOutput.model_validate(out.model_dump(mode="json"))  # round-trips the schema
    with_rul = next(a for a in out.ranked_assets if a.rul_cycles is not None)
    without = next(a for a in out.ranked_assets if a.rul_cycles is None)
    assert with_rul.rul_cycles == 12.3
    assert with_rul.rul_model_version == "v-42"
    assert any("remaining useful life: 12.3 cycles" in f for f in with_rul.primary_risk_factors)
    assert without.rul_model_version is None
    assert not any("remaining useful life" in f for f in without.primary_risk_factors)


def test_rul_does_not_change_ranking_or_risk_tier() -> None:
    """RUL is informational in this task: ordering and tiers still come from the risk score."""
    low_rul_low_risk = _record(10.0, 1.0)
    high_rul_high_risk = _record(90.0, 120.0)
    out = prioritize_and_explain_risks(
        facility_id=FACILITY, raw_scores=[low_rul_low_risk, high_rul_high_risk]
    )
    assert [a.risk_tier for a in out.ranked_assets] == ["critical", "low"]
    assert out.ranked_assets[0].rul_cycles == 120.0


def test_agent_output_schema_rejects_invalid_rul() -> None:
    base: dict[str, Any] = {
        "asset_id": uuid.uuid4(),
        "risk_score": 50.0,
        "urgency_rank": 1,
        "risk_tier": "high",
        "recommended_action": "inspect",
    }
    ScoredAsset(**base, rul_cycles=0.0, rul_model_version="v1")
    with pytest.raises(ValidationError):
        ScoredAsset(**base, rul_cycles=-3.0)
    with pytest.raises(ValidationError):
        ScoredAsset(**base, rul_cycles=5.0, rul_model_version="x" * 300)
    with pytest.raises(ValidationError):
        ScoredAsset(**base, hallucinated_field="x")  # type: ignore[call-arg]


def test_risk_score_result_rejects_negative_rul() -> None:
    kwargs: dict[str, Any] = {
        "asset_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "facility_id": FACILITY,
        "score": 40.0,
        "model_version": "m",
        "factors": {},
        "computed_at": datetime.now(UTC),
    }
    assert RiskScoreResult(**kwargs).rul_cycles is None
    assert RiskScoreResult(**kwargs, rul_cycles=17.5).rul_cycles == 17.5
    with pytest.raises(ValidationError):
        RiskScoreResult(**kwargs, rul_cycles=-0.1)


@pytest.mark.asyncio
async def test_tool_passes_rul_through_and_keeps_none_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        SimpleNamespace(
            asset_id=uuid.uuid4(),
            score=61.0,
            rul_cycles=33.5,
            factors_json={"rul_model_version": "v-9"},
            model_version="risk-1",
            computed_at=datetime.now(UTC),
        ),
        SimpleNamespace(
            asset_id=uuid.uuid4(),
            score=12.0,
            rul_cycles=None,
            factors_json={},
            model_version="risk-1",
            computed_at=datetime.now(UTC),
        ),
    ]

    async def fake_score_facility(*args: Any, **kwargs: Any) -> list[Any]:
        return rows

    monkeypatch.setattr(risk_scoring_tool, "score_facility", fake_score_facility)
    results = await score_facility_assets_tool(
        cast(Any, None), cast(Any, None), tenant_id=uuid.uuid4(), facility_id=FACILITY
    )
    assert [r["rul_cycles"] for r in results] == [33.5, None]
    assert results[0]["factors"]["rul_model_version"] == "v-9"
