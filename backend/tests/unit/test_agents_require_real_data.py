"""Real-data mode: agents refuse to invent data (no sample-fixture fallback), and what-if
target resolution never silently simulates a different asset than the one asked about."""

import asyncio
import uuid
from typing import Any

import networkx as nx
import pytest

from app.agents.errors import MissingRealDataError, NoAssetsError, UnknownAssetError
from app.agents.maintenance_inventory import real_maintenance_inventory_callable
from app.agents.planner import real_planner_callable
from app.agents.risk_assessment import real_risk_assessment_callable
from app.agents.route_optimization import real_route_optimization_callable
from app.agents.simulation_decision import real_simulation_decision_callable
from app.agents.tools.cascade_simulator_tool import (
    parse_what_if_subject,
    resolve_simulation_target_asset,
)

FACILITY = str(uuid.uuid4())
TENANT = str(uuid.uuid4())


def _ctx(**extra: Any) -> dict[str, Any]:
    return {
        "tenant_id": TENANT,
        "facility_id": FACILITY,
        "trigger": "scheduled",
        "require_real_data": True,
        **extra,
    }


@pytest.mark.parametrize(
    "callable_",
    [
        real_planner_callable,
        real_risk_assessment_callable,
        real_maintenance_inventory_callable,
        real_route_optimization_callable,
        real_simulation_decision_callable,
    ],
)
def test_every_agent_refuses_the_sample_fixture_in_real_data_mode(callable_: Any) -> None:
    with pytest.raises(MissingRealDataError):
        asyncio.run(callable_(_ctx()))


@pytest.mark.parametrize(
    "callable_",
    [
        real_planner_callable,
        real_risk_assessment_callable,
        real_maintenance_inventory_callable,
        real_route_optimization_callable,
        real_simulation_decision_callable,
    ],
)
def test_fixture_fallback_still_works_when_real_data_is_not_required(callable_: Any) -> None:
    """The unit-test/skeleton behaviour is preserved outside real-data mode."""
    out = asyncio.run(callable_(_ctx(require_real_data=False)))
    assert isinstance(out, dict) and out


def _graph() -> nx.DiGraph:
    g = nx.DiGraph()
    ids = {name: str(uuid.uuid4()) for name in ("Pump 7", "Pump 70", "Heat Exchanger Alpha")}
    for name, node_id in ids.items():
        g.add_node(node_id, id=node_id, name=name, type="x", status="operational")
    g.graph["ids"] = ids
    return g


def test_what_if_names_the_asset_it_resolves_to() -> None:
    g = _graph()
    ids = g.graph["ids"]
    got = resolve_simulation_target_asset(
        g, user_query="What if Heat Exchanger Alpha fails?", strict=True
    )
    assert str(got) == ids["Heat Exchanger Alpha"]


def test_longest_asset_name_wins_over_a_prefix_name() -> None:
    g = _graph()
    ids = g.graph["ids"]
    got = resolve_simulation_target_asset(g, user_query="what if pump 70 fails", strict=True)
    assert str(got) == ids["Pump 70"]  # not "Pump 7"


def test_unknown_asset_in_a_what_if_raises_in_strict_mode_naming_known_assets() -> None:
    g = _graph()
    risk = [{"asset_id": g.graph["ids"]["Pump 7"], "risk_score": 99.0}]
    with pytest.raises(UnknownAssetError) as err:
        resolve_simulation_target_asset(
            g, user_query="What if the Flux Capacitor fails?", risk_scores=risk, strict=True
        )
    assert "Flux Capacitor" in str(err.value) and "Pump 7" in str(err.value)


def test_non_strict_mode_keeps_the_old_permissive_fallback() -> None:
    g = _graph()
    risk = [{"asset_id": g.graph["ids"]["Pump 7"], "risk_score": 99.0}]
    got = resolve_simulation_target_asset(
        g, user_query="What if the Flux Capacitor fails?", risk_scores=risk
    )
    assert str(got) == g.graph["ids"]["Pump 7"]


def test_a_query_that_is_not_a_what_if_still_uses_the_highest_risk_asset() -> None:
    g = _graph()
    risk = [
        {"asset_id": g.graph["ids"]["Pump 7"], "risk_score": 10.0},
        {"asset_id": g.graph["ids"]["Heat Exchanger Alpha"], "risk_score": 90.0},
    ]
    got = resolve_simulation_target_asset(
        g, user_query="Which asset worries you most?", risk_scores=risk, strict=True
    )
    assert str(got) == g.graph["ids"]["Heat Exchanger Alpha"]


def test_empty_facility_raises_instead_of_fabricating_a_root() -> None:
    with pytest.raises(NoAssetsError):
        resolve_simulation_target_asset(nx.DiGraph(), user_query=None, strict=True)


@pytest.mark.parametrize(
    ("query", "subject"),
    [
        ("What if Pump 7 fails?", "Pump 7"),
        ("what-if the Heat Exchanger Alpha goes down", "Heat Exchanger Alpha"),
        ("WHAT IF Turbine Gen 1 shuts down tomorrow", "Turbine Gen 1"),
        ("Which asset is riskiest?", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_what_if_subject(query: str | None, subject: str | None) -> None:
    assert parse_what_if_subject(query) == subject
