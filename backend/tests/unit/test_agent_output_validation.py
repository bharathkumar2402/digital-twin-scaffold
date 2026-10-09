"""Adversarial and failure-case tests for the agent output validation layer (PROJECT_PLAN.md §4.4).

Verifies:
1. All 5 agent output schemas strictly reject malformed, out-of-bounds, or hallucinated payloads.
2. The retry-then-escalate harness handles transient errors, injects error feedback into context,
   and halts execution with AgentEscalationRequired on persistent failure, never allowing invalid
   data into state or DB.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Any

import pytest

from app.agents.validation import (
    AgentEscalationRequired,
    AgentValidationError,
    execute_agent_with_retry,
    validate_agent_output,
)
from app.schemas.agent_outputs import (
    MaintenanceInventoryOutput,
    PlannerOutput,
    RiskAssessmentOutput,
    RouteOptimizationOutput,
    SimulationDecisionOutput,
)

# ---------------------------------------------------------------------------
# Test Fixtures & Valid Baseline Payloads
# ---------------------------------------------------------------------------

@pytest.fixture
def valid_planner_payload() -> dict[str, Any]:
    return {
        "plan_id": str(uuid.uuid4()),
        "goal": "Optimize Sector B maintenance following vibration anomaly",
        "target_facility_id": str(uuid.uuid4()),
        "active_agents": ["risk_assessment", "maintenance_inventory", "route_optimization"],
        "tasks": [
            {
                "task_id": "task_1",
                "agent": "risk_assessment",
                "action": "Score failure risk for Sector B pumps",
                "priority": 1,
                "dependencies": [],
                "target_asset_ids": [str(uuid.uuid4())],
            }
        ],
        "asset_graph_summary": {
            "total_nodes": 12,
            "total_edges": 8,
            "root_asset_ids": [str(uuid.uuid4())],
            "critical_path_asset_ids": [str(uuid.uuid4())],
        },
        "reasoning": "High vibration on pump 7 requires urgent risk analysis and dispatch",
        "confidence": 0.95,
    }


@pytest.fixture
def valid_risk_payload() -> dict[str, Any]:
    asset_id = str(uuid.uuid4())
    return {
        "assessment_id": str(uuid.uuid4()),
        "facility_id": str(uuid.uuid4()),
        "model_version": "20261001-abc12345",
        "assessed_at": datetime.now().isoformat(),
        "ranked_assets": [
            {
                "asset_id": asset_id,
                "risk_score": 85.5,
                "urgency_rank": 1,
                "risk_tier": "critical",
                "predicted_failure_mode": "HDF",
                "primary_risk_factors": ["High process temperature", "Elevated torque"],
                "telemetry_anomaly_count_30d": 4,
                "recommended_action": "Emergency cooling inspection",
            }
        ],
        "high_risk_count": 1,
        "medium_risk_count": 0,
        "low_risk_count": 0,
        "executive_summary": "Pump 7 exhibits severe heat dissipation failure indicators",
        "confidence": 0.92,
    }


@pytest.fixture
def valid_maintenance_payload() -> dict[str, Any]:
    asset_id = str(uuid.uuid4())
    return {
        "schedule_id": str(uuid.uuid4()),
        "facility_id": str(uuid.uuid4()),
        "scheduled_items": [
            {
                "item_id": "sched_1",
                "asset_id": asset_id,
                "scheduled_date": str(date.today()),
                "priority": "critical",
                "estimated_duration_hours": 3.5,
                "required_technician_skills": ["mechanical", "hydraulics"],
                "required_parts": [
                    {"part_id": "seal-01", "part_name": "High-Pressure Seal", "quantity": 2}
                ],
            }
        ],
        "inventory_shortages": [
            {
                "part_id": "seal-01",
                "part_name": "High-Pressure Seal",
                "needed_quantity": 2,
                "available_quantity": 0,
                "shortage_count": 2,
                "lead_time_days": 3,
                "impacted_asset_ids": [asset_id],
            }
        ],
        "drafted_purchase_orders": [
            {
                "po_id": "po_101",
                "part_id": "seal-01",
                "order_quantity": 5,
                "estimated_cost_usd": 250.0,
                "vendor": "Industrial Seals Corp",
            }
        ],
        "total_tasks_scheduled": 1,
        "critical_shortage_count": 1,
        "schedule_summary": "1 critical overhaul scheduled; parts on 3-day lead time",
        "confidence": 0.88,
    }


@pytest.fixture
def valid_route_payload() -> dict[str, Any]:
    asset_id = str(uuid.uuid4())
    return {
        "optimization_id": str(uuid.uuid4()),
        "facility_id": str(uuid.uuid4()),
        "solver_status": "OPTIMAL",
        "solver_duration_seconds": 1.25,
        "technician_routes": [
            {
                "technician_id": "tech_1",
                "technician_name": "Alice Johnson",
                "assigned_stops": [
                    {
                        "stop_number": 1,
                        "asset_id": asset_id,
                        "task_id": "sched_1",
                        "estimated_arrival_minutes": 15.0,
                        "service_duration_minutes": 90.0,
                        "travel_time_from_previous_minutes": 15.0,
                    }
                ],
                "total_travel_minutes": 30.0,
                "total_service_minutes": 90.0,
                "total_route_duration_minutes": 120.0,
            }
        ],
        "unassigned_task_ids": [],
        "total_distance_meters": 450.0,
        "confidence": 0.94,
    }


@pytest.fixture
def valid_simulation_payload() -> dict[str, Any]:
    root_id = str(uuid.uuid4())
    child_id = str(uuid.uuid4())
    return {
        "decision_id": str(uuid.uuid4()),
        "facility_id": str(uuid.uuid4()),
        "scenario_trigger": "user_query",
        "cascade_impact": {
            "root_cause_asset_id": root_id,
            "directly_affected_asset_ids": [child_id],
            "downstream_shutoff_asset_ids": [child_id],
            "total_affected_assets": 2,
            "critical_subsystems_interrupted": ["Sector B Secondary Cooling"],
            "estimated_downtime_hours": 6.0,
            "cascade_depth": 2,
        },
        "executive_summary": "Shutdown of main pump causes cascade failure in Sector B cooling",
        "confidence_score": 0.91,
        "human_escalation_required": True,
        "escalation_reason": "Cascade outage affects primary cooling with >$10k/hr downtime cost",
        "recommended_interventions": ["Bypass to redundant chiller before taking pump offline"],
    }


# ---------------------------------------------------------------------------
# 1. Adversarial Schema Boundary Tests (Reject Malformed / Hallucinated Outputs)
# ---------------------------------------------------------------------------

def test_planner_output_validates_successfully(valid_planner_payload: dict[str, Any]) -> None:
    res = validate_agent_output(PlannerOutput, valid_planner_payload)
    assert isinstance(res, PlannerOutput)
    assert res.confidence == 0.95
    assert len(res.tasks) == 1


def test_planner_output_rejects_confidence_out_of_bounds(
    valid_planner_payload: dict[str, Any],
) -> None:
    """Adversarial: confidence > 1.0 or < 0.0 must be rejected."""
    bad_payload = dict(valid_planner_payload, confidence=1.5)
    with pytest.raises(AgentValidationError) as exc_info:
        validate_agent_output(PlannerOutput, bad_payload)
    assert "confidence" in exc_info.value.formatted_error


def test_planner_output_rejects_hallucinated_extra_keys(
    valid_planner_payload: dict[str, Any],
) -> None:
    """Adversarial: extra='forbid' protects against LLM inventing arbitrary top-level fields."""
    bad_payload = dict(valid_planner_payload, rogue_hallucination="invented field")
    with pytest.raises(AgentValidationError) as exc_info:
        validate_agent_output(PlannerOutput, bad_payload)
    assert "extra_forbidden" in str(exc_info.value.errors)


def test_risk_assessment_output_rejects_risk_score_above_100(
    valid_risk_payload: dict[str, Any],
) -> None:
    """Adversarial: risk_score must be bounded in [0.0, 100.0]."""
    bad_payload = dict(valid_risk_payload)
    bad_payload["ranked_assets"] = [
        dict(valid_risk_payload["ranked_assets"][0], risk_score=105.0)
    ]
    with pytest.raises(AgentValidationError) as exc_info:
        validate_agent_output(RiskAssessmentOutput, bad_payload)
    assert "risk_score" in exc_info.value.formatted_error


def test_risk_assessment_output_rejects_invalid_uuid(valid_risk_payload: dict[str, Any]) -> None:
    """Adversarial: malformed UUID strings must not pass into state."""
    bad_payload = dict(valid_risk_payload)
    bad_payload["ranked_assets"] = [
        dict(valid_risk_payload["ranked_assets"][0], asset_id="not-a-valid-uuid")
    ]
    with pytest.raises(AgentValidationError) as exc_info:
        validate_agent_output(RiskAssessmentOutput, bad_payload)
    assert "asset_id" in exc_info.value.formatted_error


def test_maintenance_inventory_rejects_negative_shortage_or_lead_time(
    valid_maintenance_payload: dict[str, Any],
) -> None:
    """Adversarial: quantities, durations, and shortages must respect non-negative bounds."""
    bad_payload = dict(valid_maintenance_payload)
    bad_payload["inventory_shortages"] = [
        dict(valid_maintenance_payload["inventory_shortages"][0], shortage_count=-5)
    ]
    with pytest.raises(AgentValidationError) as exc_info:
        validate_agent_output(MaintenanceInventoryOutput, bad_payload)
    assert "shortage_count" in exc_info.value.formatted_error


def test_route_optimization_rejects_excessive_solver_duration(
    valid_route_payload: dict[str, Any],
) -> None:
    """Adversarial: solver duration exceeding 10.0s violates OR-Tools 5s SLA."""
    bad_payload = dict(valid_route_payload, solver_duration_seconds=45.0)
    with pytest.raises(AgentValidationError) as exc_info:
        validate_agent_output(RouteOptimizationOutput, bad_payload)
    assert "solver_duration_seconds" in exc_info.value.formatted_error


def test_simulation_decision_rejects_missing_required_fields(
    valid_simulation_payload: dict[str, Any],
) -> None:
    """Adversarial: missing essential fields like human_escalation_required."""
    bad_payload = dict(valid_simulation_payload)
    del bad_payload["human_escalation_required"]
    with pytest.raises(AgentValidationError) as exc_info:
        validate_agent_output(SimulationDecisionOutput, bad_payload)
    assert "human_escalation_required" in exc_info.value.formatted_error


def test_validate_agent_output_rejects_malformed_json_string() -> None:
    """Adversarial: raw LLM string that is not valid JSON."""
    raw_str = "{ 'unclosed_json: true "
    with pytest.raises(AgentValidationError) as exc_info:
        validate_agent_output(PlannerOutput, raw_str)
    assert "not valid JSON" in exc_info.value.formatted_error


def test_validate_agent_output_accepts_valid_json_string(
    valid_planner_payload: dict[str, Any],
) -> None:
    """Valid JSON strings are parsed and validated seamlessly."""
    raw_json = json.dumps(valid_planner_payload)
    res = validate_agent_output(PlannerOutput, raw_json)
    assert isinstance(res, PlannerOutput)
    assert res.plan_id == uuid.UUID(valid_planner_payload["plan_id"])


# ---------------------------------------------------------------------------
# 2. Retry-then-Escalate Engine Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_execute_agent_with_retry_succeeds_on_first_attempt(
    valid_planner_payload: dict[str, Any],
) -> None:
    """Happy path: agent returns valid payload immediately."""
    async def mock_agent(ctx: dict[str, Any]) -> dict[str, Any]:
        return valid_planner_payload

    result = await execute_agent_with_retry("planner", PlannerOutput, mock_agent, {})
    assert result.attempts == 1
    assert result.recovered_on_retry is False
    assert result.retry_error_message is None
    assert result.output.confidence == 0.95


@pytest.mark.anyio
async def test_execute_agent_with_retry_recovers_after_transient_failure(
    valid_risk_payload: dict[str, Any],
) -> None:
    """Transient failure: attempt 1 fails validation; attempt 2 receives error feedback
    and returns a corrected valid response."""
    calls = 0

    async def mock_agent(ctx: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            # Attempt 1 returns string for risk_score instead of float (hallucination)
            bad = dict(valid_risk_payload)
            bad["ranked_assets"] = [
                dict(valid_risk_payload["ranked_assets"][0], risk_score="extremely_dangerous")
            ]
            return bad
        # Attempt 2 receives feedback and returns corrected payload
        assert "last_validation_error" in ctx
        assert "risk_score" in ctx["last_validation_error"]
        return valid_risk_payload

    result = await execute_agent_with_retry("risk_assessment", RiskAssessmentOutput, mock_agent, {})
    assert calls == 2
    assert result.attempts == 2
    assert result.recovered_on_retry is True
    assert result.retry_error_message is not None
    assert "risk_score" in result.retry_error_message
    assert result.output.ranked_assets[0].risk_score == 85.5


@pytest.mark.anyio
async def test_execute_agent_with_retry_halts_and_escalates_on_persistent_failure(
    valid_simulation_payload: dict[str, Any],
) -> None:
    """Persistent failure: both attempts fail validation.
    Verifies that AgentEscalationRequired is raised, execution halts, and no invalid
    output is returned to the state machine."""
    calls = 0

    async def persistent_bad_agent(ctx: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        # Returns invalid confidence (> 1.0) on both attempts
        return dict(valid_simulation_payload, confidence_score=4.2)

    with pytest.raises(AgentEscalationRequired) as exc_info:
        await execute_agent_with_retry(
            "simulation_decision",
            SimulationDecisionOutput,
            persistent_bad_agent,
            {"query": "simulate sector B shutdown"},
        )

    assert calls == 2
    exc = exc_info.value
    assert exc.agent_name == "simulation_decision"
    assert exc.schema_name == "SimulationDecisionOutput"
    assert len(exc.validation_errors) == 2
    assert "confidence_score" in exc.validation_errors[0]
    assert "confidence_score" in exc.validation_errors[1]


@pytest.mark.anyio
async def test_execute_agent_with_retry_zero_retries_escalates_immediately() -> None:
    """Zero retries: immediately raises AgentEscalationRequired on first failure."""
    async def bad_agent(ctx: dict[str, Any]) -> dict[str, Any]:
        return {"invalid": "payload"}

    with pytest.raises(AgentEscalationRequired) as exc_info:
        await execute_agent_with_retry(
            "planner",
            PlannerOutput,
            bad_agent,
            {},
            max_retries=0,
        )

    assert len(exc_info.value.validation_errors) == 1
