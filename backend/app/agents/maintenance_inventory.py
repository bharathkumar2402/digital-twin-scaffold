"""Agent 3 — Maintenance & Inventory Planning Agent LangGraph node (PROJECT_PLAN.md §4.3).

Generates constraint-based maintenance schedules and performs inventory gap analysis.
Validates all outputs against MaintenanceInventoryOutput before updating state.
"""

import logging
import uuid
from datetime import date
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.state import MAX_GRAPH_ITERATIONS, FacilityTwinState
from app.agents.tools.inventory_tool import evaluate_inventory_gaps_tool
from app.agents.tools.maintenance_scheduler import generate_constraint_schedule
from app.agents.validation import AgentEscalationRequired, execute_agent_with_retry
from app.schemas.agent_outputs.maintenance_inventory import MaintenanceInventoryOutput

logger = logging.getLogger("agents.maintenance_inventory")


def plan_maintenance_and_inventory(
    *,
    facility_id: uuid.UUID,
    scored_assets: list[dict[str, Any]],
    start_date: date | None = None,
    daily_tech_hours: float = 16.0,
    custom_on_hand: dict[str, int] | None = None,
) -> MaintenanceInventoryOutput:
    """Generates a constraint-respecting 30-day maintenance schedule and evaluates parts demand."""
    scheduled_items = generate_constraint_schedule(
        scored_assets,
        start_date=start_date,
        daily_tech_hours=daily_tech_hours,
    )

    inventory_shortages, draft_pos = evaluate_inventory_gaps_tool(
        scheduled_items,
        custom_on_hand=custom_on_hand,
    )

    shortage_skus = {s.part_id for s in inventory_shortages}
    critical_shortages = sum(
        1
        for item in scheduled_items
        if item.priority in ("critical", "high")
        and any(part.part_id in shortage_skus for part in item.required_parts)
    )

    total_tasks = len(scheduled_items)
    crit_count = sum(1 for t in scheduled_items if t.priority == "critical")
    high_count = sum(1 for t in scheduled_items if t.priority == "high")

    if total_tasks == 0:
        summary = "No maintenance tasks scheduled: all facility assets operating within low risk."
        confidence = 0.95
    else:
        parts_note = (
            f"{len(inventory_shortages)} inventory shortage(s) identified with "
            f"{critical_shortages} critical task(s) impacted."
            if inventory_shortages
            else "All required spare parts currently on-hand."
        )
        summary = (
            f"Scheduled {total_tasks} maintenance task(s) over 30 days ({crit_count} critical, "
            f"{high_count} high). {parts_note}"
        )
        # Penalize confidence if critical tasks are blocked by inventory shortages
        penalty = min(0.25, critical_shortages * 0.05)
        confidence = round(max(0.70, 0.94 - penalty), 2)

    return MaintenanceInventoryOutput(
        schedule_id=uuid.uuid4(),
        facility_id=facility_id,
        scheduled_items=scheduled_items,
        inventory_shortages=inventory_shortages,
        drafted_purchase_orders=draft_pos,
        total_tasks_scheduled=total_tasks,
        critical_shortage_count=critical_shortages,
        schedule_summary=summary,
        confidence=confidence,
    )


async def real_maintenance_inventory_callable(context: dict[str, Any]) -> dict[str, Any]:
    """Production callable for Agent 3: generates schedule and cross-references inventory."""
    facility_id_raw = context.get("facility_id", str(uuid.uuid4()))
    facility_id = (
        uuid.UUID(facility_id_raw) if isinstance(facility_id_raw, str) else facility_id_raw
    )

    scored_assets = context.get("risk_scores")
    if scored_assets is None:
        # Fallback to single critical asset for skeleton compatibility
        scored_assets = [
            {
                "asset_id": uuid.uuid4(),
                "risk_score": 78.4,
                "urgency_rank": 1,
                "risk_tier": "critical",
                "predicted_failure_mode": "HDF",
            }
        ]

    start_date_val = context.get("start_date")
    start_date: date | None = None
    if isinstance(start_date_val, date):
        start_date = start_date_val
    elif isinstance(start_date_val, str):
        start_date = date.fromisoformat(start_date_val)

    daily_tech_hours = float(context.get("daily_tech_hours", 16.0))
    custom_on_hand = context.get("custom_on_hand")

    output = plan_maintenance_and_inventory(
        facility_id=facility_id,
        scored_assets=scored_assets,
        start_date=start_date,
        daily_tech_hours=daily_tech_hours,
        custom_on_hand=custom_on_hand,
    )

    return output.model_dump(mode="json")


default_maintenance_inventory_callable = real_maintenance_inventory_callable


async def maintenance_inventory_node(
    state: FacilityTwinState,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """LangGraph node executing Maintenance & Inventory Agent with schema validation."""
    if state.get("halted_for_escalation"):
        return {}

    current_iterations = state.get("iteration_count", 0)
    if current_iterations >= MAX_GRAPH_ITERATIONS:
        logger.error(
            "Maintenance inventory node loop guard triggered: %d >= %d",
            current_iterations,
            MAX_GRAPH_ITERATIONS,
        )
        return {
            "halted_for_escalation": True,
            "escalation_reason": "Loop guard exceeded maximum iterations",
            "errors": ["Loop guard exceeded max iterations in maintenance_inventory_node"],
            "iteration_count": current_iterations + 1,
        }

    configurable = (config or {}).get("configurable", {})
    agent_fn = configurable.get(
        "maintenance_inventory_callable", real_maintenance_inventory_callable
    )

    context: dict[str, Any] = {
        "tenant_id": state["tenant_id"],
        "facility_id": state["facility_id"],
        "trigger": state["trigger"],
        "risk_scores": state.get("risk_scores"),
        "asset_graph": state.get("asset_graph"),
        "custom_on_hand": configurable.get("custom_on_hand"),
        "daily_tech_hours": configurable.get("daily_tech_hours", 16.0),
        "start_date": configurable.get("start_date"),
    }

    try:
        result = await execute_agent_with_retry(
            "maintenance_inventory", MaintenanceInventoryOutput, agent_fn, context
        )
        validated: MaintenanceInventoryOutput = result.output

        return {
            "maintenance_schedule": [s.model_dump(mode="json") for s in validated.scheduled_items],
            "inventory_gaps": [g.model_dump(mode="json") for g in validated.inventory_shortages],
            "iteration_count": current_iterations + 1,
        }
    except AgentEscalationRequired as exc:
        logger.error("Maintenance inventory failed validation after retry. Halting for escalation.")
        return {
            "halted_for_escalation": True,
            "escalation_reason": str(exc),
            "validation_errors": list(exc.validation_errors),
            "errors": [f"Maintenance inventory validation escalation: {exc}"],
            "iteration_count": current_iterations + 1,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in maintenance_inventory_node: %s", exc)
        return {
            "halted_for_escalation": True,
            "escalation_reason": f"Unhandled exception in maintenance_inventory_node: {exc}",
            "errors": [f"Maintenance inventory node runtime error: {exc}"],
            "iteration_count": current_iterations + 1,
        }
