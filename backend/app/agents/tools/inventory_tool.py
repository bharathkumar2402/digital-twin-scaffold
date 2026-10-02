"""Inventory lookup and gap evaluation tool for Agent 3 (Maintenance & Inventory Planning).

Non-negotiable Rule 6: Inventory lookups are tool calls inside Maintenance Planning,
not a separate LangGraph node.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.schemas.agent_outputs.maintenance_inventory import (
    DraftPurchaseOrder,
    InventoryShortage,
    MaintenanceScheduleItem,
    RequiredPartItem,
)

# Standard spare parts catalog for industrial equipment and AI4I failure modes
DEFAULT_PARTS_CATALOG: dict[str, dict[str, Any]] = {
    "SKU-SEAL-HDF": {
        "part_id": "SKU-SEAL-HDF",
        "part_name": "High-Temp Viton Heat Exchanger Seal Kit",
        "category": "cooling",
        "unit_cost_usd": 85.0,
        "vendor": "Apex Fluidics Ltd",
        "lead_time_days": 3,
        "default_stock": 4,
        "failure_modes": ["HDF"],
    },
    "SKU-RAD-HDF": {
        "part_id": "SKU-RAD-HDF",
        "part_name": "Auxiliary Cooling Radiator Assembly",
        "category": "thermal",
        "unit_cost_usd": 420.0,
        "vendor": "ThermaCore Systems",
        "lead_time_days": 7,
        "default_stock": 1,
        "failure_modes": ["HDF"],
    },
    "SKU-TOOL-INSERT": {
        "part_id": "SKU-TOOL-INSERT",
        "part_name": "Tungsten Carbide Milling Inserts (Pack of 10)",
        "category": "tooling",
        "unit_cost_usd": 150.0,
        "vendor": "Precision Machining Supply",
        "lead_time_days": 2,
        "default_stock": 5,
        "failure_modes": ["TWF"],
    },
    "SKU-COLLET-TWF": {
        "part_id": "SKU-COLLET-TWF",
        "part_name": "Precision Spindle Collet 20mm",
        "category": "tooling",
        "unit_cost_usd": 95.0,
        "vendor": "Precision Machining Supply",
        "lead_time_days": 4,
        "default_stock": 2,
        "failure_modes": ["TWF"],
    },
    "SKU-BEARING-HD": {
        "part_id": "SKU-BEARING-HD",
        "part_name": "Heavy-Duty Angular Contact Bearing Set",
        "category": "mechanical",
        "unit_cost_usd": 310.0,
        "vendor": "Timken / Koyo Industrial",
        "lead_time_days": 5,
        "default_stock": 3,
        "failure_modes": ["OSF"],
    },
    "SKU-COUPLING-OSF": {
        "part_id": "SKU-COUPLING-OSF",
        "part_name": "Torsional Flexible Drive Coupling",
        "category": "mechanical",
        "unit_cost_usd": 175.0,
        "vendor": "Motion Dynamics Corp",
        "lead_time_days": 3,
        "default_stock": 2,
        "failure_modes": ["OSF"],
    },
    "SKU-CONTACTOR-PWF": {
        "part_id": "SKU-CONTACTOR-PWF",
        "part_name": "3-Phase Motor Inverter Contactor 60A",
        "category": "electrical",
        "unit_cost_usd": 280.0,
        "vendor": "Schneider Electric Supply",
        "lead_time_days": 4,
        "default_stock": 2,
        "failure_modes": ["PWF"],
    },
    "SKU-PSU-PWF": {
        "part_id": "SKU-PSU-PWF",
        "part_name": "Industrial 24V/40A DIN-Rail Power Supply",
        "category": "electrical",
        "unit_cost_usd": 195.0,
        "vendor": "Mean Well Direct",
        "lead_time_days": 2,
        "default_stock": 2,
        "failure_modes": ["PWF"],
    },
    "SKU-PROBE-RNF": {
        "part_id": "SKU-PROBE-RNF",
        "part_name": "Multi-Sensor Vibration & Thermal Probe Pack",
        "category": "instrumentation",
        "unit_cost_usd": 120.0,
        "vendor": "Sensirion Industrial",
        "lead_time_days": 1,
        "default_stock": 6,
        "failure_modes": ["RNF"],
    },
}


def get_parts_for_failure_mode(failure_mode: str | None) -> list[RequiredPartItem]:
    """Determines bill-of-materials spare parts needed to service a diagnosed failure mode."""
    mode = (failure_mode or "RNF").upper()

    if mode == "HDF":
        return [
            RequiredPartItem(
                part_id="SKU-SEAL-HDF",
                part_name=DEFAULT_PARTS_CATALOG["SKU-SEAL-HDF"]["part_name"],
                quantity=1,
            )
        ]
    if mode == "TWF":
        return [
            RequiredPartItem(
                part_id="SKU-TOOL-INSERT",
                part_name=DEFAULT_PARTS_CATALOG["SKU-TOOL-INSERT"]["part_name"],
                quantity=1,
            )
        ]
    if mode == "OSF":
        return [
            RequiredPartItem(
                part_id="SKU-BEARING-HD",
                part_name=DEFAULT_PARTS_CATALOG["SKU-BEARING-HD"]["part_name"],
                quantity=1,
            )
        ]
    if mode == "PWF":
        return [
            RequiredPartItem(
                part_id="SKU-CONTACTOR-PWF",
                part_name=DEFAULT_PARTS_CATALOG["SKU-CONTACTOR-PWF"]["part_name"],
                quantity=1,
            )
        ]
    # RNF / generic inspection
    return [
        RequiredPartItem(
            part_id="SKU-PROBE-RNF",
            part_name=DEFAULT_PARTS_CATALOG["SKU-PROBE-RNF"]["part_name"],
            quantity=1,
        )
    ]


def evaluate_inventory_gaps_tool(
    scheduled_items: list[MaintenanceScheduleItem],
    *,
    custom_on_hand: dict[str, int] | None = None,
    catalog: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[InventoryShortage], list[DraftPurchaseOrder]]:
    """Analyzes parts demand across scheduled maintenance tasks and identifies stock deficits.

    Returns:
        tuple containing:
        - list[InventoryShortage]: Detected shortages within the scheduled window.
        - list[DraftPurchaseOrder]: Replenishment orders drafted to resolve the shortages.
    """
    parts_db = catalog or DEFAULT_PARTS_CATALOG

    # Default stock inventory if not explicitly overridden
    on_hand = (
        custom_on_hand
        if custom_on_hand is not None
        else {sku: int(info.get("default_stock", 0)) for sku, info in parts_db.items()}
    )

    # 1. Aggregate demand per part across all tasks and track impacted assets
    demand_by_part: dict[str, int] = {}
    impacted_assets: dict[str, list[uuid.UUID]] = {}
    part_names: dict[str, str] = {}

    for task in scheduled_items:
        for part in task.required_parts:
            sku = part.part_id
            qty = part.quantity
            demand_by_part[sku] = demand_by_part.get(sku, 0) + qty
            part_names[sku] = part.part_name

            if sku not in impacted_assets:
                impacted_assets[sku] = []
            if task.asset_id not in impacted_assets[sku]:
                impacted_assets[sku].append(task.asset_id)

    shortages: list[InventoryShortage] = []
    draft_pos: list[DraftPurchaseOrder] = []

    # 2. Check each part with demand against on-hand stock
    for sku, total_needed in demand_by_part.items():
        avail = on_hand.get(sku, 0)
        if total_needed > avail:
            deficit = total_needed - avail
            part_info = parts_db.get(sku, {})
            lead_time = int(part_info.get("lead_time_days", 3))
            unit_cost = float(part_info.get("unit_cost_usd", 100.0))
            vendor = str(part_info.get("vendor", "General Industrial Supply"))
            name = part_names.get(sku, part_info.get("part_name", sku))

            shortages.append(
                InventoryShortage(
                    part_id=sku,
                    part_name=name,
                    needed_quantity=total_needed,
                    available_quantity=avail,
                    shortage_count=deficit,
                    lead_time_days=lead_time,
                    impacted_asset_ids=impacted_assets.get(sku, []),
                )
            )

            # Order deficit plus 1 safety buffer unit
            order_qty = deficit + 1
            est_cost = round(order_qty * unit_cost, 2)
            safe_sku = sku.lower().replace("-", "_")

            draft_pos.append(
                DraftPurchaseOrder(
                    po_id=f"po_{safe_sku}_{uuid.uuid4().hex[:6]}",
                    part_id=sku,
                    order_quantity=order_qty,
                    estimated_cost_usd=est_cost,
                    vendor=vendor,
                )
            )

    return shortages, draft_pos
