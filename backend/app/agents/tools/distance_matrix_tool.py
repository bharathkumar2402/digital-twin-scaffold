"""Distance Matrix Tool for Agent 4 — Route Optimization (PROJECT_PLAN.md §4.3).

Builds spatial distance and transit travel-time matrices for facility assets and
technician dispatch depots, operating over local pixel/meter coordinates.
"""

from __future__ import annotations

import math
import uuid
from typing import Any


DEFAULT_WALKING_SPEED_METERS_PER_MINUTE = 60.0  # ~1.0 m/s average facility pace


def compute_euclidean_distance(
    coord1: tuple[float, float],
    coord2: tuple[float, float],
) -> float:
    """Calculates 2D Euclidean distance in meters between two coordinate points."""
    return math.hypot(coord2[0] - coord1[0], coord2[1] - coord1[1])


def build_distance_and_time_matrices(
    locations: list[tuple[float, float]],
    speed_meters_per_minute: float = DEFAULT_WALKING_SPEED_METERS_PER_MINUTE,
) -> tuple[list[list[float]], list[list[float]]]:
    """Generates pairwise distance (meters) and travel-time (minutes) matrices.

    Args:
        locations: List of (x, y) coordinates where index 0 is typically the depot/dispatch office.
        speed_meters_per_minute: Facility traversal speed in meters/minute.

    Returns:
        tuple of (distance_matrix_meters, time_matrix_minutes)
    """
    n = len(locations)
    distance_matrix: list[list[float]] = [[0.0] * n for _ in range(n)]
    time_matrix: list[list[float]] = [[0.0] * n for _ in range(n)]

    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            dist = compute_euclidean_distance(locations[i], locations[j])
            distance_matrix[i][j] = round(dist, 2)
            time_matrix[i][j] = round(dist / speed_meters_per_minute, 2)

    return distance_matrix, time_matrix


def extract_asset_coordinates_map(
    assets: list[Any] | None = None,
    asset_graph: dict[str, Any] | None = None,
) -> dict[str, tuple[float, float]]:
    """Extracts a mapping of asset_id (str) -> (x, y) coordinates.

    Supports raw asset models, dictionaries, and NetworkX serialized asset_graph structures.
    """
    coord_map: dict[str, tuple[float, float]] = {}

    if assets:
        for asset in assets:
            raw_id = getattr(asset, "id", None) or (
                asset.get("id") if isinstance(asset, dict) else None
            )
            if raw_id is None:
                continue
            str_id = str(raw_id)
            raw_x = getattr(asset, "x", None) or (
                asset.get("x") if isinstance(asset, dict) else 0.0
            )
            raw_y = getattr(asset, "y", None) or (
                asset.get("y") if isinstance(asset, dict) else 0.0
            )
            coord_map[str_id] = (float(raw_x or 0.0), float(raw_y or 0.0))

    if asset_graph and "nodes" in asset_graph:
        for node in asset_graph.get("nodes", []):
            node_id = str(node.get("id", ""))
            if node_id and node_id not in coord_map:
                coord_map[node_id] = (
                    float(node.get("x", 0.0)),
                    float(node.get("y", 0.0)),
                )

    return coord_map


def get_or_create_coordinate(
    asset_id: uuid.UUID | str,
    coordinate_map: dict[str, tuple[float, float]],
    fallback_index: int = 0,
) -> tuple[float, float]:
    """Retrieves coordinate for asset, or generates a deterministic fallback location on a grid."""
    str_id = str(asset_id)
    if str_id in coordinate_map:
        return coordinate_map[str_id]

    # Deterministic fallback layout: 25-meter grid spacing
    grid_col = fallback_index % 5
    grid_row = fallback_index // 5
    return (float(50.0 + grid_col * 25.0), float(50.0 + grid_row * 25.0))
