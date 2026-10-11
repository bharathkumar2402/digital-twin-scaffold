"""The Meridian plant specification: it is the shared test bed, so it must be internally sound."""

import networkx as nx
import pytest

from scripts import demo_factory as f


def test_plant_has_three_floors_and_the_expected_scale() -> None:
    assert [fl.level for fl in f.FLOORS] == [0, 1, 2]
    assert len(f.MACHINES) == 44
    assert {lvl: len(f.machines_on(lvl)) for lvl in (0, 1, 2)} == {0: 17, 1: 16, 2: 11}
    assert len(f.LINKS) >= 55


def test_keys_and_names_are_unique_and_every_link_refers_to_a_real_machine_and_kind() -> None:
    assert len({m.key for m in f.MACHINES}) == len(f.MACHINES)
    assert len({m.name for m in f.MACHINES}) == len(f.MACHINES)  # what-if queries match by name
    for dependent, upstream, kind in f.LINKS:
        assert dependent in f.BY_KEY and upstream in f.BY_KEY, (dependent, upstream)
        assert dependent != upstream
        assert kind in f.LINK_COLORS
    assert len({(d, u) for d, u, _ in f.LINKS}) == len(f.LINKS)  # no duplicate edges


def test_no_machine_name_is_a_substring_of_another() -> None:
    """The what-if resolver matches names inside free text; a prefix name would be ambiguous."""
    names = [m.name.lower() for m in f.MACHINES]
    for a in names:
        assert not any(a != b and a in b for b in names), a


def test_dependency_graph_is_acyclic() -> None:
    graph = nx.DiGraph([(u, d) for d, u, _ in f.LINKS])
    assert nx.is_directed_acyclic_graph(graph)


def test_every_machine_stands_inside_its_area_on_its_floor() -> None:
    for m in f.MACHINES:
        areas = [a for a in f.AREAS if a.floor == m.floor and a.label == m.area]
        assert len(areas) == 1, (m.key, m.area)
        x0, y0, x1, y1 = areas[0].rect
        assert x0 + 150 <= m.x <= x1 - 150 and y0 + 150 <= m.y <= y1 - 150, m.key
        assert 0 < m.x < f.PLAN_W and 0 < m.y < f.PLAN_H


def test_machines_on_a_floor_do_not_overlap() -> None:
    for level in (0, 1, 2):
        ms = f.machines_on(level)
        for i, a in enumerate(ms):
            for b in ms[i + 1 :]:
                assert abs(a.x - b.x) >= 330 or abs(a.y - b.y) >= 330, (a.key, b.key)


def test_links_cross_floors_in_both_directions() -> None:
    crossing = [(d, u) for d, u, _ in f.LINKS if f.BY_KEY[d].floor != f.BY_KEY[u].floor]
    assert len(crossing) >= 15
    assert any(f.BY_KEY[u].floor > f.BY_KEY[d].floor for d, u in crossing)  # roof feeds ground
    assert any(f.BY_KEY[u].floor < f.BY_KEY[d].floor for d, u in crossing)  # hoist feeds assembly


def test_the_scenarios_the_demo_and_tests_rely_on_are_present() -> None:
    failing_snapshots = {m.key for m in f.MACHINES if m.snapshot[0]}
    assert {"AC-1", "CNC-2", "CT-2"} <= failing_snapshots
    assert {m.key for m in f.MACHINES if m.vibration == "failing"} == {"CWP-2", "AC-1", "CNC-2"}
    assert f.BY_KEY["CWP-2"].snapshot[0] is False  # early warning: alarm while risk is still low
    assert {m.key for m in f.MACHINES if m.rul_health is not None} == {
        "CH-2",
        "AC-1",
        "AC-2",
        "AC-3",
        "GEN-1",  # fmt: skip
    }
    assert f.BY_KEY["AC-1"].rul_health == 1.0  # at the end of its life


def test_a_failing_compressor_stops_the_robots_and_the_whole_downstream_line() -> None:
    down = f.downstream_of("AC-1")
    assert {"R-1", "R-2", "R-3", "PP-1"} <= down  # fed directly with compressed air
    assert {"EOL-1", "LT-1", "PK-1", "LB-1", "PL-1"} <= down  # and everything after them
    assert "AC-2" not in down and "AC-3" not in down  # a sibling compressor is unaffected
    assert all(f.BY_KEY[k].floor == 1 for k in down)  # all on the assembly floor: ground -> first


def test_losing_the_main_switchgear_is_a_plant_wide_event_across_all_floors() -> None:
    down = f.downstream_of("SG-1")
    assert len(down) >= 30
    assert {f.BY_KEY[k].floor for k in down} == {0, 1, 2}
    assert "TR-1" not in down  # the transformer is upstream of the switchgear, not downstream


def test_downstream_of_a_leaf_is_empty_and_an_unknown_key_is_too() -> None:
    assert f.downstream_of("PL-1") == set()
    assert f.downstream_of("NOPE") == set()


@pytest.mark.parametrize("level", [0, 1, 2])
def test_tile_prefix_is_per_floor(level: int) -> None:
    assert f.tile_prefix(level) == f"demo/meridian/L{level}"
