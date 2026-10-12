"""The stateful live machine simulator (Phase L task L.2): realism, determinism, faults, storyline.

These are property-style checks over thousands of ticks and several seeds: the simulator exists
to feed a risk model trained on coherent real AI4I snapshots, so the failure to guard against is
output that looks plausible per sensor but is physically incoherent or non-repeatable.
"""

import ast
import math
from pathlib import Path

import numpy as np
import pytest

from app.ml.live_machine_sim import (
    AIR,
    FAULTS,
    PROC,
    SENSORS,
    SPEED,
    TORQUE,
    WEAR,
    FleetSim,
    Storyline,
    calibrate_from_csv,
)

BACKEND = Path(__file__).resolve().parents[2]
CSV = BACKEND / "data" / "raw" / "ai4i2020.csv"

pytestmark = pytest.mark.skipif(not CSV.exists(), reason="needs the real AI4I 2020 file")

# Three starting points: healthy, tool already worn, and a record already in failure territory.
STARTS = {
    "OK-1": {AIR: 298.1, PROC: 308.6, SPEED: 1551.0, TORQUE: 42.8, WEAR: 10.0},
    "OK-2": {AIR: 301.2, PROC: 311.5, SPEED: 1480.0, TORQUE: 36.0, WEAR: 150.0},
    "BAD-1": {AIR: 298.0, PROC: 308.2, SPEED: 1300.0, TORQUE: 60.0, WEAR: 202.0},
}


@pytest.fixture(scope="module")
def stats():
    return calibrate_from_csv(CSV)


def _fleet(stats, seed=0, **kw) -> FleetSim:
    kw.setdefault("storyline", Storyline(mode="off", machine="OK-1"))
    return FleetSim(STARTS, stats, seed=seed, tick_seconds=15, **kw)


def _run(sim: FleetSim, ticks: int) -> list[dict]:
    return [sim.step() for _ in range(ticks)]


# ---- calibration comes from the real data ---------------------------------------------------


def test_calibration_matches_the_published_dataset_profile(stats) -> None:
    # numbers from docs/DATASETS.md "Data profile" (10,000 real rows)
    assert stats.low[WEAR] == 0 and stats.high[WEAR] == 253
    assert stats.low[TORQUE] == pytest.approx(3.8) and stats.high[TORQUE] == pytest.approx(76.6)
    assert stats.low[SPEED] == 1168 and stats.high[SPEED] == 2886
    assert (stats.diff_low, stats.diff_high) == (pytest.approx(7.6), pytest.approx(12.1))
    assert stats.std[AIR] == pytest.approx(2.0, abs=0.01)
    assert stats.diff_std == pytest.approx(1.0, abs=0.01)
    assert stats.speed_slope < 0, "torque and speed are negatively correlated in the real data"
    assert stats.air_phi > 0.9, "air temperature is a slow walk in the real data"


# ---- realism of the healthy stream ----------------------------------------------------------


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_values_stay_inside_the_datasets_ranges(stats, seed: int) -> None:
    for out in _run(_fleet(stats, seed), 3000):
        for values in out.values():
            for sensor in SENSORS:
                assert stats.low[sensor] <= values[sensor] <= stats.high[sensor], sensor


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_temperature_difference_stays_in_the_datasets_range(stats, seed: int) -> None:
    for out in _run(_fleet(stats, seed), 3000):
        for key in ("OK-1", "OK-2"):
            diff = out[key][PROC] - out[key][AIR]
            assert stats.diff_low - 0.01 <= diff <= stats.diff_high + 0.01


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_tool_wear_never_decreases(stats, seed: int) -> None:
    last = {k: v[WEAR] for k, v in STARTS.items()}
    for out in _run(_fleet(stats, seed), 4000):
        for key, values in out.items():
            assert values[WEAR] >= last[key], f"{key} wear went backwards"
            last[key] = values[WEAR]


def test_tool_wear_advances_at_the_configured_rate(stats) -> None:
    sim = _fleet(stats, wear_min_per_hour=60.0)  # 60 min of wear per hour = 0.25 min per 15 s
    outs = _run(sim, 240)  # one hour
    assert outs[-1]["OK-1"][WEAR] - STARTS["OK-1"][WEAR] == pytest.approx(60.0, abs=0.5)


def test_torque_and_speed_stay_negatively_correlated_per_machine(stats) -> None:
    outs = _run(_fleet(stats, 5), 3000)
    torque = [o["OK-1"][TORQUE] for o in outs]
    speed = [o["OK-1"][SPEED] for o in outs]
    assert np.corrcoef(torque, speed)[0, 1] < -0.5


