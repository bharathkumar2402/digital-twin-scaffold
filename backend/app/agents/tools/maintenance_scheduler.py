"""Constraint-based maintenance scheduling engine for Agent 3.

Schedules maintenance work orders over a 30-day horizon respecting urgency windows,
daily technician labor capacities, and failure-mode domain competencies.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from app.agents.tools.inventory_tool import get_parts_for_failure_mode
from app.schemas.agent_outputs.maintenance_inventory import MaintenanceScheduleItem

# Standard labor hours available per day (2 technicians * 8h shift)
DEFAULT_DAILY_TECH_HOURS: float = 16.0
DEFAULT_PLANNING_HORIZON_DAYS: int = 30

# Domain competencies and estimated labor durations per diagnosed failure mode
FAILURE_MODE_WORK_PROFILES: dict[str, dict[str, Any]] = {
    "HDF": {
        "duration_hours": 3.5,
        "skills": ["mechanical", "thermal", "hvac"],
    },
    "PWF": {
        "duration_hours": 4.0,
        "skills": ["electrical", "power_distribution"],
    },
    "TWF": {
        "duration_hours": 2.5,
        "skills": ["tooling", "mechanical_machining"],
    },
    "OSF": {
        "duration_hours": 3.5,
        "skills": ["mechanical", "structural"],
    },
    "RNF": {
        "duration_hours": 2.0,
        "skills": ["general_inspection", "instrumentation"],
    },
}


def _get_work_profile(failure_mode: str | None) -> tuple[float, list[str]]:
    """Retrieves standard task duration and technician skill requirements."""
    mode = (failure_mode or "RNF").upper()
    profile = FAILURE_MODE_WORK_PROFILES.get(mode, FAILURE_MODE_WORK_PROFILES["RNF"])
    return float(profile["duration_hours"]), list(profile["skills"])


def generate_constraint_schedule(
    scored_assets: list[dict[str, Any]],
    *,
    start_date: date | None = None,
    daily_tech_hours: float = DEFAULT_DAILY_TECH_HOURS,
    planning_horizon_days: int = DEFAULT_PLANNING_HORIZON_DAYS,
) -> list[MaintenanceScheduleItem]:
    """Generates a constraint-respecting 30-day maintenance schedule.

    Constraints enforced:
    1. Urgency Window Constraint:
       - critical (risk_score >= 67): scheduled in Days 1-2 (immediate intervention)
       - high (50 <= score < 67): scheduled in Days 3-5 (urgent overhaul)
       - medium (34 <= score < 50): scheduled in Days 6-14 (planned maintenance)
       - low (score < 34): scheduled in Days 15-30 (routine inspection)
    2. Daily Technician Capacity Constraint:
       - Sum of task durations on any given calendar day cannot exceed `daily_tech_hours`.
       - When a day is saturated, tasks roll forward to the earliest feasible day.
    """
    base_date = start_date or datetime.now(UTC).date()

    if not scored_assets:
        return []

    # Sort assets strictly by descending risk score / ascending urgency rank
    sorted_assets = sorted(
        scored_assets,
        key=lambda item: float(item.get("risk_score", 0.0)),
        reverse=True,
    )

    daily_hours_allocated: dict[date, float] = {}
    scheduled_items: list[MaintenanceScheduleItem] = []

    for rank_idx, asset in enumerate(sorted_assets, start=1):
        asset_id_raw = asset.get("asset_id") or asset.get("id")
        if isinstance(asset_id_raw, uuid.UUID):
            asset_id = asset_id_raw
        elif isinstance(asset_id_raw, str):
            asset_id = uuid.UUID(asset_id_raw)
        else:
            asset_id = uuid.uuid4()

        score = float(asset.get("risk_score", 0.0))
        pred_mode = asset.get("predicted_failure_mode")

        # 1. Determine priority and target scheduling window
        priority: Literal["low", "medium", "high", "critical"]
        if score >= 67.0:
            priority = "critical"
            target_start_offset = 0   # Day 1
            target_max_offset = 1     # Day 2
        elif score >= 50.0:
            priority = "high"
            target_start_offset = 2   # Day 3
            target_max_offset = 4     # Day 5
        elif score >= 34.0:
            priority = "medium"
            target_start_offset = 5   # Day 6
            target_max_offset = 13    # Day 14
        else:
            priority = "low"
            target_start_offset = 14  # Day 15
            target_max_offset = min(29, planning_horizon_days - 1)

        duration_hours, skills = _get_work_profile(pred_mode)
        required_parts = get_parts_for_failure_mode(pred_mode)

        # 2. Constraint solver: find earliest calendar day with remaining capacity
        scheduled_date: date | None = None

        # Pass A: search within target urgency window
        for offset in range(target_start_offset, target_max_offset + 1):
            cand_date = base_date + timedelta(days=offset)
            current_hours = daily_hours_allocated.get(cand_date, 0.0)
            if current_hours + duration_hours <= daily_tech_hours:
                scheduled_date = cand_date
                break

        # Pass B: if target window is full, search forward to earliest available day
        if scheduled_date is None:
            for offset in range(target_max_offset + 1, planning_horizon_days):
                cand_date = base_date + timedelta(days=offset)
                current_hours = daily_hours_allocated.get(cand_date, 0.0)
                if current_hours + duration_hours <= daily_tech_hours:
                    scheduled_date = cand_date
                    break

        # Pass C: if entire remaining horizon is full, allocate to day with minimum load
        if scheduled_date is None:
            all_dates = [base_date + timedelta(days=i) for i in range(planning_horizon_days)]
            scheduled_date = min(all_dates, key=lambda d: daily_hours_allocated.get(d, 0.0))

        # Update allocation tracker
        daily_hours_allocated[scheduled_date] = (
            daily_hours_allocated.get(scheduled_date, 0.0) + duration_hours
        )

        item_id = f"maint_{rank_idx:02d}_{asset_id.hex[:6]}"

        scheduled_items.append(
            MaintenanceScheduleItem(
                item_id=item_id,
                asset_id=asset_id,
                scheduled_date=scheduled_date,
                priority=priority,
                estimated_duration_hours=duration_hours,
                required_technician_skills=skills,
                required_parts=required_parts,
            )
        )

    # Sort final schedule chronologically by scheduled_date, then priority
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    scheduled_items.sort(
        key=lambda item: (item.scheduled_date, priority_order.get(item.priority, 9))
    )

    return scheduled_items
