"""Unit tests for Phase 4 Task 4: Agent 2 — Risk Assessment (PROJECT_PLAN.md §4.3, Issue #26).

Asserts on:
1. Urgency prioritization and rank assignment across scored assets.
2. Risk tier classification boundaries (critical >=67, high 50-66, medium 34-49, low <34).
3. Telemetry failure mode diagnosis (HDF, PWF, OSF, TWF) and explanatory factor synthesis.
4. Actionable operational recommendations aligned with urgency tier.
5. Strict Pydantic schema validation contract compliance for RiskAssessmentOutput.
6. Execution of the Risk Assessment node within the compiled LangGraph pipeline.
"""

from __future__ import annotations

import uuid

import pytest
from langchain_core.runnables import RunnableConfig

from app.agents.graph import run_facility_twin_pipeline
from app.agents.risk_assessment import (
    prioritize_and_explain_risks,
    real_risk_assessment_callable,
    risk_assessment_node,
)
from app.agents.state import create_initial_state
from app.agents.validation import validate_agent_output
from app.schemas.agent_outputs.risk_assessment import RiskAssessmentOutput


@pytest.fixture
def sample_facility_id() -> uuid.UUID:
    return uuid.uuid4()


# ============================================================================
# 1. Prioritization & Tier Classification Tests
# ============================================================================


def test_risk_scoring_tool_prioritization_and_urgency_ranking(
    sample_facility_id: uuid.UUID,
) -> None:
    """Verifies that assets are sorted strictly in descending order of risk_score
    and assigned 1-indexed urgency_rank values."""
    id_low = uuid.uuid4()
    id_critical = uuid.uuid4()
    id_high = uuid.uuid4()

    raw_scores = [
        {"asset_id": id_low, "risk_score": 18.5, "factors": {}},
        {
            "asset_id": id_critical,
            "risk_score": 88.2,
            "factors": {"air_temperature_30d_anomaly_count": 3},
        },
        {"asset_id": id_high, "risk_score": 58.0, "factors": {"torque_30d_latest_value": 65.0}},
    ]

    output = prioritize_and_explain_risks(
        facility_id=sample_facility_id,
        raw_scores=raw_scores,
    )

    assert isinstance(output, RiskAssessmentOutput)
    assert len(output.ranked_assets) == 3

    # Order must be Critical -> High -> Low
    assert output.ranked_assets[0].asset_id == id_critical
    assert output.ranked_assets[0].urgency_rank == 1
    assert output.ranked_assets[0].risk_score == 88.2
    assert output.ranked_assets[0].risk_tier == "critical"

    assert output.ranked_assets[1].asset_id == id_high
    assert output.ranked_assets[1].urgency_rank == 2
    assert output.ranked_assets[1].risk_score == 58.0
    assert output.ranked_assets[1].risk_tier == "high"

    assert output.ranked_assets[2].asset_id == id_low
    assert output.ranked_assets[2].urgency_rank == 3
    assert output.ranked_assets[2].risk_score == 18.5
    assert output.ranked_assets[2].risk_tier == "low"

    assert output.high_risk_count == 1
    assert output.medium_risk_count == 1  # 58.0 is 34<=score<67
    assert output.low_risk_count == 1


def test_risk_tier_classification_boundaries(
    sample_facility_id: uuid.UUID,
) -> None:
    """Verifies precise tier classification across numeric boundary thresholds."""
    boundary_records = [
        {"asset_id": uuid.uuid4(), "risk_score": 67.0},  # critical
        {"asset_id": uuid.uuid4(), "risk_score": 66.9},  # high
        {"asset_id": uuid.uuid4(), "risk_score": 50.0},  # high
        {"asset_id": uuid.uuid4(), "risk_score": 49.9},  # medium
        {"asset_id": uuid.uuid4(), "risk_score": 34.0},  # medium
        {"asset_id": uuid.uuid4(), "risk_score": 33.9},  # low
        {"asset_id": uuid.uuid4(), "risk_score": 0.0},   # low
    ]

    output = prioritize_and_explain_risks(
        facility_id=sample_facility_id,
        raw_scores=boundary_records,
    )

    tiers = [a.risk_tier for a in output.ranked_assets]
    assert tiers == ["critical", "high", "high", "medium", "medium", "low", "low"]
    assert output.high_risk_count == 1       # >= 67
    assert output.medium_risk_count == 4     # 34 <= score < 67 (66.9, 50.0, 49.9, 34.0)
    assert output.low_risk_count == 2        # < 34 (33.9, 0.0)


