"""Agent 1 — Planner Agent LangGraph node (PROJECT_PLAN.md §4.3).

Decomposes incoming triggers/queries into agent tasks and loads the asset dependency
graph topology. Validates all outputs against PlannerOutput before updating state.
"""

import logging
import uuid
from typing import Any

import networkx as nx
from langchain_core.runnables import RunnableConfig

from app.agents.state import MAX_GRAPH_ITERATIONS, FacilityTwinState
from app.agents.tools.asset_graph_tool import (
    build_asset_graph_from_records,
    load_facility_asset_graph,
)
from app.agents.validation import AgentEscalationRequired, execute_agent_with_retry
from app.schemas.agent_outputs.planner import (
    AssetGraphSummary,
    PlannerOutput,
    PlannerSubTask,
)

logger = logging.getLogger("agents.planner")


def _find_matching_assets_in_query(
    graph: nx.DiGraph,
    user_query: str | None,
) -> list[str]:
    """Matches asset names or IDs against user query text."""
    if not user_query:
        return []

    query_lower = user_query.lower()
    matches: list[str] = []

    for node_id, data in graph.nodes(data=True):
        asset_name = str(data.get("name", "")).lower()
        if (asset_name and asset_name in query_lower) or (node_id.lower() in query_lower):
            matches.append(str(node_id))

    return matches


