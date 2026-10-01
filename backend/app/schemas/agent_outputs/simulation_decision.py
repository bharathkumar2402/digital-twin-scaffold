"""Pydantic output schema for Agent 5 — Simulation & Decision Agent (PROJECT_PLAN.md §4.3)."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CascadeImpactReport(BaseModel):
    """NetworkX dependency graph shutdown propagation analysis."""

    model_config = ConfigDict(extra="forbid")

    root_cause_asset_id: uuid.UUID = Field(
        ..., description="Root asset undergoing hypothetical or actual failure"
    )
    directly_affected_asset_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Assets directly linked as downstream children in dependency graph",
    )
    downstream_shutoff_asset_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="All recursively affected assets in the shutdown cascade tree",
    )
    total_affected_assets: int = Field(
        ...,
        ge=0,
        description="Count of all operational nodes impacted by the failure",
    )
    critical_subsystems_interrupted: list[str] = Field(
        default_factory=list,
        description="Key production zones or processes stalled (e.g. Sector B cooling)",
    )
    estimated_downtime_hours: float = Field(
        ...,
        ge=0.0,
        description="Projected duration of facility downtime in hours",
    )
    cascade_depth: int = Field(
        ...,
        ge=0,
        description="Maximum topological depth of the cascade graph propagation",
    )


class SimulationDecisionOutput(BaseModel):
    """Pydantic validation schema for the Simulation & Decision Agent's return value."""

    model_config = ConfigDict(extra="forbid")

    decision_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        description="Unique decision run identifier",
    )
    facility_id: uuid.UUID = Field(..., description="Facility target for decision synthesis")
    scenario_trigger: Literal["user_query", "scheduled", "anomaly_alert"] = Field(
        ...,
        description="Trigger mechanism initiating the multi-agent pipeline run",
    )
    cascade_impact: CascadeImpactReport = Field(
        ...,
        description="Graph-based failure cascade propagation analysis",
    )
    executive_summary: str = Field(
        ...,
        min_length=10,
        description="Final natural-language synthesis covering risk, maintenance, and impact",
    )
    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Overall synthesized confidence score between 0.0 and 1.0",
    )
    human_escalation_required: bool = Field(
        ...,
        description="True if human authorization/intervention is required before acting",
    )
    escalation_reason: str | None = Field(
        default=None,
        description="Explicit rationale required if human_escalation_required is True",
    )
    recommended_interventions: list[str] = Field(
        default_factory=list,
        description="Immediate prioritized mitigation steps for operators",
    )
