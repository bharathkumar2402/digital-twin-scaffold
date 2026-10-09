"""CVRP Route Solver Tool for Agent 4 — Route Optimization (PROJECT_PLAN.md §4.3).

Executes Capacitated Vehicle Routing Problem (CVRP) dispatch optimization using Google OR-Tools.
Enforces the non-negotiable 5-second solver time limit (Rule 7), incorporates technician
skill matching, shift capacity constraints, and generates validated RouteOptimizationOutput.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from app.agents.tools.distance_matrix_tool import (
    DEFAULT_WALKING_SPEED_METERS_PER_MINUTE,
    build_distance_and_time_matrices,
    get_or_create_coordinate,
)
from app.schemas.agent_outputs.route_optimization import (
    RouteOptimizationOutput,
    RouteStop,
    TechnicianRoute,
)

logger = logging.getLogger("agents.tools.route_solver")

DEFAULT_TECHNICIANS: list[dict[str, Any]] = [
    {
        "technician_id": "tech_alpha",
        "technician_name": "Jordan Lee",
        "skills": ["mechanical", "electrical", "hvac", "inspection", "general"],
        "max_shift_minutes": 480.0,
        "depot": (0.0, 0.0),
    },
]

MULTI_TECHNICIAN_ROSTER: list[dict[str, Any]] = [
    {
        "technician_id": "tech_alpha",
        "technician_name": "Jordan Lee",
        "skills": ["mechanical", "electrical", "hvac", "inspection", "general"],
        "max_shift_minutes": 480.0,
        "depot": (0.0, 0.0),
    },
    {
        "technician_id": "tech_beta",
        "technician_name": "Alex Rivera",
        "skills": ["mechanical", "hydraulic", "lubrication", "inspection", "general"],
        "max_shift_minutes": 480.0,
        "depot": (0.0, 0.0),
    },
]


def solve_cvrp_routes(
    *,
    facility_id: uuid.UUID,
    tasks: list[dict[str, Any]],
    asset_coordinates: dict[str, tuple[float, float]] | None = None,
    technicians: list[dict[str, Any]] | None = None,
    time_limit_seconds: float = 5.0,
    walking_speed_meters_per_minute: float = DEFAULT_WALKING_SPEED_METERS_PER_MINUTE,
) -> RouteOptimizationOutput:
    """Solves technician dispatch CVRP using Google OR-Tools with strict 5s time limit."""
    coord_map = asset_coordinates or {}
    techs = technicians if technicians is not None else DEFAULT_TECHNICIANS
    num_vehicles = len(techs)

    if not tasks:
        # Edge case: No maintenance tasks scheduled
        return RouteOptimizationOutput(
            optimization_id=uuid.uuid4(),
            facility_id=facility_id,
            solver_status="OPTIMAL",
            solver_duration_seconds=0.01,
            technician_routes=[
                TechnicianRoute(
                    technician_id=t["technician_id"],
                    technician_name=t["technician_name"],
                    assigned_stops=[],
                    total_travel_minutes=0.0,
                    total_service_minutes=0.0,
                    total_route_duration_minutes=0.0,
                )
                for t in techs
            ],
            unassigned_task_ids=[],
            total_distance_meters=0.0,
            confidence=1.0,
        )

    if num_vehicles == 0:
        # Edge case: No technicians available
        task_ids = [str(t.get("item_id", f"task_{idx}")) for idx, t in enumerate(tasks)]
        return RouteOptimizationOutput(
            optimization_id=uuid.uuid4(),
            facility_id=facility_id,
            solver_status="NO_SOLUTION_FOUND",
            solver_duration_seconds=0.01,
            technician_routes=[],
            unassigned_task_ids=task_ids,
            total_distance_meters=0.0,
            confidence=0.0,
        )

    # 1. Prepare locations: Depot (0.0, 0.0) at index 0, followed by task asset locations
    depot_coord = (0.0, 0.0)
    locations: list[tuple[float, float]] = [depot_coord]
    task_service_minutes: list[float] = [0.0]

    for idx, task in enumerate(tasks):
        asset_id_raw = task.get("asset_id")
        coord = get_or_create_coordinate(str(asset_id_raw), coord_map, fallback_index=idx)
        locations.append(coord)

        # Service duration in minutes (gt=0.0)
        hours = float(task.get("estimated_duration_hours", 1.0) or 1.0)
        duration_mins = max(5.0, round(hours * 60.0, 1))
        task_service_minutes.append(duration_mins)

    num_locations = len(locations)

    # 2. Build pairwise distance and travel-time matrices
    dist_matrix, travel_time_matrix = build_distance_and_time_matrices(
        locations, speed_meters_per_minute=walking_speed_meters_per_minute
    )

    # Integer scale factor for OR-Tools solver (e.g. 1 unit = 0.01 minutes)
    scale_factor = 100

    # 3. Initialize OR-Tools Routing Model
    manager = pywrapcp.RoutingIndexManager(num_locations, num_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    def transit_and_service_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        travel_m = travel_time_matrix[from_node][to_node]
        service_m = task_service_minutes[from_node]
        total_m = travel_m + service_m
        return int(total_m * scale_factor)

    callback_index = routing.RegisterTransitCallback(transit_and_service_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(callback_index)

    # 4. Add Shift Duration Capacity Dimension
    max_shift_units = int(max(t.get("max_shift_minutes", 480.0) for t in techs) * scale_factor)
    routing.AddDimension(
        callback_index,
        slack_max=60 * scale_factor,  # allow up to 60 mins waiting/slack
        capacity=max_shift_units,
        fix_start_cumul_to_zero=True,
        name="Time",
    )
    time_dimension = routing.GetDimensionOrDie("Time")

    # 5. Allow dropping tasks with penalty so solver finds best partial solution if overloaded
    drop_penalty = 1_000_000
    for node_idx in range(1, num_locations):
        routing.AddDisjunction([manager.NodeToIndex(node_idx)], drop_penalty)

    # 6. Apply Technician Skill Matching
    for node_idx in range(1, num_locations):
        task = tasks[node_idx - 1]
        req_skills = set(task.get("required_technician_skills") or [])
        if req_skills:
            allowed_vehicles: list[int] = []
            for v_idx, tech in enumerate(techs):
                tech_skills = set(tech.get("skills") or [])
                # Match if technician has all required skills or any required skill
                if req_skills.issubset(tech_skills) or ("general" in tech_skills):
                    allowed_vehicles.append(v_idx)
            if allowed_vehicles and len(allowed_vehicles) < num_vehicles:
                node_var_index = manager.NodeToIndex(node_idx)
                routing.VehicleVar(node_var_index).SetValues([-1] + allowed_vehicles)

    # 7. Configure Search Parameters with 5-second hard time limit (Rule 7)
    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_params.time_limit.seconds = int(max(1, min(10, int(time_limit_seconds))))

    # 8. Solve CVRP
    start_time = time.monotonic()
    solution = routing.SolveWithParameters(search_params)
    solver_duration = round(min(10.0, max(0.01, time.monotonic() - start_time)), 2)

    # 9. Extract Solution
    status_code = routing.status()
    if status_code == 1:
        solver_status: Any = "OPTIMAL"
    elif status_code == 2:
        solver_status = "TIME_LIMIT_REACHED"
    elif solution is not None:
        solver_status = "FEASIBLE"
    else:
        solver_status = "NO_SOLUTION_FOUND"

    assigned_task_set: set[str] = set()
    technician_routes: list[TechnicianRoute] = []
    total_travel_distance_meters = 0.0

    if solution is not None:
        for v_idx, tech in enumerate(techs):
            assigned_stops: list[RouteStop] = []
            index = routing.Start(v_idx)
            prev_node = 0
            current_time_minutes = 0.0
            vehicle_travel_minutes = 0.0
            vehicle_service_minutes = 0.0
            stop_num = 1

            while not routing.IsEnd(index):
                node = manager.IndexToNode(index)
                if node != 0:
                    task = tasks[node - 1]
                    task_id = str(task.get("item_id", f"task_{node}"))
                    assigned_task_set.add(task_id)

                    travel_dist = dist_matrix[prev_node][node]
                    travel_time = travel_time_matrix[prev_node][node]
                    total_travel_distance_meters += travel_dist
                    vehicle_travel_minutes += travel_time

                    arrival_time = round(current_time_minutes + travel_time, 1)
                    service_time = task_service_minutes[node]
                    vehicle_service_minutes += service_time

                    assigned_stops.append(
                        RouteStop(
                            stop_number=stop_num,
                            asset_id=uuid.UUID(str(task.get("asset_id"))),
                            task_id=task_id,
                            estimated_arrival_minutes=arrival_time,
                            service_duration_minutes=service_time,
                            travel_time_from_previous_minutes=round(travel_time, 1),
                        )
                    )
                    current_time_minutes = arrival_time + service_time
                    stop_num += 1

                prev_node = node
                index = solution.Value(routing.NextVar(index))

            # Return travel to depot
            return_travel = travel_time_matrix[prev_node][0]
            vehicle_travel_minutes += return_travel
            total_travel_distance_meters += dist_matrix[prev_node][0]

            technician_routes.append(
                TechnicianRoute(
                    technician_id=tech["technician_id"],
                    technician_name=tech["technician_name"],
                    assigned_stops=assigned_stops,
                    total_travel_minutes=round(vehicle_travel_minutes, 1),
                    total_service_minutes=round(vehicle_service_minutes, 1),
                    total_route_duration_minutes=round(
                        vehicle_travel_minutes + vehicle_service_minutes, 1
                    ),
                )
            )

    unassigned_task_ids = [
        str(t.get("item_id", f"task_{i+1}"))
        for i, t in enumerate(tasks)
        if str(t.get("item_id", f"task_{i+1}")) not in assigned_task_set
    ]

    total_tasks_count = len(tasks)
    assigned_count = len(assigned_task_set)
    assignment_ratio = assigned_count / total_tasks_count if total_tasks_count > 0 else 1.0

    if solver_status == "NO_SOLUTION_FOUND":
        confidence = 0.20
    elif unassigned_task_ids:
        confidence = round(max(0.40, 0.90 * assignment_ratio), 2)
    elif solver_status == "OPTIMAL":
        confidence = 0.96
    else:
        confidence = 0.90

    return RouteOptimizationOutput(
        optimization_id=uuid.uuid4(),
        facility_id=facility_id,
        solver_status=solver_status,
        solver_duration_seconds=solver_duration,
        technician_routes=technician_routes,
        unassigned_task_ids=unassigned_task_ids,
        total_distance_meters=round(total_travel_distance_meters, 1),
        confidence=confidence,
    )
