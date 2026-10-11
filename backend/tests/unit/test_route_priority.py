"""Routing must serve the most urgent work first (found by the Work tab, UI task U5).

Before this fix every task cost the same to drop, so with one 8-hour shift the solver maximised
the NUMBER of jobs: three 2-hour low-priority inspections beat one 4-hour critical repair.
These tests run the real OR-Tools solver on small problems whose right answer is known, with the
input order varied so a pass is never down to task order.
"""

import uuid
from datetime import date, datetime
from typing import Any

import pytest

from app.agents.route_optimization import real_route_optimization_callable
from app.agents.tools.route_solver_tool import (
    DEFAULT_PRIORITY,
    PRIORITY_DROP_PENALTY,
    drop_penalty_for,
    select_due_tasks,
    solve_cvrp_routes,
)

FACILITY = uuid.UUID("11111111-1111-1111-1111-111111111111")


def task(
    name: str,
    priority: str | None,
    hours: float,
    scheduled: Any = "2026-10-12",
) -> dict[str, Any]:
    t: dict[str, Any] = {
        "item_id": name,
        "asset_id": uuid.uuid4(),
        "scheduled_date": scheduled,
        "estimated_duration_hours": hours,
        "required_technician_skills": [],
    }
    if priority is not None:
        t["priority"] = priority
    return t


