"""Pydantic output schema for Agent 1 — Planner Agent (PROJECT_PLAN.md §4.3)."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PlannerSubTask(BaseModel):
    """A decomposed sub-task assigned to a specific downstream agent."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(..., description="Unique task identifier, e.g. task_1")
    agent: Literal[
        "risk_assessment",
        "maintenance_inventory",
        "route_optimization",
        "simulation_decision",
    ] = Field(..., description="Target agent assigned to execute this sub-task")
    action: str = Field(..., min_length=3, description="Action or query description")
    priority: int = Field(1, ge=1, le=5, description="1=highest priority, 5=lowest")
    dependencies: list[str] = Field(
        default_factory=list,
        description="List of task_ids that must complete before this sub-task runs",
    )
    target_asset_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Assets involved in or targeted by this sub-task",
    )


class AssetGraphSummary(BaseModel):
    """Summary of the facility asset graph topology loaded by the Planner."""

    model_config = ConfigDict(extra="forbid")

    total_nodes: int = Field(..., ge=0, description="Total asset nodes in the facility graph")
    total_edges: int = Field(..., ge=0, description="Total dependency edges between assets")
    root_asset_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Assets with no upstream dependencies (top of cascade tree)",
    )
    critical_path_asset_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Assets with highest degree of downstream dependencies",
    )


class PlannerOutput(BaseModel):
    """Pydantic validation schema for the Planner Agent's return value."""

    model_config = ConfigDict(extra="forbid")

    plan_id: uuid.UUID = Field(default_factory=uuid.uuid4, description="Unique plan identifier")
    goal: str = Field(..., min_length=3, description="High-level user or scheduled objective")
    target_facility_id: uuid.UUID = Field(..., description="Target facility being planned for")
    active_agents: list[
        Literal[
            "risk_assessment",
            "maintenance_inventory",
            "route_optimization",
            "simulation_decision",
        ]
    ] = Field(
        ...,
        min_length=1,
        description="Downstream agents that must be activated for this plan",
    )
    tasks: list[PlannerSubTask] = Field(
        ...,
        min_length=1,
        description="Ordered or DAG list of sub-tasks to execute",
    )
    asset_graph_summary: AssetGraphSummary = Field(
        ...,
        description="Topological summary of the loaded facility asset graph",
    )
    reasoning: str = Field(..., min_length=5, description="Planner chain-of-thought rationale")
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Planner confidence score between 0.0 and 1.0",
    )