# ============================================================================
# 2. Failure Mode Diagnosis & Explanation Tests
# ============================================================================


def test_failure_mode_diagnosis_from_telemetry_factors(
    sample_facility_id: uuid.UUID,
) -> None:
    """Verifies empirical diagnosis of AI4I failure modes from sensor signals."""
    records = [
        # HDF: thermal dissipation deficit (temp delta < 8.6K)
        {
            "asset_id": uuid.uuid4(),
            "risk_score": 75.0,
            "factors": {"temperature_difference_30d_latest_value": 7.4},
        },
        # PWF: abnormal power envelope
        {
            "asset_id": uuid.uuid4(),
            "risk_score": 72.0,
            "factors": {
                "temperature_difference_30d_latest_value": 11.0,
                "rotational_speed_30d_latest_value": 1200.0,
                "torque_30d_latest_value": 2.0,  # 2400W < 3500W limit
            },
        },
        # TWF: excessive tool wear
        {
            "asset_id": uuid.uuid4(),
            "risk_score": 68.0,
            "factors": {
                "temperature_difference_30d_latest_value": 11.0,
                "rotational_speed_30d_latest_value": 1500.0,
                "torque_30d_latest_value": 40.0,
                "tool_wear_30d_anomaly_count": 3,
            },
        },
        # OSF: excessive mechanical torque stress
        {
            "asset_id": uuid.uuid4(),
            "risk_score": 65.0,
            "factors": {
                "temperature_difference_30d_latest_value": 11.0,
                "rotational_speed_30d_latest_value": 1500.0,
                "torque_30d_latest_value": 68.0,  # > 60Nm
            },
        },
    ]

    output = prioritize_and_explain_risks(
        facility_id=sample_facility_id,
        raw_scores=records,
    )

    modes = [a.predicted_failure_mode for a in output.ranked_assets]
    assert modes == ["HDF", "PWF", "TWF", "OSF"]

    # Explanatory factor text check
    assert any("Heat dissipation" in f for f in output.ranked_assets[0].primary_risk_factors)
    assert any("power" in f.lower() for f in output.ranked_assets[1].primary_risk_factors)
    assert any("Tool wear" in f for f in output.ranked_assets[2].primary_risk_factors)
    assert any("torque" in f.lower() for f in output.ranked_assets[3].primary_risk_factors)


def test_recommended_actions_per_tier(
    sample_facility_id: uuid.UUID,
) -> None:
    """Verifies that recommended actions reflect the urgency of each risk tier."""
    records = [
        {"asset_id": uuid.uuid4(), "risk_score": 85.0},  # critical
        {"asset_id": uuid.uuid4(), "risk_score": 55.0},  # high
        {"asset_id": uuid.uuid4(), "risk_score": 40.0},  # medium
        {"asset_id": uuid.uuid4(), "risk_score": 15.0},  # low
    ]

    output = prioritize_and_explain_risks(facility_id=sample_facility_id, raw_scores=records)

    assert "Immediate" in output.ranked_assets[0].recommended_action
    assert "Schedule prioritized" in output.ranked_assets[1].recommended_action
    assert "Monitor telemetry" in output.ranked_assets[2].recommended_action
    assert "Normal operational" in output.ranked_assets[3].recommended_action


# ============================================================================
# 3. Schema Validation & Node Execution Tests
# ============================================================================