def coords(tasks: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    # Everything a few metres from the depot: travel is negligible, only job length matters.
    return {str(t["asset_id"]): (float(i + 1), 0.0) for i, t in enumerate(tasks)}


def routed(tasks: list[dict[str, Any]]) -> set[str]:
    result = solve_cvrp_routes(
        facility_id=FACILITY,
        tasks=tasks,
        asset_coordinates=coords(tasks),
        time_limit_seconds=1.0,
    )
    return {s.task_id for r in result.technician_routes for s in r.assigned_stops}


def rotations(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Every rotation plus the reverse, so the interesting task is first, last and in between."""
    out = [items[i:] + items[:i] for i in range(len(items))]
    out.append(list(reversed(items)))
    return out


# ---- the bug itself ---------------------------------------------------------------------------


@pytest.mark.parametrize("order", range(4))
def test_a_critical_job_is_served_before_more_numerous_short_low_priority_jobs(order: int) -> None:
    # 8 h shift. Old behaviour: 3 x 2 h lows (3 jobs) beat the 5 h critical + 1 low (2 jobs).
    items = [task("critical", "critical", 5.0)] + [task(f"low{i}", "low", 2.0) for i in range(6)]
    got = routed(rotations(items)[order])
    assert "critical" in got
    assert len(got) == 2  # the critical plus the one low that still fits


def test_the_priority_ladder_fills_the_shift_critical_then_high_then_the_rest() -> None:
    items = [
        task("low-a", "low", 2.5),
        task("high", "high", 3.0),
        task("low-b", "low", 2.5),
        task("critical", "critical", 3.0),
        task("medium", "medium", 2.5),
        task("low-c", "low", 2.5),
    ]
    for order in rotations(items):
        # 3 + 3 = 6 h; no other job (2.5 h) fits in the remaining 2 h
        assert routed(order) == {"critical", "high"}


def test_many_short_jobs_can_never_crowd_out_one_critical_job() -> None:
    items = [task("critical", "critical", 7.0)] + [task(f"low{i}", "low", 1.0) for i in range(20)]
    for order in (items, list(reversed(items))):
        assert routed(order) == {"critical"}


def test_one_critical_outweighs_two_high_jobs_when_only_one_set_fits() -> None:
    """Policy: each level is worth 10x the one below, so a critical beats two highs."""
    items = [
        task("high-1", "high", 3.5),
        task("high-2", "high", 3.5),
        task("critical", "critical", 7.0),
    ]
    for order in rotations(items):
        assert routed(order) == {"critical"}


def test_a_high_job_outweighs_a_pile_of_medium_jobs() -> None:
    items = [task("high", "high", 7.0)] + [task(f"med{i}", "medium", 2.0) for i in range(5)]
    assert routed(items) == {"high"}


# ---- it must not over-correct -----------------------------------------------------------------


def test_when_everything_fits_nothing_is_dropped_even_low_priority_work() -> None:
    items = [task(f"low{i}", "low", 1.0) for i in range(4)]
    result = solve_cvrp_routes(
        facility_id=FACILITY, tasks=items, asset_coordinates=coords(items), time_limit_seconds=1.0
    )
    assert {s.task_id for r in result.technician_routes for s in r.assigned_stops} == {
        t["item_id"] for t in items
    }
    assert result.unassigned_task_ids == []


def test_a_mixed_set_that_fits_is_served_completely() -> None:
    items = [
        task("c", "critical", 1.0),
        task("h", "high", 1.0),
        task("m", "medium", 1.0),
        task("l", "low", 1.0),
    ]
    assert routed(items) == {"c", "h", "m", "l"}


# ---- odd priorities ---------------------------------------------------------------------------


def test_a_missing_priority_counts_as_medium_so_it_beats_low_jobs() -> None:
    items = [task("no-priority", None, 5.0)] + [task(f"low{i}", "low", 2.0) for i in range(6)]
    assert "no-priority" in routed(items)


def test_an_unknown_priority_counts_as_medium_and_does_not_crash() -> None:
    items = [task("odd", "URGENT!!", 5.0)] + [task(f"low{i}", "low", 2.0) for i in range(6)]
    assert "odd" in routed(items)


def test_priority_is_case_insensitive() -> None:
    items = [task("shouty", "CRITICAL", 5.0)] + [task(f"low{i}", "low", 2.0) for i in range(6)]
    assert "shouty" in routed(items)


# ---- the penalties themselves -----------------------------------------------------------------


def test_each_level_is_at_least_ten_times_the_one_below() -> None:
    order = ["low", "medium", "high", "critical"]
    assert list(PRIORITY_DROP_PENALTY) == order
    values = [PRIORITY_DROP_PENALTY[p] for p in order]
    for lower, higher in zip(values, values[1:], strict=False):
        assert higher >= 10 * lower


def test_the_lowest_penalty_exceeds_the_most_a_route_can_cost() -> None:
    # solver units are 1/100 minute: a 480 min shift + 60 min slack = 54,000
    assert PRIORITY_DROP_PENALTY["low"] > 54_000


@pytest.mark.parametrize(
    ("priority", "expected"),
    [
        ("critical", PRIORITY_DROP_PENALTY["critical"]),
        ("High", PRIORITY_DROP_PENALTY["high"]),
        ("low", PRIORITY_DROP_PENALTY["low"]),
        (None, PRIORITY_DROP_PENALTY[DEFAULT_PRIORITY]),
        ("", PRIORITY_DROP_PENALTY[DEFAULT_PRIORITY]),
        ("nonsense", PRIORITY_DROP_PENALTY[DEFAULT_PRIORITY]),
    ],
)
def test_drop_penalty_for(priority: str | None, expected: int) -> None:
    assert drop_penalty_for({"priority": priority}) == expected


# ---- only what is due goes in one shift -------------------------------------------------------


def test_only_tasks_due_on_or_before_the_earliest_date_are_dispatched() -> None:
    tasks = [
        task("later", "low", 1.0, "2026-10-26"),
        task("today", "critical", 1.0, "2026-10-12"),
        task("tomorrow", "high", 1.0, "2026-10-13"),
        task("overdue", "high", 1.0, "2026-10-12"),
    ]
    due, dispatch = select_due_tasks(tasks)
    assert dispatch == date(2026, 10, 12)
    assert [t["item_id"] for t in due] == ["today", "overdue"]  # order preserved


def test_the_boundary_is_inclusive_and_exact() -> None:
    tasks = [task("a", "low", 1.0, "2026-10-12"), task("b", "low", 1.0, "2026-10-13")]
    due, _ = select_due_tasks(tasks)
    assert [t["item_id"] for t in due] == ["a"]


def test_dates_may_be_date_objects_datetimes_or_iso_strings() -> None:
    tasks = [
        task("obj", "low", 1.0, date(2026, 10, 12)),
        task("dt", "low", 1.0, datetime(2026, 10, 12, 18, 30)),
        task("iso", "low", 1.0, "2026-10-12T08:00:00Z"),
        task("later", "low", 1.0, "2026-10-20"),
    ]
    due, dispatch = select_due_tasks(tasks)
    assert dispatch == date(2026, 10, 12)
    assert [t["item_id"] for t in due] == ["obj", "dt", "iso"]


@pytest.mark.parametrize("bad", [None, "", "not-a-date", "2026-13-45", 12345])
def test_a_missing_or_unreadable_date_counts_as_due_so_work_is_never_silently_dropped(
    bad: Any,
) -> None:
    tasks = [
        task("good", "low", 1.0, "2026-10-12"),
        task("bad", "low", 1.0, bad),
        task("later", "low", 1.0, "2026-10-30"),
    ]
    due, dispatch = select_due_tasks(tasks)
    assert dispatch == date(2026, 10, 12)
    assert [t["item_id"] for t in due] == ["good", "bad"]


def test_when_no_date_is_readable_everything_is_dispatched() -> None:
    tasks = [task("a", "low", 1.0, None), task("b", "low", 1.0, "nope")]
    due, dispatch = select_due_tasks(tasks)
    assert dispatch is None
    assert len(due) == 2


def test_an_empty_schedule_dispatches_nothing() -> None:
    assert select_due_tasks([]) == ([], None)


# ---- the agent, end to end --------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_agent_dispatches_todays_critical_job_and_future_work_is_not_unassigned() -> None:
    schedule = (
        [task("crit-today", "critical", 4.0, "2026-10-12")]
        + [task(f"high-{i}", "high", 3.5, "2026-10-14") for i in range(3)]
        + [task(f"low-{i}", "low", 2.0, "2026-10-26") for i in range(20)]
    )
    out = await real_route_optimization_callable(
        {
            "facility_id": str(FACILITY),
            "maintenance_schedule": schedule,
            "asset_coordinates": coords(schedule),
            "time_limit_seconds": 1.0,
        }
    )
    stops = [s["task_id"] for r in out["technician_routes"] for s in r["assigned_stops"]]
    assert stops == ["crit-today"]
    assert out["unassigned_task_ids"] == []  # later jobs are scheduled, not unassigned


@pytest.mark.anyio
async def test_a_due_job_that_does_not_fit_is_unassigned_and_the_critical_one_is_kept() -> None:
    schedule = [task("crit", "critical", 5.0)] + [task(f"low-{i}", "low", 2.0) for i in range(4)]
    out = await real_route_optimization_callable(
        {
            "facility_id": str(FACILITY),
            "maintenance_schedule": schedule,
            "asset_coordinates": coords(schedule),
            "time_limit_seconds": 1.0,
        }
    )
    stops = {s["task_id"] for r in out["technician_routes"] for s in r["assigned_stops"]}
    assert "crit" in stops
    assert "crit" not in out["unassigned_task_ids"]
    assert len(out["unassigned_task_ids"]) == 3  # four lows due today, one fits beside the critical
