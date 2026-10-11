"""Floor-aware technician routing: changing floors costs time and distance, pixels are scaled to
metres, and nothing changes for callers that pass no floors."""

import asyncio
import uuid
from typing import Any

import pytest

from app.agents.route_optimization import real_route_optimization_callable
from app.agents.tools.asset_graph_tool import build_asset_graph_from_records
from app.agents.tools.distance_matrix_tool import (
    FLOOR_HEIGHT_METERS,
    FLOOR_TRANSIT_MINUTES,
    build_distance_and_time_matrices,
    extract_asset_floors_map,
)
from app.agents.tools.route_solver_tool import solve_cvrp_routes

LOCS = [(0.0, 0.0), (300.0, 400.0), (300.0, 400.0)]  # depot, then two assets at the same spot


def test_no_floors_means_the_old_flat_behaviour() -> None:
    flat_d, flat_t = build_distance_and_time_matrices(LOCS)
    same_d, same_t = build_distance_and_time_matrices(LOCS, floors=[0, 0, 0])
    assert flat_d == same_d and flat_t == same_t
    assert flat_d[0][1] == 500.0  # 3-4-5 triangle, in units


def test_changing_floors_adds_vertical_distance_and_lift_time() -> None:
    _, flat_t = build_distance_and_time_matrices(LOCS, floors=[0, 0, 0])
    d, t = build_distance_and_time_matrices(LOCS, floors=[0, 0, 2])
    # asset 2 is two floors up: same horizontal spot, but not the same cost
    assert d[0][2] == pytest.approx(500.0 + 2 * FLOOR_HEIGHT_METERS)
    # walking the horizontal run and the climb, plus waiting for / riding the lift per floor
    expected = (500.0 + 2 * FLOOR_HEIGHT_METERS) / 60.0 + 2 * FLOOR_TRANSIT_MINUTES
    assert t[0][2] == pytest.approx(expected, abs=0.02)
    assert t[0][2] > flat_t[0][2] + 2 * FLOOR_TRANSIT_MINUTES
    assert d[0][1] == 500.0  # the same-floor neighbour is unaffected
    assert d[1][2] == pytest.approx(2 * FLOOR_HEIGHT_METERS)  # between the two: only the climb
    assert t[1][2] > 0.0  # two assets at the same (x, y) on different floors are NOT zero apart


def test_costs_are_symmetric_and_a_negative_level_counts_like_a_positive_one() -> None:
    d, t = build_distance_and_time_matrices(LOCS, floors=[0, -1, 1])
    assert d[1][2] == d[2][1] and t[1][2] == t[2][1]
    assert d[0][1] == pytest.approx(500.0 + FLOOR_HEIGHT_METERS)
    assert d[1][2] == pytest.approx(2 * FLOOR_HEIGHT_METERS)  # basement to first floor = 2 floors


def test_pixels_are_scaled_to_metres() -> None:
    d, _ = build_distance_and_time_matrices(LOCS, meters_per_unit=0.01)
    assert d[0][1] == 5.0


def test_extract_floors_handles_dicts_models_and_missing_values() -> None:
    a, b, c = (uuid.uuid4() for _ in range(3))

    class Model:
        id = b
        floor_level = 2

    out = extract_asset_floors_map([{"id": a, "floor_level": 1}, Model(), {"id": c}, {"name": "x"}])
    assert out == {str(a): 1, str(b): 2, str(c): 0}
    assert extract_asset_floors_map(None) == {}


def _task(asset: uuid.UUID, hours: float = 1.0) -> dict[str, Any]:
    return {
        "item_id": f"t-{asset.hex[:4]}",
        "asset_id": asset,
        "scheduled_date": "2026-10-10",
        "priority": "high",
        "estimated_duration_hours": hours,
        "required_technician_skills": [],
    }


def _travel(floor_of_second: int | None) -> float:
    near, far = uuid.uuid4(), uuid.uuid4()
    coords = {str(near): (300.0, 0.0), str(far): (0.0, 300.0)}  # equally far from the depot
    floors = None if floor_of_second is None else {str(near): 0, str(far): floor_of_second}
    out = solve_cvrp_routes(
        facility_id=uuid.uuid4(),
        tasks=[_task(near), _task(far)],
        asset_coordinates=coords,
        asset_floors=floors,
        time_limit_seconds=1.0,
    )
    return sum(r.total_travel_minutes for r in out.technician_routes)


def test_a_task_on_another_floor_makes_the_route_longer() -> None:
    flat = _travel(None)
    same_floor = _travel(0)
    roof = _travel(2)
    assert same_floor == pytest.approx(flat, abs=0.2)
    assert roof > same_floor + FLOOR_TRANSIT_MINUTES  # at least the lift time, out and back


def test_agent_callable_uses_floors_and_the_pixel_scale() -> None:
    ground, roof = uuid.uuid4(), uuid.uuid4()
    assets = [
        {
            "id": ground,
            "name": "G",
            "type": "pump",
            "status": "operational",
            "x": 1000.0,
            "y": 0.0,
            "floor_level": 0,
        },
        {
            "id": roof,
            "name": "R",
            "type": "pump",
            "status": "operational",
            "x": 1000.0,
            "y": 0.0,
            "floor_level": 2,
        },
    ]
    tasks = [_task(ground), _task(roof)]
    ctx = {
        "facility_id": str(uuid.uuid4()),
        "maintenance_schedule": [{**t, "asset_id": str(t["asset_id"])} for t in tasks],
        "assets": assets,
        "meters_per_pixel": 0.01,  # 1000 px = 10 m
        "time_limit_seconds": 1.0,
    }
    out = asyncio.run(real_route_optimization_callable(ctx))
    stops = {s["asset_id"]: s for r in out["technician_routes"] for s in r["assigned_stops"]}
    assert set(stops) == {str(ground), str(roof)}
    # 10 m on foot is ~0.2 min; the roof stop's travel time is dominated by the lift, not pixels
    assert stops[str(ground)]["travel_time_from_previous_minutes"] < 1.0
    assert (
        max(s["travel_time_from_previous_minutes"] for s in stops.values())
        >= 2 * FLOOR_TRANSIT_MINUTES - 1
    )


def test_graph_nodes_record_their_floor_and_default_to_ground() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    graph, _, _ = build_asset_graph_from_records(
        [{"id": a, "name": "A", "floor_level": 2}, {"id": b, "name": "B"}], []
    )
    assert graph.nodes[str(a)]["floor_level"] == 2
    assert graph.nodes[str(b)]["floor_level"] == 0
