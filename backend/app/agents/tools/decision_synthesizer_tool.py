"""Decision Synthesizer Tool for Agent 5 — Simulation & Decision (PROJECT_PLAN.md §4.3).

Synthesizes multi-agent pipeline telemetry (Planner, Risk Assessment, Maintenance & Inventory,
Route Optimization, and Cascade Simulation) into a natural-language executive decision report,
computes holistic system confidence, and evaluates explicit human escalation criteria.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

from app.schemas.agent_outputs.simulation_decision import (
    CascadeImpactReport,
    SimulationDecisionOutput,
)

logger = logging.getLogger("agents.tools.decision_synthesizer")


def synthesize_executive_decision(
    *,
    decision_id: uuid.UUID | None = None,
    facility_id: uuid.UUID,
    scenario_trigger: Literal["user_query", "scheduled", "anomaly_alert"],
    cascade_impact: CascadeImpactReport,
    user_query: str | None = None,
    risk_scores: list[dict[str, Any]] | None = None,
    maintenance_schedule: list[dict[str, Any]] | None = None,
    inventory_gaps: list[dict[str, Any]] | None = None,
    dispatch_routes: list[dict[str, Any]] | None = None,
) -> SimulationDecisionOutput:
    """Combines all upstream outputs into a schema-validated SimulationDecisionOutput."""
    risks = risk_scores or []
    tasks = maintenance_schedule or []
    shortages = inventory_gaps or []
    routes = dispatch_routes or []

    # 1. Determine High/Critical Risk Counts
    critical_risks = [r for r in risks if r.get("risk_tier") == "critical" or float(r.get("risk_score", 0)) >= 75.0]
    high_risks = [r for r in risks if r.get("risk_tier") == "high" or (60.0 <= float(r.get("risk_score", 0)) < 75.0)]

    # 2. Analyze Inventory Shortages
    critical_shortages = [s for s in shortages if int(s.get("needed_quantity", 0)) > int(s.get("available_quantity", 0))]

    # 3. Analyze Technician Routes
    total_assigned_stops = sum(len(r.get("assigned_stops", [])) for r in routes)
    tech_count = len(routes)

    # 4. Evaluate Human Escalation Criteria
    escalation_triggers: list[str] = []

    if critical_shortages and critical_risks:
        escalation_triggers.append(
            f"{len(critical_shortages)} spare part shortage(s) block urgent maintenance on critical assets"
        )
    if cascade_impact.total_affected_assets >= 4 or cascade_impact.cascade_depth >= 3:
        escalation_triggers.append(
            f"Extensive cascade failure risk: {cascade_impact.total_affected_assets} assets affected across {cascade_impact.cascade_depth} topological tiers"
        )
    if any(float(r.get("risk_score", 0)) >= 90.0 for r in risks):
        escalation_triggers.append("Imminent asset breakdown detected (risk score >= 90.0)")

    human_escalation_required = len(escalation_triggers) > 0
    escalation_reason = (
        "; ".join(escalation_triggers) if human_escalation_required else None
    )

    # 5. Formulate Recommended Interventions
    interventions: list[str] = []
    if critical_risks:
        top_asset_id = critical_risks[0].get("asset_id")
        interventions.append(
            f"Dispatch technician to service critical asset {top_asset_id} immediately"
        )
    if critical_shortages:
        interventions.append(
            f"Expedite purchase order fulfillment for {len(critical_shortages)} out-of-stock component SKU(s)"
        )
    if cascade_impact.downstream_shutoff_asset_ids:
        interventions.append(
            f"Prepare bypass or isolation protocol for {len(cascade_impact.downstream_shutoff_asset_ids)} dependent downstream assets"
        )
    if not interventions:
        interventions.append("Proceed with planned preventive maintenance schedule")

    # 6. Synthesize Executive Summary
    trigger_desc = (
        f"investigation of '{user_query}'"
        if (scenario_trigger == "user_query" and user_query)
        else f"{scenario_trigger} facility operational cycle"
    )

    impact_desc = (
        f"Failure at root asset {cascade_impact.root_cause_asset_id} threatens "
        f"{cascade_impact.total_affected_assets} total asset(s) with an estimated "
        f"{cascade_impact.estimated_downtime_hours} hours of facility downtime across {cascade_impact.cascade_depth} cascade levels."
        if cascade_impact.total_affected_assets > 1
        else f"Evaluated asset {cascade_impact.root_cause_asset_id} is topologically isolated with nominal cascade impact."
    )

    maint_desc = (
        f"Maintenance schedule generated {len(tasks)} task(s) dispatched to {tech_count} technician(s) "
        f"covering {total_assigned_stops} stop(s)."
        if tasks
        else "No urgent maintenance interventions required."
    )

    inventory_desc = (
        f"WARNING: {len(critical_shortages)} critical inventory gap(s) identified."
        if critical_shortages
        else "All required maintenance parts are currently verified on-hand."
    )

    summary = (
        f"Multi-agent digital twin analysis concluded successfully for {trigger_desc}. "
        f"{impact_desc} {maint_desc} {inventory_desc}"
    )

    # 7. Compute Synthesized Confidence Score
    confidence = 0.94
    if critical_shortages:
        confidence -= min(0.15, len(critical_shortages) * 0.05)
    if human_escalation_required:
        confidence -= 0.05
    confidence = round(max(0.65, min(0.98, confidence)), 2)

    return SimulationDecisionOutput(
        decision_id=decision_id or uuid.uuid4(),
        facility_id=facility_id,
        scenario_trigger=scenario_trigger,
        cascade_impact=cascade_impact,
        executive_summary=summary,
        confidence_score=confidence,
        human_escalation_required=human_escalation_required,
        escalation_reason=escalation_reason,
        recommended_interventions=interventions,
    )
