"""Cascade Simulator Tool for Agent 5 — Simulation & Decision (PROJECT_PLAN.md §4.3).

Executes NetworkX directed failure cascade propagation analysis over facility asset
dependency topologies. Traces root cause failure down through child and dependent
subsystems, calculates topological cascade depth, and projects downtime.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import networkx as nx

from app.agents.tools.asset_graph_tool import build_asset_graph_from_records
from app.schemas.agent_outputs.simulation_decision import CascadeImpactReport

logger = logging.getLogger("agents.tools.cascade_simulator")


def simulate_failure_cascade(
    *,
    graph: nx.DiGraph,
    root_cause_asset_id: uuid.UUID,
    estimated_base_downtime_hours: float = 2.5,
) -> CascadeImpactReport:
    """Propagates hypothetical failure from root cause through the dependency graph.

    Graph convention: Directed edges (upstream/child -> downstream/parent) represent
    cascade failure propagation.
    """
    root_str = str(root_cause_asset_id)

    if not graph.has_node(root_str):
        # Node not in graph: Isolated asset with no dependencies
        return CascadeImpactReport(
            root_cause_asset_id=root_cause_asset_id,
            directly_affected_asset_ids=[],
            downstream_shutoff_asset_ids=[],
            total_affected_assets=1,
            critical_subsystems_interrupted=[],
            estimated_downtime_hours=estimated_base_downtime_hours,
            cascade_depth=0,
        )

    # 1. Directly affected downstream assets (immediate successors in failure direction)
    direct_successors = sorted(graph.successors(root_str))
    directly_affected_uuids = [uuid.UUID(s) for s in direct_successors if _is_valid_uuid(s)]

    # 2. All downstream shutoff assets (transitive closure / descendants)
    all_descendants = sorted(nx.descendants(graph, root_str))
    downstream_shutoff_uuids = [uuid.UUID(d) for d in all_descendants if _is_valid_uuid(d)]

    total_affected = 1 + len(downstream_shutoff_uuids)

    # 3. Compute topological cascade depth (longest path from root_str)
    subgraph_nodes = {root_str} | set(all_descendants)
    subgraph = graph.subgraph(subgraph_nodes)
    if nx.is_directed_acyclic_graph(subgraph):
        cascade_depth = nx.dag_longest_path_length(subgraph)
    else:
        # Fallback for cyclic graphs: BFS shortest path length to furthest reachable node
        lengths = nx.single_source_shortest_path_length(graph, root_str)
        cascade_depth = max(lengths.values()) if lengths else 0

    # 4. Identify critical subsystems interrupted
    subsystems_interrupted: list[str] = []
    affected_node_ids = [root_str] + all_descendants
    for node_id in affected_node_ids:
        node_data = graph.nodes.get(node_id, {})
        asset_type = node_data.get("type", "")
        asset_name = node_data.get("name", "")
        zone = node_data.get("zone") or node_data.get("subsystem")

        descriptor = zone or (f"{asset_name} ({asset_type})" if asset_type else asset_name)
        if descriptor and descriptor not in subsystems_interrupted:
            subsystems_interrupted.append(descriptor)

    # 5. Estimate facility downtime
    # Scales non-linearly with cascade depth and number of affected assets
    depth_multiplier = 1.0 + (cascade_depth * 0.4)
    scale_factor = 1.0 + (min(10, len(downstream_shutoff_uuids)) * 0.15)
    estimated_downtime = round(estimated_base_downtime_hours * depth_multiplier * scale_factor, 1)

    return CascadeImpactReport(
        root_cause_asset_id=root_cause_asset_id,
        directly_affected_asset_ids=directly_affected_uuids,
        downstream_shutoff_asset_ids=downstream_shutoff_uuids,
        total_affected_assets=total_affected,
        critical_subsystems_interrupted=subsystems_interrupted[:5],  # top 5 key subsystems
        estimated_downtime_hours=estimated_downtime,
        cascade_depth=cascade_depth,
    )


def resolve_simulation_target_asset(
    graph: nx.DiGraph,
    *,
    user_query: str | None = None,
    risk_scores: list[dict[str, Any]] | None = None,
    default_asset_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Identifies the root-cause asset to simulate from query, risk scores, or graph."""
    if user_query:
        query_lower = user_query.lower()
        for node_id, data in graph.nodes(data=True):
            name = str(data.get("name", "")).lower()
            if (name and name in query_lower) or (node_id.lower() in query_lower):
                if _is_valid_uuid(node_id):
                    return uuid.UUID(node_id)

    if risk_scores:
        # Pick highest risk asset
        sorted_risks = sorted(
            risk_scores,
            key=lambda r: float(r.get("risk_score", 0.0)),
            reverse=True,
        )
        if sorted_risks:
            top_asset_raw = sorted_risks[0].get("asset_id")
            if top_asset_raw and _is_valid_uuid(str(top_asset_raw)):
                return uuid.UUID(str(top_asset_raw))

    if graph.number_of_nodes() > 0:
        first_node = next(iter(graph.nodes))
        if _is_valid_uuid(first_node):
            return uuid.UUID(first_node)

    return default_asset_id or uuid.uuid4()


def _is_valid_uuid(val: str) -> bool:
    try:
        uuid.UUID(str(val))
        return True
    except (ValueError, TypeError):
        return False