def decompose_facility_plan(
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    trigger: str,
    user_query: str | None,
    graph: nx.DiGraph,
    summary: AssetGraphSummary,
    target_asset_ids: list[uuid.UUID] | None = None,
) -> PlannerOutput:
    """Decomposes trigger and asset topology into a validated multi-agent execution plan."""
    plan_id = uuid.uuid4()
    tasks: list[PlannerSubTask] = []
    active_agents: list[Any] = []

    matched_nodes = _find_matching_assets_in_query(graph, user_query)
    target_uuids: list[uuid.UUID] = (
        target_asset_ids if target_asset_ids else [uuid.UUID(m) for m in matched_nodes]
    )

    if trigger == "user_query" and target_uuids:
        # User query targeting specific asset(s)
        primary_target = target_uuids[0]
        descendants: set[str] = (
            nx.descendants(graph, str(primary_target))
            if graph.has_node(str(primary_target))
            else set()
        )
        downstream_uuids = [uuid.UUID(d) for d in sorted(descendants)]
        all_involved = target_uuids + downstream_uuids

        node_data = graph.nodes.get(str(primary_target), {})
        target_name = node_data.get("name", f"Asset {primary_target}")

        goal = f"Analyze shutdown scenario and cascade impact for {target_name}: '{user_query}'"
        active_agents = [
            "risk_assessment",
            "simulation_decision",
            "maintenance_inventory",
            "route_optimization",
        ]

        tasks = [
            PlannerSubTask(
                task_id="task_1_target_risk",
                agent="risk_assessment",
                action=f"Evaluate risk telemetry and failure propensity for {target_name}",
                priority=1,
                dependencies=[],
                target_asset_ids=target_uuids,
            ),
            PlannerSubTask(
                task_id="task_2_cascade_sim",
                agent="simulation_decision",
                action=(
                    f"Simulate shutdown propagation through {len(descendants)} dependents"
                ),
                priority=1,
                dependencies=["task_1_target_risk"],
                target_asset_ids=all_involved,
            ),
            PlannerSubTask(
                task_id="task_3_parts_check",
                agent="maintenance_inventory",
                action="Verify spare parts availability for emergency isolation or repair",
                priority=2,
                dependencies=["task_2_cascade_sim"],
                target_asset_ids=target_uuids,
            ),
            PlannerSubTask(
                task_id="task_4_route_dispatch",
                agent="route_optimization",
                action="Compute technician dispatch route for isolation inspection",
                priority=2,
                dependencies=["task_3_parts_check"],
                target_asset_ids=target_uuids,
            ),
        ]
        reasoning = (
            f"User query identified specific target asset '{target_name}' with "
            f"{len(descendants)} downstream dependents in the cascade tree. "
            "Decomposed into risk evaluation, cascade simulation, inventory check, and dispatch."
        )
        confidence = 0.95

    elif trigger == "alert":
        # Telemetry anomaly alert trigger
        alert_targets = target_uuids or summary.critical_path_asset_ids or summary.root_asset_ids
        goal = "Evaluate anomalous telemetry alert, assess failure risk, and contain cascade impact"
        active_agents = [
            "risk_assessment",
            "maintenance_inventory",
            "route_optimization",
            "simulation_decision",
        ]

        tasks = [
            PlannerSubTask(
                task_id="task_alert_risk",
                agent="risk_assessment",
                action="Rescore asset risk based on recent anomalous telemetry deviations",
                priority=1,
                dependencies=[],
                target_asset_ids=alert_targets,
            ),
            PlannerSubTask(
                task_id="task_alert_maint",
                agent="maintenance_inventory",
                action="Formulate immediate maintenance work order and reserve necessary parts",
                priority=1,
                dependencies=["task_alert_risk"],
                target_asset_ids=alert_targets,
            ),
            PlannerSubTask(
                task_id="task_alert_route",
                agent="route_optimization",
                action="Dispatch nearest qualified technician to anomalous asset under 5s SLA",
                priority=2,
                dependencies=["task_alert_maint"],
                target_asset_ids=alert_targets,
            ),
            PlannerSubTask(
                task_id="task_alert_sim",
                agent="simulation_decision",
                action="Assess downstream shutdown cascade and generate operator decision summary",
                priority=2,
                dependencies=["task_alert_route"],
                target_asset_ids=alert_targets,
            ),
        ]
        reasoning = (
            "Anomaly alert trigger decomposed into high-priority triage: immediate telemetry "
            "rescore, emergency maintenance work order, rapid technician routing, and cascade."
        )
        confidence = 0.93

    else:
        # Standard scheduled facility evaluation (or fallback general query)
        all_targets = summary.critical_path_asset_ids or summary.root_asset_ids
        goal = (
            f"Execute scheduled facility-wide health evaluation: '{user_query}'"
            if user_query
            else "Execute scheduled facility-wide health evaluation and dispatch planning"
        )
        active_agents = [
            "risk_assessment",
            "maintenance_inventory",
            "route_optimization",
            "simulation_decision",
        ]

        tasks = [
            PlannerSubTask(
                task_id="task_sched_risk",
                agent="risk_assessment",
                action="Score predictive failure risks across facility assets using XGBoost model",
                priority=1,
                dependencies=[],
                target_asset_ids=all_targets,
            ),
            PlannerSubTask(
                task_id="task_sched_maint",
                agent="maintenance_inventory",
                action="Generate 30-day maintenance schedule and identify inventory shortages",
                priority=2,
                dependencies=["task_sched_risk"],
                target_asset_ids=all_targets,
            ),
            PlannerSubTask(
                task_id="task_sched_route",
                agent="route_optimization",
                action="Solve CVRP technician dispatch routes under 5s solver SLA",
                priority=3,
                dependencies=["task_sched_maint"],
                target_asset_ids=all_targets,
            ),
            PlannerSubTask(
                task_id="task_sched_decision",
                agent="simulation_decision",
                action="Synthesize risk, maintenance schedule, and dispatch routes into report",
                priority=4,
                dependencies=["task_sched_route"],
                target_asset_ids=all_targets,
            ),
        ]
        reasoning = (
            f"Scheduled facility evaluation covering {summary.total_nodes} assets and "
            f"{summary.total_edges} dependency edges. Decomposed into sequential 4-agent pipeline."
        )
        confidence = 0.95

    return PlannerOutput(
        plan_id=plan_id,
        goal=goal,
        target_facility_id=facility_id,
        active_agents=active_agents,
        tasks=tasks,
        asset_graph_summary=summary,
        reasoning=reasoning,
        confidence=confidence,
    )


