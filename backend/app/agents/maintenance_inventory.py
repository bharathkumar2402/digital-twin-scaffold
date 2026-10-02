"""Agent 3 — Maintenance & Inventory Planning Agent LangGraph node (PROJECT_PLAN.md §4.3).

Generates constraint-based maintenance schedules and performs inventory gap analysis.
Validates all outputs against MaintenanceInventoryOutput before updating state.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.state import MAX_GRAPH_ITERATIONS, FacilityTwinState
from app.agents.validation import AgentEscalationRequired, execute_agent_with_retry
from app.schemas.agent_outputs.maintenance_inventory import MaintenanceInventoryOutput

logger = logging.getLogger("agents.maintenance_inventory")


async def default_maintenance_inventory_callable(context: dict[str, Any]) -> dict[str, Any]:
    """Default placeholder for Maintenance & Inventory Agent producing valid output."""
    facility_id_raw = context.get("facility_id", str(uuid.uuid4()))
    facility_id = (
        uuid.UUID(facility_id_raw) if isinstance(facility_id_raw, str) else facility_id_raw
    )
    asset_id = uuid.uuid4()
    today_str = datetime.now(UTC).date().isoformat()

    return {
        "schedule_id": str(uuid.uuid4()),
        "facility_id": str(facility_id),
        "scheduled_items": [
            {
                "item_id": "maint_task_01",
                "asset_id": str(asset_id),
                "scheduled_date": today_str,
                "priority": "high",
                "estimated_duration_hours": 2.5,
                "required_technician_skills": ["mechanical", "hydraulics"],
                "required_parts": [
                    {
                        "part_id": "SKU-SEAL-77",
                        "part_name": "High-Pressure Hydraulic Seal",
                        "quantity": 2,
                    }
                ],
            }
        ],
        "inventory_shortages": [],
        "drafted_purchase_orders": [],
        "total_tasks_scheduled": 1,
        "critical_shortage_count": 0,
        "schedule_summary": (
            "1 high-priority maintenance task scheduled with full parts availability."
        ),
        "confidence": 0.90,
    }


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
            "errors": [
                "Loop guard exceeded max iterations in maintenance_inventory_node"
            ],
            "iteration_count": current_iterations + 1,
        }

    configurable = (config or {}).get("configurable", {})
    agent_fn = configurable.get(
        "maintenance_inventory_callable", default_maintenance_inventory_callable
    )

    context: dict[str, Any] = {
        "tenant_id": state["tenant_id"],
        "facility_id": state["facility_id"],
        "trigger": state["trigger"],
        "risk_scores": state.get("risk_scores"),
        "asset_graph": state.get("asset_graph"),
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
        logger.error(
            "Maintenance inventory failed validation after retry. Halting for escalation."
        )
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