def test_a_machine_keeps_its_own_operating_point(stats) -> None:
    """Coherent per machine over time: each stays near where it started, not near the fleet mean."""
    outs = _run(_fleet(stats, 6), 2000)
    for key in ("OK-1", "OK-2"):
        mean_air = np.mean([o[key][AIR] for o in outs])
        assert abs(mean_air - STARTS[key][AIR]) < 1.0, key
    assert (
        abs(np.mean([o["OK-1"][AIR] for o in outs]) - np.mean([o["OK-2"][AIR] for o in outs])) > 2
    )


def test_a_healthy_stream_almost_never_trips_the_live_z3_anomaly_check(stats) -> None:
    """Replays the live check (z of each reading against ALL prior readings, >= 10 samples, |z| > 3,
    the rule in `anomaly_detection_service`) over 24 simulated hours: a healthy fleet must not bury
    the demo in alerts. Allowed: well under one false alert per simulated hour."""
    sim = _fleet(stats, 7, wear_min_per_hour=0.0)
    outs = _run(sim, 5760)  # 24 h at 15 s
    flagged = total = 0
    for key in STARTS:
        for sensor in (AIR, PROC, SPEED, TORQUE):
            x = np.array([o[key][sensor] for o in outs])
            csum, csq = np.cumsum(x), np.cumsum(x * x)
            for i in range(200, len(x)):
                mean = csum[i - 1] / i
                var = (csq[i - 1] - i * mean * mean) / (i - 1)
                z = abs(x[i] - mean) / math.sqrt(var) if var > 0 else 0.0
                flagged += z > 3.0
                total += 1
    assert flagged / 24 < 1.0, f"{flagged} false alerts in 24 h over {total} readings"


def test_every_step_reports_exactly_the_five_sensors_for_every_machine(stats) -> None:
    out = _fleet(stats).step()
    assert set(out) == set(STARTS)
    assert all(set(v) == set(SENSORS) for v in out.values())
    assert all(math.isfinite(x) for v in out.values() for x in v.values())


# ---- determinism ----------------------------------------------------------------------------


def test_same_seed_gives_the_identical_stream(stats) -> None:
    a = _run(_fleet(stats, 42), 500)
    b = _run(_fleet(stats, 42), 500)
    assert a == b


def test_different_seeds_give_different_streams(stats) -> None:
    assert _run(_fleet(stats, 1), 50) != _run(_fleet(stats, 2), 50)


def test_machines_do_not_share_noise(stats) -> None:
    outs = _run(_fleet(stats, 9), 300)
    a = np.diff([o["OK-1"][TORQUE] for o in outs])
    b = np.diff([o["OK-2"][TORQUE] for o in outs])
    assert abs(np.corrcoef(a, b)[0, 1]) < 0.3


# ---- faults drive the published AI4I failure rules --------------------------------------------


def _after_fault(stats, fault: str, ticks: int = 40):
    sim = _fleet(stats, 3, wear_min_per_hour=0.0)
    sim.inject("OK-1", fault, duration_s=15 * 20)
    return _run(sim, ticks)[-1]["OK-1"]


def test_overstrain_fault_crosses_the_wear_times_torque_rule(stats) -> None:
    v = _after_fault(stats, "overstrain")
    assert v[WEAR] * v[TORQUE] > 11000  # AI4I OSF threshold (L variant)


def test_tool_wear_fault_enters_the_failure_band(stats) -> None:
    assert 200 <= _after_fault(stats, "tool_wear")[WEAR] <= 240  # AI4I TWF band


def test_overheat_fault_narrows_the_temperature_difference_at_low_speed(stats) -> None:
    v = _after_fault(stats, "overheat")
    assert v[PROC] - v[AIR] < 8.6 and v[SPEED] < 1380  # AI4I HDF rule


def test_power_fault_exceeds_the_power_limit(stats) -> None:
    v = _after_fault(stats, "power")
    assert v[TORQUE] * v[SPEED] * 2 * math.pi / 60 > 9000  # AI4I PWF rule


def test_a_fault_ramps_instead_of_jumping(stats) -> None:
    sim = _fleet(stats, 3, wear_min_per_hour=0.0)
    sim.inject("OK-1", "overstrain", duration_s=15 * 20)
    torque = np.array([o["OK-1"][TORQUE] for o in _run(sim, 25)])
    blocks = [torque[i : i + 5].mean() for i in range(0, 20, 5)]  # smooths the per-tick noise
    assert blocks == sorted(blocks), f"the climb must be gradual and monotone: {blocks}"
    assert torque[0] < 50 < torque[-1]
    assert blocks[1] - blocks[0] < 10, "no single-step jump to the failure state"


def test_fault_is_held_after_the_ramp(stats) -> None:
    sim = _fleet(stats, 3, wear_min_per_hour=0.0)
    sim.inject("OK-1", "overstrain", duration_s=15 * 10)
    outs = _run(sim, 300)
    for o in outs[-50:]:
        assert o["OK-1"][WEAR] * o["OK-1"][TORQUE] > 11000