async def real_planner_callable(context: dict[str, Any]) -> dict[str, Any]:
    """Production callable for Planner Agent: loads asset graph and decomposes tasks."""
    facility_id_raw = context.get("facility_id", str(uuid.uuid4()))
    facility_id = (
        uuid.UUID(facility_id_raw) if isinstance(facility_id_raw, str) else facility_id_raw
    )
    tenant_id_raw = context.get("tenant_id", str(uuid.uuid4()))
    tenant_id = uuid.UUID(tenant_id_raw) if isinstance(tenant_id_raw, str) else tenant_id_raw
    trigger = str(context.get("trigger", "scheduled"))
    user_query = context.get("user_query")
    session = context.get("db_session")

    target_asset_ids = context.get("target_asset_ids")

    if session is not None:
        graph, summary, _ = await load_facility_asset_graph(
            session, tenant_id=tenant_id, facility_id=facility_id
        )
    elif context.get("assets") is not None:
        graph, summary, _ = build_asset_graph_from_records(
            context.get("assets", []), context.get("dependencies", [])
        )
    else:
        # Default mock facility topology with 2 sample nodes for standalone tests
        sample_1 = uuid.uuid4()
        sample_2 = uuid.uuid4()
        mock_assets = [
            {"id": sample_1, "name": "Pump 1", "type": "pump", "status": "operational"},
            {"id": sample_2, "name": "Conveyor A", "type": "conveyor", "status": "operational"},
        ]
        mock_deps = [{"parent_asset_id": sample_2, "child_asset_id": sample_1}]
        graph, summary, _ = build_asset_graph_from_records(mock_assets, mock_deps)

    planner_output = decompose_facility_plan(
        tenant_id=tenant_id,
        facility_id=facility_id,
        trigger=trigger,
        user_query=user_query,
        graph=graph,
        summary=summary,
        target_asset_ids=target_asset_ids,
    )
    return planner_output.model_dump(mode="json")


async def planner_node(
    state: FacilityTwinState,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """LangGraph node executing the Planner Agent with schema validation and retry harness."""
    if state.get("halted_for_escalation"):
        return {}

    current_iterations = state.get("iteration_count", 0)
    if current_iterations >= MAX_GRAPH_ITERATIONS:
        logger.error(
            "Planner node loop guard triggered: %d >= %d", current_iterations, MAX_GRAPH_ITERATIONS
        )
        return {
            "halted_for_escalation": True,
            "escalation_reason": "Loop guard exceeded maximum iterations",
            "errors": [f"Loop guard exceeded {MAX_GRAPH_ITERATIONS} iterations in planner_node"],
            "iteration_count": current_iterations + 1,
        }

    configurable = (config or {}).get("configurable", {})
    agent_fn = configurable.get("planner_callable", real_planner_callable)

    context: dict[str, Any] = {
        "tenant_id": state["tenant_id"],
        "facility_id": state["facility_id"],
        "trigger": state["trigger"],
        "user_query": state.get("user_query"),
        "db_session": configurable.get("db_session"),
        "assets": configurable.get("assets"),
        "dependencies": configurable.get("dependencies"),
        "target_asset_ids": configurable.get("target_asset_ids"),
    }

    try:
        result = await execute_agent_with_retry("planner", PlannerOutput, agent_fn, context)
        validated: PlannerOutput = result.output

        return {
            "asset_graph": {
                "summary": validated.asset_graph_summary.model_dump(mode="json"),
                "tasks": [t.model_dump(mode="json") for t in validated.tasks],
                "plan_id": str(validated.plan_id),
                "active_agents": list(validated.active_agents),
                "reasoning": validated.reasoning,
            },
            "confidence": validated.confidence,
            "iteration_count": current_iterations + 1,
        }
    except AgentEscalationRequired as exc:
        logger.error("Planner failed schema validation after retry. Halting branch for escalation.")
        return {
            "halted_for_escalation": True,
            "escalation_reason": str(exc),
            "validation_errors": list(exc.validation_errors),
            "errors": [f"Planner validation escalation: {exc}"],
            "iteration_count": current_iterations + 1,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error in planner_node: %s", exc)
        return {
            "halted_for_escalation": True,
            "escalation_reason": f"Unhandled exception in planner_node: {exc}",
            "errors": [f"Planner node runtime error: {exc}"],
            "iteration_count": current_iterations + 1,
        }