def test_risk_assessment_output_satisfies_pydantic_schema(
    sample_facility_id: uuid.UUID,
) -> None:
    """Verifies that prioritize_and_explain_risks strictly conforms to RiskAssessmentOutput."""
    records = [
        {
            "asset_id": uuid.uuid4(),
            "risk_score": 79.2,
            "factors": {"air_temperature_30d_anomaly_count": 2},
        },
        {"asset_id": uuid.uuid4(), "risk_score": 22.0, "factors": {}},
    ]

    output = prioritize_and_explain_risks(facility_id=sample_facility_id, raw_scores=records)
    raw_dict = output.model_dump(mode="json")

    validated = validate_agent_output(RiskAssessmentOutput, raw_dict)
    assert validated.assessment_id == output.assessment_id
    assert validated.confidence == 0.94
    assert len(validated.ranked_assets) == 2


def test_risk_assessment_empty_facility(
    sample_facility_id: uuid.UUID,
) -> None:
    """Verifies graceful handling when no assets are provided."""
    output = prioritize_and_explain_risks(facility_id=sample_facility_id, raw_scores=[])

    assert output.ranked_assets == []
    assert output.high_risk_count == 0
    assert output.medium_risk_count == 0
    assert output.low_risk_count == 0
    assert "No facility assets" in output.executive_summary
    assert output.confidence >= 0.90


@pytest.mark.anyio
async def test_risk_assessment_node_direct_execution() -> None:
    """Verifies direct execution of risk_assessment_node with custom scored assets in config."""
    asset_id = uuid.uuid4()
    initial_state = create_initial_state(
        tenant_id=str(uuid.uuid4()),
        facility_id=str(uuid.uuid4()),
    )

    config: RunnableConfig = {
        "configurable": {
            "scored_assets": [
                {
                    "asset_id": asset_id,
                    "risk_score": 82.5,
                    "factors": {"air_temperature_30d_anomaly_count": 4},
                }
            ],
            "risk_assessment_callable": real_risk_assessment_callable,
        }
    }

    delta = await risk_assessment_node(initial_state, config=config)

    assert "risk_scores" in delta
    assert len(delta["risk_scores"]) == 1
    assert delta["risk_scores"][0]["asset_id"] == str(asset_id)
    assert delta["risk_scores"][0]["risk_score"] == 82.5
    assert delta["risk_scores"][0]["risk_tier"] == "critical"
    assert delta["iteration_count"] == 1


@pytest.mark.anyio
async def test_risk_assessment_node_within_compiled_pipeline() -> None:
    """Verifies that the compiled LangGraph pipeline executes the Planner and Risk Assessment
    agents in sequence with realistic asset prioritization."""
    pump_id = uuid.uuid4()
    boiler_id = uuid.uuid4()

    assets = [
        {"id": pump_id, "name": "Pump Alpha", "status": "operational", "risk_score": 84.0},
        {"id": boiler_id, "name": "Boiler Beta", "status": "operational", "risk_score": 38.0},
    ]

    initial_state = create_initial_state(
        tenant_id=str(uuid.uuid4()),
        facility_id=str(uuid.uuid4()),
        trigger="scheduled",
    )

    config: RunnableConfig = {
        "configurable": {
            "assets": assets,
            "dependencies": [],
            "risk_assessment_callable": real_risk_assessment_callable,
        }
    }

    final_state = await run_facility_twin_pipeline(initial_state, config=config)

    assert final_state["halted_for_escalation"] is False
    assert final_state["risk_scores"] is not None
    assert len(final_state["risk_scores"]) == 2
    # Verify ranked order: Pump Alpha (84.0) should be rank 1
    assert final_state["risk_scores"][0]["asset_id"] == str(pump_id)
    assert final_state["risk_scores"][0]["urgency_rank"] == 1
    assert final_state["risk_scores"][0]["risk_tier"] == "critical"
    assert final_state["iteration_count"] == 5
