"""Asset Graph Tool for Agent 1 — Planner (PROJECT_PLAN.md §4.3).

Queries facility assets and dependencies, builds a directed NetworkX graph
representing operational cascade flow (child/upstream -> parent/downstream),
computes topological metrics, and serializes the graph for agent state.
"""

import logging
import uuid
from typing import Any

import networkx as nx
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.agent_outputs.planner import AssetGraphSummary
from app.services.asset_dependency_service import list_dependencies
from app.services.asset_service import list_assets

logger = logging.getLogger("agents.tools.asset_graph")


def build_asset_graph_from_records(
    assets: list[Any],
    dependencies: list[Any],
) -> tuple[nx.DiGraph, AssetGraphSummary, dict[str, Any]]:
    """Constructs a NetworkX DiGraph from asset and dependency records.

    Direction: An edge (child_id -> parent_id) models failure cascade propagation:
    parent depends on child (child is upstream), so when child fails, the failure
    propagates downstream to parent.
    """
    g = nx.DiGraph()

    for asset in assets or []:
        asset_id_raw = getattr(asset, "id", None) or (
            asset.get("id") if isinstance(asset, dict) else None
        )
        if asset_id_raw is None:
            continue
        asset_id = str(asset_id_raw)

        name = getattr(asset, "name", None) or (
            asset.get("name", "Unknown Asset") if isinstance(asset, dict) else "Unknown Asset"
        )
        asset_type = getattr(asset, "type", None) or (
            asset.get("type", "generic") if isinstance(asset, dict) else "generic"
        )
        raw_status = getattr(asset, "status", None) or (
            asset.get("status", "operational") if isinstance(asset, dict) else "operational"
        )
        status_val = raw_status.value if hasattr(raw_status, "value") else str(raw_status)

        raw_x: Any = getattr(asset, "x", None)
        if raw_x is None and isinstance(asset, dict):
            raw_x = asset.get("x")
        x_val = float(raw_x) if raw_x is not None else 0.0

        raw_y: Any = getattr(asset, "y", None)
        if raw_y is None and isinstance(asset, dict):
            raw_y = asset.get("y")
        y_val = float(raw_y) if raw_y is not None else 0.0

        g.add_node(
            asset_id,
            id=asset_id,
            name=name,
            type=asset_type,
            status=status_val,
            x=x_val,
            y=y_val,
        )

    for dep in dependencies or []:
        child_raw = getattr(dep, "child_asset_id", None) or (
            dep.get("child_asset_id") if isinstance(dep, dict) else None
        )
        parent_raw = getattr(dep, "parent_asset_id", None) or (
            dep.get("parent_asset_id") if isinstance(dep, dict) else None
        )
        if child_raw is None or parent_raw is None:
            continue
        child_id = str(child_raw)
        parent_id = str(parent_raw)

        # Ensure both endpoints exist in graph
        if not g.has_node(child_id):
            g.add_node(child_id, id=child_id, name="Unknown", type="generic", status="operational")
        if not g.has_node(parent_id):
            g.add_node(
                parent_id, id=parent_id, name="Unknown", type="generic", status="operational"
            )

        g.add_edge(child_id, parent_id, relationship="depends_on")

    # Topological calculations
    total_nodes = g.number_of_nodes()
    total_edges = g.number_of_edges()

    # Root assets: nodes with in-degree 0 (no upstream dependencies, top of cascade tree)
    root_node_ids = sorted(node for node in g.nodes if g.in_degree(node) == 0)

    # Critical path assets: nodes with highest downstream dependencies (out-degree > 0)
    out_degrees = [(node, g.out_degree(node)) for node in g.nodes if g.out_degree(node) > 0]
    out_degrees.sort(key=lambda item: (-item[1], item[0]))
    critical_path_ids = [node for node, _ in out_degrees]

    summary = AssetGraphSummary(
        total_nodes=total_nodes,
        total_edges=total_edges,
        root_asset_ids=[uuid.UUID(r) for r in root_node_ids],
        critical_path_asset_ids=[uuid.UUID(c) for c in critical_path_ids],
    )

    serialized = nx.node_link_data(g)
    return g, summary, serialized


async def load_facility_asset_graph(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
) -> tuple[nx.DiGraph, AssetGraphSummary, dict[str, Any]]:
    """Loads facility assets and dependencies from PostgreSQL and constructs the DiGraph."""
    logger.info(
        "Loading facility asset graph from DB: tenant_id=%s, facility_id=%s",
        tenant_id,
        facility_id,
    )
    assets = await list_assets(session, tenant_id=tenant_id, facility_id=facility_id)
    dependencies = await list_dependencies(session, tenant_id=tenant_id, facility_id=facility_id)
    return build_asset_graph_from_records(assets, dependencies)
