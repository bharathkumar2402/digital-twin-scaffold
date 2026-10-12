"""Stateful live machine simulator for the Phase L data loop (task L.2).

Pure and deterministic: no network, no database, no clock. A `FleetSim` holds one coherent
machine per asset and advances every machine by one tick per `step()`; `scripts/live_simulator.py`
turns the output into `POST /telemetry` batches.

Why it is stateful: the risk model was trained on AI4I 2020 rows, each a single coherent snapshot
(process temperature is air temperature plus about 10 K, torque and speed are strongly negatively
correlated, tool wear only goes up). Independent noise per sensor would give meaningless scores,
so each machine keeps an operating point and mean-reverting deviations around it.

Calibration (CLAUDE.md rule 8): every statistic comes from the real `ai4i2020.csv` via
`calibrate_from_csv` - value ranges, the torque -> speed regression, the lag-1 autocorrelation of
the air temperature and temperature difference (rows are in time order), and the published AI4I
failure rules the faults target. Two parameters cannot be identified from a cross-sectional
dataset and are demo parameters, documented in `docs/DATASETS.md`: `WITHIN_MACHINE_FRACTION`
(how much of the dataset's spread one machine shows over time) and the tool-wear rate. This module
only reads real data; its output is never an input to training (enforced by
`tests/unit/test_live_machine_sim.py`).
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

AIR, PROC, SPEED, TORQUE, WEAR = (
    "air_temperature",
    "process_temperature",
    "rotational_speed",
    "torque",
    "tool_wear",
)
SENSORS: tuple[str, ...] = (AIR, PROC, SPEED, TORQUE, WEAR)
UNITS: dict[str, str] = {AIR: "K", PROC: "K", SPEED: "rpm", TORQUE: "Nm", WEAR: "min"}
_CSV_COLUMNS: dict[str, str] = {
    AIR: "Air temperature [K]",
    PROC: "Process temperature [K]",
    SPEED: "Rotational speed [rpm]",
    TORQUE: "Torque [Nm]",
    WEAR: "Tool wear [min]",
}

# Demo parameters (not identifiable from the cross-sectional dataset; see module docstring).
# One machine varies by this share of the dataset's std over time. Chosen by measuring score
# stability with the real risk model (2026-10-11): at 0.25 six machines flipped across the 50-point
# line within 150 ticks, healthy ones jumping from ~1 to ~97; at 0.05 only the two machines the demo
# places at ~58-59 on purpose (CT-2, CNC-2) still flicker and the critical AC-1 stays critical.
WITHIN_MACHINE_FRACTION = 0.05
DEFAULT_WEAR_MIN_PER_HOUR = 1.0
# Healthy noise is clipped to this many (series) standard deviations, so almost only faults and the
# storyline exceed the live anomaly check's z > 3 threshold and raise alerts (measured: about one
# false alert per 35 simulated minutes across 44 machines, see tests/unit/test_live_machine_sim.py).
# The dataset's own record-to-record persistence of air temperature is 0.9994 (a slow random walk);
# at one record per tick that drifts so slowly that a machine's recent history is a poor baseline
# and the live check flags the drift itself. The persistence is therefore capped (demo parameter).
MAX_PERSISTENCE = 0.7
NOISE_CLIP_SIGMA = 2.0

FAULTS: tuple[str, ...] = ("overstrain", "tool_wear", "overheat", "power", "sensor_dropout")
STORYLINES: tuple[str, ...] = ("off", "once", "loop")


@dataclass(frozen=True)
class Ai4iStats:
    """Everything the simulator needs, derived from the real AI4I 2020 file."""

    low: dict[str, float]
    high: dict[str, float]
    std: dict[str, float]
    diff_low: float
    diff_high: float
    diff_std: float
    air_phi: float
    diff_phi: float
    speed_slope: float
    speed_intercept: float
    speed_resid_std: float


def calibrate_from_csv(path: Path | str) -> Ai4iStats:
    with Path(path).open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    cols = {s: np.array([float(r[_CSV_COLUMNS[s]]) for r in rows]) for s in SENSORS}
    diff = cols[PROC] - cols[AIR]

    def lag1(x: np.ndarray) -> float:
        x = x - x.mean()
        return float(np.dot(x[:-1], x[1:]) / np.dot(x, x))

    design = np.vstack([cols[TORQUE], np.ones(len(rows))]).T
    (slope, intercept), *_ = np.linalg.lstsq(design, cols[SPEED], rcond=None)
    resid = cols[SPEED] - design @ np.array([slope, intercept])
    return Ai4iStats(
        low={s: float(cols[s].min()) for s in SENSORS},
        high={s: float(cols[s].max()) for s in SENSORS},
        std={s: float(cols[s].std()) for s in SENSORS},
        diff_low=float(diff.min()),
        diff_high=float(diff.max()),
        diff_std=float(diff.std()),
        air_phi=lag1(cols[AIR]),
        diff_phi=lag1(diff),
        speed_slope=float(slope),
        speed_intercept=float(intercept),
        speed_resid_std=float(resid.std()),
    )


# Published AI4I failure rules (Matzka 2020) that each fault drives a machine toward.
# value = operating-point target; machines move there linearly over the fault's duration.
FAULT_TARGETS: dict[str, dict[str, float]] = {
    # OSF: tool wear x torque above 11000 (L variant)
    "overstrain": {WEAR: 232.0, TORQUE: 62.0},
    # TWF: tool wear in the 200-240 minute band
    "tool_wear": {WEAR: 225.0},
    # HDF: temperature difference below 8.6 K with speed below 1380 rpm
    "overheat": {"diff": 8.2, SPEED: 1300.0},
    # PWF: power above 9000 W (torque x speed)
    "power": {TORQUE: 70.0, SPEED: 1500.0},
}


@dataclass
class _Ramp:
    fault: str
    start_tick: int
    duration_ticks: int
    origin: dict[str, float]  # operating point when the fault began


@dataclass
class MachineSim:
    key: str
    start: dict[str, float]  # the five AI4I sensors the machine starts from
    stats: Ai4iStats
    rng: np.random.Generator
    wear_per_tick: float
    op: dict[str, float] = field(default_factory=dict)  # air, diff, torque, speed_bias, wear
    dev: dict[str, float] = field(default_factory=dict)  # mean-reverting deviations
    ramp: _Ramp | None = None
    dropped_out: bool = False
    hold_after_ramp: bool = True

    def __post_init__(self) -> None:
        s = self.stats
        self.op = {
            AIR: self.start[AIR],
            "diff": self.start[PROC] - self.start[AIR],
            TORQUE: self.start[TORQUE],
            # speed is torque-coupled; keep this machine's offset from the fitted line
            "speed_bias": self.start[SPEED]
            - (s.speed_intercept + s.speed_slope * self.start[TORQUE]),
            WEAR: self.start[WEAR],
        }
        self.dev = {AIR: 0.0, "diff": 0.0, TORQUE: 0.0, "speed_resid": 0.0}
        self._healthy_op = dict(self.op)

    # -- faults ------------------------------------------------------------------------------
    def begin_fault(self, fault: str, tick: int, duration_ticks: int) -> None:
        origin = dict(self.op)
        origin[SPEED] = self._speed_op(self.op)
        self.ramp = _Ramp(fault, tick, max(1, duration_ticks), origin)

    def clear_fault(self) -> None:
        """Recover the operating point (not the tool wear: wear never goes backwards)."""
        wear = self.op[WEAR]
        self.op = dict(self._healthy_op)
        self.op[WEAR] = wear
        self.ramp = None

    def replace_tool(self) -> None:
        """The one documented way tool wear drops: a tool change, used by the looping storyline."""
        self.op[WEAR] = 0.0
        self._healthy_op[WEAR] = 0.0

    def _speed_op(self, op: dict[str, float]) -> float:
        s = self.stats
        return s.speed_intercept + s.speed_slope * op[TORQUE] + op["speed_bias"]

    def _ramp_progress(self, tick: int) -> float:
        assert self.ramp is not None
        return min(1.0, max(0.0, (tick - self.ramp.start_tick + 1) / self.ramp.duration_ticks))

    # -- one tick ----------------------------------------------------------------------------
    def step(self, tick: int) -> dict[str, float]:
        s = self.stats
        op = self.op
        speed_override: float | None = None

        if self.ramp is not None:
            p = self._ramp_progress(tick)
            origin = self.ramp.origin
            for name, goal in FAULT_TARGETS[self.ramp.fault].items():
                if name == SPEED:
                    speed_override = origin[SPEED] + p * (goal - origin[SPEED])
                elif name == WEAR:
                    op[WEAR] = max(op[WEAR], origin[WEAR] + p * (goal - origin[WEAR]))
                else:
                    op[name] = origin[name] + p * (goal - origin[name])

        # the tool always wears; the ramp above can only push it further up
        op[WEAR] = min(s.high[WEAR], op[WEAR] + self.wear_per_tick)

        sd = {
            AIR: WITHIN_MACHINE_FRACTION * s.std[AIR],
            "diff": WITHIN_MACHINE_FRACTION * s.diff_std,
            TORQUE: WITHIN_MACHINE_FRACTION * s.std[TORQUE],
            "speed_resid": WITHIN_MACHINE_FRACTION * s.speed_resid_std,
        }
        phi = {
            AIR: min(s.air_phi, MAX_PERSISTENCE),
            "diff": min(s.diff_phi, MAX_PERSISTENCE),
            TORQUE: 0.0,
            "speed_resid": 0.0,
        }
        for name in self.dev:
            eps = float(np.clip(self.rng.standard_normal(), -NOISE_CLIP_SIGMA, NOISE_CLIP_SIGMA))
            dev = phi[name] * self.dev[name] + sd[name] * math.sqrt(1.0 - phi[name] ** 2) * eps
            limit = NOISE_CLIP_SIGMA * sd[name]
            self.dev[name] = float(np.clip(dev, -limit, limit))

        air = op[AIR] + self.dev[AIR]
        diff = op["diff"] + self.dev["diff"]
        torque = op[TORQUE] + self.dev[TORQUE]
        if speed_override is not None:
            speed = speed_override + self.dev["speed_resid"]
        else:
            speed = s.speed_intercept + s.speed_slope * torque + op["speed_bias"]
            speed += self.dev["speed_resid"]

        clamp = {
            AIR: (s.low[AIR], s.high[AIR]),
            PROC: (s.low[PROC], s.high[PROC]),
            SPEED: (s.low[SPEED], s.high[SPEED]),
            TORQUE: (s.low[TORQUE], s.high[TORQUE]),
        }
        air = float(np.clip(air, *clamp[AIR]))
        # a faulty machine may leave the healthy 7.6-12.1 K band (the overheat fault does)
        floor = min(s.diff_low, diff) if self.ramp is not None else s.diff_low
        diff = float(np.clip(diff, floor, s.diff_high))
        proc = float(np.clip(air + diff, *clamp[PROC]))
        return {
            AIR: round(air, 2),
            PROC: round(proc, 2),
            SPEED: round(float(np.clip(speed, *clamp[SPEED])), 1),
            TORQUE: round(float(np.clip(torque, *clamp[TORQUE])), 2),
            WEAR: round(op[WEAR], 3),
        }


@dataclass(frozen=True)
class Storyline:
    mode: str = "once"  # off | once | loop
    machine: str = "AC-2"
    fault: str = "overstrain"
    delay_s: float = 180.0
    duration_s: float = 900.0
    hold_s: float = 600.0  # loop only: how long the failing state is held before recovery


class FleetSim:
    """All machines, advanced together. `step()` returns {machine key: {sensor: value}}.

    A machine that is in `sensor_dropout` is simply absent from the result.
    """

    def __init__(
        self,
        starts: dict[str, dict[str, float]],
        stats: Ai4iStats,
        *,
        seed: int = 0,
        tick_seconds: float = 15.0,
        storyline: Storyline | None = None,
        wear_min_per_hour: float = DEFAULT_WEAR_MIN_PER_HOUR,
    ) -> None:
        story = storyline or Storyline()
        if story.mode not in STORYLINES:
            raise ValueError(f"storyline must be one of {STORYLINES}")
        if story.fault not in FAULT_TARGETS:
            raise ValueError(f"storyline fault must be one of {tuple(FAULT_TARGETS)}")
        if tick_seconds <= 0:
            raise ValueError("tick_seconds must be positive")
        self.tick_seconds = tick_seconds
        self.storyline = story
        self.tick = 0
        self.events: list[tuple[int, str]] = []
        wear_per_tick = wear_min_per_hour * tick_seconds / 3600.0
        self.machines = {
            key: MachineSim(
                key=key,
                start=start,
                stats=stats,
                rng=np.random.default_rng([seed, index]),
                wear_per_tick=wear_per_tick,
            )
            for index, (key, start) in enumerate(sorted(starts.items()))
        }
        if story.mode != "off" and story.machine not in self.machines:
            raise ValueError(f"storyline machine {story.machine!r} is not in the fleet")
        self._story_active = False
        self._story_begin_tick = 0
        self._next_story_tick = round(story.delay_s / tick_seconds)

    # -- controls ----------------------------------------------------------------------------
    def inject(self, key: str, fault: str, *, duration_s: float = 120.0) -> None:
        if key not in self.machines:
            raise KeyError(f"unknown machine {key!r}")
        if fault not in FAULTS:
            raise ValueError(f"unknown fault {fault!r}; choose from {FAULTS}")
        machine = self.machines[key]
        if fault == "sensor_dropout":
            machine.dropped_out = True
        else:
            machine.begin_fault(fault, self.tick, round(duration_s / self.tick_seconds))
        self.events.append((self.tick, f"inject {key} {fault}"))

    def clear(self, key: str) -> None:
        if key not in self.machines:
            raise KeyError(f"unknown machine {key!r}")
        machine = self.machines[key]
        machine.dropped_out = False
        machine.clear_fault()
        self.events.append((self.tick, f"clear {key}"))

    # -- storyline ---------------------------------------------------------------------------
    def _advance_storyline(self) -> None:
        story = self.storyline
        if story.mode == "off":
            return
        machine = self.machines[story.machine]
        ramp_ticks = max(1, round(story.duration_s / self.tick_seconds))
        if not self._story_active:
            if self.tick >= self._next_story_tick:
                machine.begin_fault(story.fault, self.tick, ramp_ticks)
                self._story_active = True
                self._story_begin_tick = self.tick
                self.events.append((self.tick, f"storyline start {story.machine} {story.fault}"))
        elif story.mode == "loop":
            recover_tick = (
                self._story_begin_tick + ramp_ticks + round(story.hold_s / self.tick_seconds)
            )
            if self.tick >= recover_tick:
                machine.clear_fault()
                machine.replace_tool()
                self._story_active = False
                self._next_story_tick = self.tick + round(story.delay_s / self.tick_seconds)
                self.events.append((self.tick, f"storyline recover {story.machine} (tool change)"))

    def step(self) -> dict[str, dict[str, float]]:
        self._advance_storyline()
        out: dict[str, dict[str, float]] = {}
        for key, machine in self.machines.items():
            values = machine.step(self.tick)  # always advances, so a dropout keeps its place
            if not machine.dropped_out:
                out[key] = values
        self.tick += 1
        return out
