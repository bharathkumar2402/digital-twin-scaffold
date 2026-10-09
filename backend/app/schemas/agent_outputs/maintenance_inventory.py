"""Pydantic output schema for Agent 3 — Maintenance & Inventory Planning Agent
(PROJECT_PLAN.md §4.3).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RequiredPartItem(BaseModel):
    """A spare part required for an asset maintenance procedure."""

    model_config = ConfigDict(extra="forbid")

    part_id: str = Field(..., min_length=1, description="SKU or part identifier")
    part_name: str = Field(..., min_length=1, description="Descriptive part name")
    quantity: int = Field(..., ge=1, description="Quantity required for the task")


class MaintenanceScheduleItem(BaseModel):
    """An individual maintenance work order scheduled for an asset."""

    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(..., description="Unique schedule item identifier, e.g. sched_1")
    asset_id: uuid.UUID = Field(..., description="Asset to be serviced")
    scheduled_date: date = Field(..., description="Target service date within the 30-day window")
    priority: Literal["low", "medium", "high", "critical"] = Field(
        ...,
        description="Scheduling urgency level",
    )
    estimated_duration_hours: float = Field(
        ...,
        gt=0.0,
        le=48.0,
        description="Estimated duration required for the task in hours",
    )
    required_technician_skills: list[str] = Field(
        default_factory=list,
        description="Skill competencies required (e.g. electrical, mechanical, hvac)",
    )
    required_parts: list[RequiredPartItem] = Field(
        default_factory=list,
        description="Spare parts necessary to complete this task",
    )


class InventoryShortage(BaseModel):
    """A detected gap between scheduled spare parts demand and on-hand stock."""

    model_config = ConfigDict(extra="forbid")

    part_id: str = Field(..., min_length=1, description="SKU of the deficient part")
    part_name: str = Field(..., min_length=1, description="Part description")
    needed_quantity: int = Field(..., ge=1, description="Total quantity required by schedule")
    available_quantity: int = Field(..., ge=0, description="Current on-hand inventory count")
    shortage_count: int = Field(..., ge=1, description="Deficit (needed - available)")
    lead_time_days: int = Field(..., ge=0, description="Supplier lead time in days")
    impacted_asset_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Assets whose scheduled maintenance depends on this part",
    )


class DraftPurchaseOrder(BaseModel):
    """A drafted procurement order to replenish critical inventory shortages."""

    model_config = ConfigDict(extra="forbid")

    po_id: str = Field(..., description="Draft purchase order identifier, e.g. po_101")
    part_id: str = Field(..., min_length=1, description="Part identifier to order")
    order_quantity: int = Field(..., ge=1, description="Quantity to purchase")
    estimated_cost_usd: float = Field(..., ge=0.0, description="Estimated procurement cost in USD")
    vendor: str | None = Field(default=None, description="Preferred supplier/vendor name")


class MaintenanceInventoryOutput(BaseModel):
    """Pydantic validation schema for the Maintenance & Inventory Planning Agent's return value."""

    model_config = ConfigDict(extra="forbid")

    schedule_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        description="Unique schedule generation identifier",
    )
    facility_id: uuid.UUID = Field(..., description="Facility target for the schedule")
    scheduled_items: list[MaintenanceScheduleItem] = Field(
        default_factory=list,
        description="Ordered list of maintenance tasks for the next 30 days",
    )
    inventory_shortages: list[InventoryShortage] = Field(
        default_factory=list,
        description="Identified parts shortages within the next 14-day window",
    )
    drafted_purchase_orders: list[DraftPurchaseOrder] = Field(
        default_factory=list,
        description="Draft POs generated to resolve detected parts shortages",
    )
    total_tasks_scheduled: int = Field(
        ...,
        ge=0,
        description="Total maintenance tasks scheduled",
    )
    critical_shortage_count: int = Field(
        ...,
        ge=0,
        description="Count of high/critical priority tasks currently blocked by parts",
    )
    schedule_summary: str = Field(
        ...,
        min_length=5,
        description="Summary of schedule feasibility and technician utilization",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Planning confidence score between 0.0 and 1.0",
    )
