"""5-Agent LangGraph State Machine Architecture (PROJECT_PLAN.md §4.3, §4.5).

Wires Planner -> Risk Assessment -> Maintenance & Inventory -> Route Optimization ->
Simulation & Decision into a compiled LangGraph pipeline with hop-by-hop schema
validation, branch escalation halts, and loop guards.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.maintenance_inventory import maintenance_inventory_node
from app.agents.planner import planner_node
from app.agents.risk_assessment import risk_assessment_node
from app.agents.route_optimization import route_optimization_node
from app.agents.simulation_decision import simulation_decision_node
from app.agents.state import MAX_GRAPH_ITERATIONS, FacilityTwinState

logger = logging.getLogger("agents.graph")


def check_pipeline_branch(state: FacilityTwinState) -> str:
    """Conditional router checking if the pipeline should proceed or halt for escalation."""
    if state.get("halted_for_escalation"):
        logger.info(
            "Pipeline halting branch at current hop. Reason: %s",
            state.get("escalation_reason", "Unspecified escalation"),
        )
        return "halt"

    if state.get("iteration_count", 0) >= MAX_GRAPH_ITERATIONS:
        logger.warning(
            "Pipeline halting branch: loop guard reached %d iterations",
            state.get("iteration_count", 0),
        )
        return "halt"

    return "continue"


def build_facility_twin_graph() -> CompiledStateGraph:
    """Builds and compiles the 5-agent LangGraph pipeline."""
    workflow = StateGraph(FacilityTwinState)

    # 1. Register 5 placeholder agent nodes
    workflow.add_node("planner", planner_node)
    workflow.add_node("risk_assessment", risk_assessment_node)
    workflow.add_node("maintenance_inventory", maintenance_inventory_node)
    workflow.add_node("route_optimization", route_optimization_node)
    workflow.add_node("simulation_decision", simulation_decision_node)

    # 2. Wire entry point
    workflow.add_edge(START, "planner")

    # 3. Wire conditional branch edges with fail-fast safety escalation
    workflow.add_conditional_edges(
        "planner",
        check_pipeline_branch,
        {
            "continue": "risk_assessment",
            "halt": END,
        },
    )
    workflow.add_conditional_edges(
        "risk_assessment",
        check_pipeline_branch,
        {
            "continue": "maintenance_inventory",
            "halt": END,
        },
    )
    workflow.add_conditional_edges(
        "maintenance_inventory",
        check_pipeline_branch,
        {
            "continue": "route_optimization",
            "halt": END,
        },
    )
    workflow.add_conditional_edges(
        "route_optimization",
        check_pipeline_branch,
        {
            "continue": "simulation_decision",
            "halt": END,
        },
    )

    # 4. Wire final hop
    workflow.add_edge("simulation_decision", END)

    return workflow.compile()


async def run_facility_twin_pipeline(
    initial_state: FacilityTwinState,
    config: RunnableConfig | None = None,
) -> FacilityTwinState:
    """Convenience helper to run the compiled 5-agent pipeline end-to-end."""
    graph = build_facility_twin_graph()
    result: dict[str, Any] = await graph.ainvoke(initial_state, config=config)  # type: ignore[assignment]
    return result  # type: ignore[return-value]
