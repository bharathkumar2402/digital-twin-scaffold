"""Agent output validation schemas.

Every agent node in `app/agents/` must validate its return value against a schema
defined here before writing to `FacilityTwinState` or persisting to the database.
"""

from app.schemas.agent_outputs.maintenance_inventory import (
    DraftPurchaseOrder,
    InventoryShortage,
    MaintenanceInventoryOutput,
    MaintenanceScheduleItem,
    RequiredPartItem,
)
from app.schemas.agent_outputs.planner import (
    AssetGraphSummary,
    PlannerOutput,
    PlannerSubTask,
)
from app.schemas.agent_outputs.risk_assessment import (
    RiskAssessmentOutput,
    ScoredAsset,
)
from app.schemas.agent_outputs.route_optimization import (
    RouteOptimizationOutput,
    RouteStop,
    TechnicianRoute,
)
from app.schemas.agent_outputs.simulation_decision import (
    CascadeImpactReport,
    SimulationDecisionOutput,
)

__all__ = [
    "AssetGraphSummary",
    "CascadeImpactReport",
    "DraftPurchaseOrder",
    "InventoryShortage",
    "MaintenanceInventoryOutput",
    "MaintenanceScheduleItem",
    "PlannerOutput",
    "PlannerSubTask",
    "RequiredPartItem",
    "RiskAssessmentOutput",
    "RouteOptimizationOutput",
    "RouteStop",
    "ScoredAsset",
    "SimulationDecisionOutput",
    "TechnicianRoute",
]
