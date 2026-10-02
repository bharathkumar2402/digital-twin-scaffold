"""FacilityTwinState schema for the 5-agent LangGraph pipeline (PROJECT_PLAN.md §4.5).

Every agent's return value is checked against a Pydantic schema before it is written
into `FacilityTwinState` or persisted to the database (Rule 1).
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

MAX_GRAPH_ITERATIONS: int = 10


class FacilityTwinState(TypedDict):
    """Execution state passed through the 5-agent LangGraph pipeline.

    Conforms to PROJECT_PLAN.md §4.5 with immutable hop updates, reducer-managed
    validation error telemetry, loop guards, and human escalation latching.
    """

    tenant_id: str
    trigger: Literal["scheduled", "user_query", "alert"]
    facility_id: str
    user_query: str | None
    asset_graph: dict[str, Any] | None  # NetworkX serialized
    risk_scores: list[dict[str, Any]] | None
    maintenance_schedule: list[dict[str, Any]] | None
    inventory_gaps: list[dict[str, Any]] | None
    dispatch_routes: list[dict[str, Any]] | None
    simulation_result: dict[str, Any] | None
    decision_report: str | None
    confidence: float | None
    validation_errors: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]
    iteration_count: int
    halted_for_escalation: bool
    escalation_reason: str | None


def create_initial_state(
    tenant_id: str,
    facility_id: str,
    trigger: Literal["scheduled", "user_query", "alert"] = "scheduled",
    user_query: str | None = None,
) -> FacilityTwinState:
    """Helper factory for initializing a fresh, clean FacilityTwinState."""
    return {
        "tenant_id": tenant_id,
        "facility_id": facility_id,
        "trigger": trigger,
        "user_query": user_query,
        "asset_graph": None,
        "risk_scores": None,
        "maintenance_schedule": None,
        "inventory_gaps": None,
        "dispatch_routes": None,
        "simulation_result": None,
        "decision_report": None,
        "confidence": None,
        "validation_errors": [],
        "errors": [],
        "iteration_count": 0,
        "halted_for_escalation": False,
        "escalation_reason": None,
    }