def test_clear_recovers_the_operating_point_but_never_the_wear(stats) -> None:
    sim = _fleet(stats, 3, wear_min_per_hour=0.0)
    sim.inject("OK-1", "overstrain", duration_s=15 * 5)
    _run(sim, 30)
    worn = sim.machines["OK-1"].op[WEAR]
    sim.clear("OK-1")
    after = _run(sim, 100)[-1]["OK-1"]
    assert after[WEAR] >= worn
    assert after[TORQUE] < 50  # back near its healthy 42.8 Nm


def test_sensor_dropout_removes_only_that_machine_and_keeps_its_place(stats) -> None:
    sim = _fleet(stats, 3, wear_min_per_hour=60.0)
    before = sim.step()["OK-2"][WEAR]
    sim.inject("OK-2", "sensor_dropout")
    for out in _run(sim, 100):
        assert "OK-2" not in out and {"OK-1", "BAD-1"} <= set(out)
    sim.clear("OK-2")
    resumed = sim.step()["OK-2"][WEAR]
    assert resumed > before + 20, "the machine kept wearing while silent"


def test_bad_inputs_are_rejected_with_clear_errors(stats) -> None:
    sim = _fleet(stats)
    with pytest.raises(KeyError):
        sim.inject("NOPE", "overstrain")
    with pytest.raises(ValueError, match="unknown fault"):
        sim.inject("OK-1", "meltdown")
    with pytest.raises(KeyError):
        sim.clear("NOPE")
    with pytest.raises(ValueError, match="storyline"):
        FleetSim(STARTS, stats, storyline=Storyline(mode="sometimes"))
    with pytest.raises(ValueError, match="not in the fleet"):
        FleetSim(STARTS, stats, storyline=Storyline(mode="once", machine="AC-2"))
    with pytest.raises(ValueError, match="tick_seconds"):
        FleetSim(STARTS, stats, tick_seconds=0, storyline=Storyline(mode="off"))
    assert set(FAULTS) == {"overstrain", "tool_wear", "overheat", "power", "sensor_dropout"}


# ---- storyline ------------------------------------------------------------------------------


def _story(stats, mode: str, **kw) -> FleetSim:
    story = Storyline(
        mode=mode, machine="OK-1", delay_s=15 * 10, duration_s=15 * 20, hold_s=15 * 30
    )
    return FleetSim(STARTS, stats, seed=1, tick_seconds=15, storyline=story, **kw)


def test_storyline_off_never_touches_the_machine(stats) -> None:
    sim = _story(stats, "off", wear_min_per_hour=0.0)
    outs = _run(sim, 600)
    assert sim.events == []
    assert max(o["OK-1"][TORQUE] for o in outs) < 55


def test_storyline_once_starts_on_time_exactly_once_and_ends_failing(stats) -> None:
    sim = _story(stats, "once")
    outs = _run(sim, 800)
    starts = [e for e in sim.events if "storyline start" in e[1]]
    assert [t for t, _ in starts] == [10]  # delay 150 s / 15 s
    assert len(sim.events) == 1, "no recovery and no second round in `once`"
    assert outs[9]["OK-1"][TORQUE] < 50, "nothing happens before the delay"
    final = outs[-1]["OK-1"]
    assert final[WEAR] * final[TORQUE] > 11000


def test_storyline_phases_happen_in_order_in_loop_mode(stats) -> None:
    sim = _story(stats, "loop")
    _run(sim, 2000)
    kinds = [e[1].split()[1] for e in sim.events]
    assert kinds[:4] == ["start", "recover", "start", "recover"]
    ticks = [t for t, _ in sim.events]
    assert ticks == sorted(ticks)


def test_tool_wear_only_drops_at_a_documented_tool_change(stats) -> None:
    sim = _story(stats, "loop")
    last = STARTS["OK-1"][WEAR]
    for _ in range(2000):
        before = len(sim.events)
        out = sim.step()
        wear = out["OK-1"][WEAR]
        if wear < last:
            assert any("tool change" in e[1] for e in sim.events[before:]), "undocumented drop"
        last = wear


def test_storyline_leaves_the_other_machines_alone(stats) -> None:
    sim = _story(stats, "once", wear_min_per_hour=0.0)
    outs = _run(sim, 600)
    assert max(o["OK-2"][TORQUE] for o in outs) < 45


# ---- the simulator never touches training code, the network or databases -----------------------


def test_live_machine_sim_imports_no_training_network_or_database_code() -> None:
    tree = ast.parse((BACKEND / "app" / "ml" / "live_machine_sim.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            imported.update(a.name for a in node.names)
    forbidden = {"sklearn", "xgboost", "train", "rul_train", "vibration_train", "httpx", "requests",
                 "sqlalchemy", "asyncpg", "redis", "minio", "boto3"}  # fmt: skip
    assert imported & forbidden == set()
