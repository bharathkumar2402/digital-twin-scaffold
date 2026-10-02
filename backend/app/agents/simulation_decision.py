"""Agent 5 — Simulation & Decision Agent LangGraph node (PROJECT_PLAN.md §4.3).

Executes cascade failure propagation simulation over dependency graph and synthesizes
executive decision summary. Validates all outputs against SimulationDecisionOutput.
"""

import logging
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.state import MAX_GRAPH_ITERATIONS, FacilityTwinState
from app.agents.validation import AgentEscalationRequired, execute_agent_with_retry
import networkx as nx

from app.agents.tools.asset_graph_tool import build_asset_graph_from_records
from app.agents.tools.cascade_simulator_tool import (
    resolve_simulation_target_asset,
    simulate_failure_cascade,
)
from app.agents.tools.decision_synthesizer_tool import synthesize_executive_decision
from app.schemas.agent_outputs.simulation_decision import SimulationDecisionOutput

logger = logging.getLogger("agents.simulation_decision")


async def real_simulation_decision_callable(context: dict[str, Any]) -> dict[str, Any]:
    """Production callable for Agent 5: runs cascade simulation and synthesizes decision."""
    facility_id_raw = context.get("facility_id", str(uuid.uuid4()))
    facility_id = (
        uuid.UUID(facility_id_raw) if isinstance(facility_id_raw, str) else facility_id_raw
    )

    trigger = context.get("trigger", "scheduled")
    scenario_trigger: Any = "anomaly_alert" if trigger == "alert" else trigger
    if scenario_trigger not in ("user_query", "scheduled", "anomaly_alert"):
        scenario_trigger = "scheduled"

    user_query = context.get("user_query")
    risk_scores = context.get("risk_scores")
    maintenance_schedule = context.get("maintenance_schedule")
    inventory_gaps = context.get("inventory_gaps")
    dispatch_routes = context.get("dispatch_routes")

    # 1. Resolve or construct NetworkX dependency graph
    graph = context.get("graph")
    if not isinstance(graph, nx.DiGraph):
        asset_graph_data = context.get("asset_graph")
        if isinstance(asset_graph_data, dict) and "graph_data" in asset_graph_data and asset_graph_data["graph_data"]:
            graph = nx.node_link_graph(asset_graph_data["graph_data"])
        elif isinstance(asset_graph_data, dict) and "nodes" in asset_graph_data and "links" in asset_graph_data:
            graph = nx.node_link_graph(asset_graph_data)
        elif context.get("assets") is not None or context.get("dependencies") is not None:
            graph, _, _ = build_asset_graph_from_records(
                context.get("assets", []), context.get("dependencies", [])
            )
        else:
            # Build representative facility topology for evaluation
            root_id = uuid.uuid4()
            child_1 = uuid.uuid4()
            child_2 = uuid.uuid4()
            mock_assets = [
                {"id": root_id, "name": "Pump 7", "type": "cooling_pump", "zone": "Zone A Cooling"},
                {"id": child_1, "name": "Heat Exchanger 2", "type": "heat_exchanger", "zone": "Primary Loop"},
                {"id": child_2, "name": "Turbine Generator 1", "type": "turbine", "zone": "Generation Block"},
            ]
            mock_deps = [
                {"child_asset_id": root_id, "parent_asset_id": child_1},
                {"child_asset_id": child_1, "parent_asset_id": child_2},
            ]
            graph, _, _ = build_asset_graph_from_records(mock_assets, mock_deps)

    # 2. Resolve root cause asset for simulation
    root_cause_asset_id = resolve_simulation_target_asset(
        graph,
        user_query=user_query,
        risk_scores=risk_scores,
    )

    # 3. Simulate failure cascade propagation
    cascade_impact = simulate_failure_cascade(
        graph=graph,
        root_cause_asset_id=root_cause_asset_id,
    )

    # 4. Synthesize multi-agent executive decision report
    decision_output = synthesize_executive_decision(
        facility_id=facility_id,
        scenario_trigger=scenario_trigger,
        cascade_impact=cascade_impact,
        user_query=user_query,
        risk_scores=risk_scores,
        maintenance_schedule=maintenance_schedule,
        inventory_gaps=inventory_gaps,
        dispatch_routes=dispatch_routes,
    )

    return decision_output.model_dump(mode="json")


default_simulation_decision_callable = real_simulation_decision_callable


async def simulation_decision_node(
    state: FacilityTwinState,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """LangGraph node executing Simulation & Decision Agent with schema validation."""
    if state.get("halted_for_escalation"):
        return {}

    current_iterations = state.get("iteration_count", 0)
    if current_iterations >= MAX_GRAPH_ITERATIONS:
        logger.error(
            "Simulation decision node loop guard triggered: %d >= %d",
            current_iterations,
            MAX_GRAPH_ITERATIONS,
        )
        return {
            "halted_for_escalation": True,
            "escalation_reason": "Loop guard exceeded maximum iterations",
            "errors": [
                f"Loop guard exceeded {MAX_GRAPH_ITERATIONS} iterations in simulation_decision_node"
            ],
            "iteration_count": current_iterations + 1,
        }

    configurable = (config or {}).get("configurable", {})
    agent_fn = configurable.get(
        "simulation_decision_callable", default_simulation_decision_callable
    )

    context: dict[str, Any] = {
        "tenant_id": state["tenant_id"],
        "facility_id": state["facility_id"],
        "trigger": state["trigger"],
        "user_query": state.get("user_query"),
        "asset_graph": state.get("asset_graph"),
        "risk_scores": state.get("risk_scores"),
        "maintenance_schedule": state.get("maintenance_schedule"),
        "inventory_gaps": state.get("inventory_gaps"),
        "dispatch_routes": state.get("dispatch_routes"),
        "graph": configurable.get("graph"),
        "assets": configurable.get("assets"),
        "dependencies": configurable.get("dependencies"),
    }

    try:
        result = await execute_agent_with_retry(
            "simulation_decision", SimulationDecisionOutput, agent_fn, context
        )
        validated: SimulationDecisionOutput = result.output

        return {
            "simulation_result": validated.cascade_impact.model_dump(mode="json"),
            "decision_report": validated.executive_summary,
            "confidence": validated.confidence_score,
            "iteration_count": current_iterations + 1,
        }
    except AgentEscalationRequired as exc:
        logger.error("Simulation decision failed validation after retry. Halting for escalation.")
        return {
            "halted_for_escalation": True,
            "escalation_reason": str(exc),
            "validation_errors": list(exc.validation_errors),
            "errors": [f"Simulation decision validation escalation: {exc}"],
            "iteration_count": current_iterations + 1,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in simulation_decision_node: %s", exc)
        return {
            "halted_for_escalation": True,
            "escalation_reason": f"Unhandled exception in simulation_decision_node: {exc}",
            "errors": [f"Simulation decision node runtime error: {exc}"],
            "iteration_count": current_iterations + 1,
        }
