"""Unit tests for Phase 4 Task 5: Agent 3 — Maintenance & Inventory Planning (Issue #27).

Asserts on:
1. Urgency window constraint and priority mapping (critical: 1-2, high: 3-5, med: 6-14, low: 15-30).
2. Daily technician labor capacity constraint enforcement (tasks roll forward when hours saturated).
3. Failure mode to domain skills and required spare parts mapping (HDF, PWF, TWF, OSF).
4. Inventory stock deficit identification, shortage counts, and impacted asset tracking.
5. Draft purchase order generation with valid SKUs, quantities, costs, and vendors.
6. Critical shortage counting for tasks with high/critical priority blocked by parts.
7. Strict Pydantic schema validation contract compliance for MaintenanceInventoryOutput.
8. Direct node execution and multi-agent compiled LangGraph pipeline integration.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from langchain_core.runnables import RunnableConfig

from app.agents.graph import run_facility_twin_pipeline
from app.agents.maintenance_inventory import (
    maintenance_inventory_node,
    plan_maintenance_and_inventory,
)
from app.agents.state import create_initial_state
from app.agents.tools.inventory_tool import (
    evaluate_inventory_gaps_tool,
    get_parts_for_failure_mode,
)
from app.agents.tools.maintenance_scheduler import generate_constraint_schedule
from app.agents.validation import validate_agent_output
from app.schemas.agent_outputs.maintenance_inventory import (
    MaintenanceInventoryOutput,
    MaintenanceScheduleItem,
)


@pytest.fixture
def sample_facility_id() -> uuid.UUID:
    return uuid.uuid4()


# ============================================================================
# 1. Constraint Scheduling Tests
# ============================================================================


def test_urgency_window_and_priority_mapping() -> None:
    """Verifies that maintenance tasks are packed within urgency windows corresponding
    to risk scores."""
    start_date = date(2026, 10, 5)  # Monday
    id_crit = uuid.uuid4()
    id_high = uuid.uuid4()
    id_med = uuid.uuid4()
    id_low = uuid.uuid4()

    scored = [
        {"asset_id": id_crit, "risk_score": 85.0, "predicted_failure_mode": "HDF"},
        {"asset_id": id_high, "risk_score": 60.0, "predicted_failure_mode": "OSF"},
        {"asset_id": id_med, "risk_score": 45.0, "predicted_failure_mode": "TWF"},
        {"asset_id": id_low, "risk_score": 20.0, "predicted_failure_mode": "RNF"},
    ]

    schedule = generate_constraint_schedule(scored, start_date=start_date)

    assert len(schedule) == 4
    task_by_id = {item.asset_id: item for item in schedule}

    # Critical: window Days 1-2 (offset 0..1)
    crit_task = task_by_id[id_crit]
    assert crit_task.priority == "critical"
    assert start_date <= crit_task.scheduled_date <= start_date + timedelta(days=1)

    # High: window Days 3-5 (offset 2..4)
    high_task = task_by_id[id_high]
    assert high_task.priority == "high"
    assert (
        start_date + timedelta(days=2)
        <= high_task.scheduled_date
        <= start_date + timedelta(days=4)
    )

    # Medium: window Days 6-14 (offset 5..13)
    med_task = task_by_id[id_med]
    assert med_task.priority == "medium"
    assert (
        start_date + timedelta(days=5)
        <= med_task.scheduled_date
        <= start_date + timedelta(days=13)
    )

    # Low: window Days 15-30 (offset 14..29)
    low_task = task_by_id[id_low]
    assert low_task.priority == "low"
    assert (
        start_date + timedelta(days=14)
        <= low_task.scheduled_date
        <= start_date + timedelta(days=29)
    )


def test_daily_technician_capacity_constraint() -> None:
    """Verifies that daily technician labor limits are strictly enforced and saturated
    days cause tasks to overflow to the next available working day."""
    start_date = date(2026, 10, 5)
    # 3 critical tasks, each requiring 3.5h. If daily limit is 4.0h, each MUST go to a distinct day!
    scored = [
        {"asset_id": uuid.uuid4(), "risk_score": 90.0, "predicted_failure_mode": "HDF"},
        {"asset_id": uuid.uuid4(), "risk_score": 85.0, "predicted_failure_mode": "HDF"},
        {"asset_id": uuid.uuid4(), "risk_score": 80.0, "predicted_failure_mode": "HDF"},
    ]

    schedule = generate_constraint_schedule(
        scored,
        start_date=start_date,
        daily_tech_hours=4.0,  # Only one 3.5h task fits per day
    )

    assert len(schedule) == 3
    dates = [item.scheduled_date for item in schedule]
    # Dates must all be distinct
    assert len(set(dates)) == 3
    assert dates[0] == start_date
    assert dates[1] == start_date + timedelta(days=1)
    assert dates[2] == start_date + timedelta(days=2)


def test_failure_mode_to_skills_and_parts_mapping() -> None:
    """Verifies that failure modes map to appropriate technical competencies and spare parts."""
    hdf_parts = get_parts_for_failure_mode("HDF")
    assert len(hdf_parts) == 1
    assert "SEAL" in hdf_parts[0].part_id

    pwf_parts = get_parts_for_failure_mode("PWF")
    assert len(pwf_parts) == 1
    assert "CONTACTOR" in pwf_parts[0].part_id

    twf_parts = get_parts_for_failure_mode("TWF")
    assert len(twf_parts) == 1
    assert "TOOL" in twf_parts[0].part_id

    osf_parts = get_parts_for_failure_mode("OSF")
    assert len(osf_parts) == 1
    assert "BEARING" in osf_parts[0].part_id


# ============================================================================
# 2. Inventory Gap Analysis & PO Generation Tests
# ============================================================================


def test_inventory_shortage_detection_and_impacted_assets() -> None:
    """Verifies that parts demand exceeding on-hand stock generates InventoryShortage records
    and tracks impacted asset identifiers."""
    id1 = uuid.uuid4()
    id2 = uuid.uuid4()

    tasks = [
        MaintenanceScheduleItem(
            item_id="task_1",
            asset_id=id1,
            scheduled_date=date(2026, 10, 6),
            priority="critical",
            estimated_duration_hours=3.5,
            required_technician_skills=["mechanical", "thermal"],
            required_parts=get_parts_for_failure_mode("HDF"),  # SKU-SEAL-HDF
        ),
        MaintenanceScheduleItem(
            item_id="task_2",
            asset_id=id2,
            scheduled_date=date(2026, 10, 7),
            priority="critical",
            estimated_duration_hours=3.5,
            required_technician_skills=["mechanical", "thermal"],
            required_parts=get_parts_for_failure_mode("HDF"),  # SKU-SEAL-HDF
        ),
    ]

    # Only 1 seal on hand, but 2 needed
    shortages, draft_pos = evaluate_inventory_gaps_tool(
        tasks,
        custom_on_hand={"SKU-SEAL-HDF": 1},
    )

    assert len(shortages) == 1
    shortage = shortages[0]
    assert shortage.part_id == "SKU-SEAL-HDF"
    assert shortage.needed_quantity == 2
    assert shortage.available_quantity == 1
    assert shortage.shortage_count == 1
    assert set(shortage.impacted_asset_ids) == {id1, id2}

    assert len(draft_pos) == 1
    po = draft_pos[0]
    assert po.part_id == "SKU-SEAL-HDF"
    assert po.order_quantity == 2  # deficit (1) + safety buffer (1)
    assert po.estimated_cost_usd > 0.0
    assert po.vendor is not None


def test_plan_maintenance_and_inventory_full_pipeline(
    sample_facility_id: uuid.UUID,
) -> None:
    """Verifies end-to-end plan generation, critical shortage counting, and confidence scoring."""
    id1 = uuid.uuid4()
    id2 = uuid.uuid4()

    scored = [
        {"asset_id": id1, "risk_score": 88.0, "predicted_failure_mode": "HDF"},
        {"asset_id": id2, "risk_score": 42.0, "predicted_failure_mode": "TWF"},
    ]

    # Zero stock for HDF seal, full stock for TWF tool insert
    output = plan_maintenance_and_inventory(
        facility_id=sample_facility_id,
        scored_assets=scored,
        custom_on_hand={"SKU-SEAL-HDF": 0, "SKU-TOOL-INSERT": 5},
    )

    assert isinstance(output, MaintenanceInventoryOutput)
    assert output.total_tasks_scheduled == 2
    assert output.critical_shortage_count == 1  # 1 critical task blocked
    assert len(output.inventory_shortages) == 1
    assert len(output.drafted_purchase_orders) == 1
    assert output.confidence < 0.94  # penalized for critical shortage
    assert "critical task(s) impacted" in output.schedule_summary


def test_empty_facility_maintenance_planning(
    sample_facility_id: uuid.UUID,
) -> None:
    """Verifies that an empty asset set produces a safe empty schedule."""
    output = plan_maintenance_and_inventory(
        facility_id=sample_facility_id,
        scored_assets=[],
    )

    assert output.total_tasks_scheduled == 0
    assert output.critical_shortage_count == 0
    assert output.scheduled_items == []
    assert output.inventory_shortages == []
    assert output.confidence >= 0.90


# ============================================================================
# 3. Schema Contract Compliance & LangGraph Node Execution Tests
# ============================================================================


def test_maintenance_inventory_output_satisfies_pydantic_schema(
    sample_facility_id: uuid.UUID,
) -> None:
    """Verifies strict Pydantic contract compliance for MaintenanceInventoryOutput."""
    scored = [
        {"asset_id": uuid.uuid4(), "risk_score": 75.0, "predicted_failure_mode": "OSF"},
    ]

    output = plan_maintenance_and_inventory(
        facility_id=sample_facility_id,
        scored_assets=scored,
    )
    raw_dict = output.model_dump(mode="json")

    validated = validate_agent_output(MaintenanceInventoryOutput, raw_dict)
    assert validated.schedule_id == output.schedule_id
    assert validated.facility_id == sample_facility_id
    assert validated.total_tasks_scheduled == 1
    assert validated.critical_shortage_count == 0
    assert validated.confidence == 0.94


@pytest.mark.anyio
async def test_maintenance_inventory_node_direct_execution() -> None:
    """Verifies direct execution of maintenance_inventory_node with custom scored assets."""
    asset_id = uuid.uuid4()
    initial_state = create_initial_state(
        tenant_id=str(uuid.uuid4()),
        facility_id=str(uuid.uuid4()),
    )
    initial_state["risk_scores"] = [
        {
            "asset_id": str(asset_id),
            "risk_score": 82.0,
            "urgency_rank": 1,
            "risk_tier": "critical",
            "predicted_failure_mode": "HDF",
        }
    ]

    delta = await maintenance_inventory_node(initial_state)

    assert "maintenance_schedule" in delta
    assert "inventory_gaps" in delta
    assert len(delta["maintenance_schedule"]) == 1
    assert delta["maintenance_schedule"][0]["asset_id"] == str(asset_id)
    assert delta["maintenance_schedule"][0]["priority"] == "critical"
    assert delta["iteration_count"] == 1


@pytest.mark.anyio
async def test_compiled_pipeline_with_planner_risk_and_maintenance_agents() -> None:
    """Verifies that the compiled LangGraph pipeline executes Hop 1, Hop 2, and Hop 3
    sequentially, populating both maintenance_schedule and inventory_gaps."""
    pump_id = uuid.uuid4()
    boiler_id = uuid.uuid4()

    assets = [
        {"id": pump_id, "name": "Main Pump", "status": "operational", "risk_score": 88.0},
        {"id": boiler_id, "name": "Aux Boiler", "status": "operational", "risk_score": 35.0},
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
        }
    }

    final_state = await run_facility_twin_pipeline(initial_state, config=config)

    assert final_state["halted_for_escalation"] is False
    assert final_state["asset_graph"] is not None
    assert final_state["risk_scores"] is not None
    assert final_state["maintenance_schedule"] is not None
    assert len(final_state["maintenance_schedule"]) == 2
    assert final_state["inventory_gaps"] is not None
    assert final_state["iteration_count"] == 5
