"""Pydantic output schema for Agent 4 — Route Optimization Agent (PROJECT_PLAN.md §4.3)."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RouteStop(BaseModel):
    """An individual asset stop along a technician's dispatch sequence."""

    model_config = ConfigDict(extra="forbid")

    stop_number: int = Field(..., ge=1, description="Sequential order of this visit (1, 2, ...)")
    asset_id: uuid.UUID = Field(..., description="Target asset to be visited/serviced")
    task_id: str = Field(..., description="Associated maintenance schedule task ID")
    estimated_arrival_minutes: float = Field(
        ...,
        ge=0.0,
        description="Minutes from shift start to arrival at this asset stop",
    )
    service_duration_minutes: float = Field(
        ...,
        gt=0.0,
        description="Expected time spent on-site servicing the asset",
    )
    travel_time_from_previous_minutes: float = Field(
        ...,
        ge=0.0,
        description="Travel time from preceding stop (or depot) in minutes",
    )


class TechnicianRoute(BaseModel):
    """The complete dispatched route assigned to a technician."""

    model_config = ConfigDict(extra="forbid")

    technician_id: str = Field(..., min_length=1, description="Identifier of the technician")
    technician_name: str = Field(..., min_length=1, description="Name of the technician")
    assigned_stops: list[RouteStop] = Field(
        default_factory=list,
        description="Ordered sequence of asset stops",
    )
    total_travel_minutes: float = Field(..., ge=0.0, description="Sum of travel transit times")
    total_service_minutes: float = Field(
        ..., ge=0.0, description="Sum of on-site service durations"
    )
    total_route_duration_minutes: float = Field(
        ...,
        ge=0.0,
        description="Total shift time (travel + service) in minutes",
    )


class RouteOptimizationOutput(BaseModel):
    """Pydantic validation schema for the Route Optimization Agent's return value."""

    model_config = ConfigDict(extra="forbid")

    optimization_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        description="Unique optimization run identifier",
    )
    facility_id: uuid.UUID = Field(..., description="Facility target for dispatch routing")
    solver_status: Literal[
        "OPTIMAL",
        "FEASIBLE",
        "TIME_LIMIT_REACHED",
        "NO_SOLUTION_FOUND",
    ] = Field(..., description="Status returned by the OR-Tools CVRP solver")
    solver_duration_seconds: float = Field(
        ...,
        ge=0.0,
        le=10.0,
        description="Execution duration of the solver (constrained by 5s SLO + overhead)",
    )
    technician_routes: list[TechnicianRoute] = Field(
        default_factory=list,
        description="Dispatched routes for each available technician",
    )
    unassigned_task_ids: list[str] = Field(
        default_factory=list,
        description="Tasks that could not be assigned due to capacity or skill constraints",
    )
    total_distance_meters: float = Field(
        ...,
        ge=0.0,
        description="Total spatial travel distance across all routes in meters",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Routing solution quality score between 0.0 and 1.0",
    )
