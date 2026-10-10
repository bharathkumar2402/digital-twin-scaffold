"""Degrading-engine telemetry simulator calibrated from C-MAPSS (Phase 3b, task 3b.8).

One simulated engine draws a life length from the real engine-life distribution and a fixed
offset per channel from the real between-engine spread. Each cycle, every channel reads
`level + offset + end_shift * progress(age / life) + noise`, where `progress` is the shared
drift curve and the noise is the real within-engine spread, all measured from the C-MAPSS
training trajectories. Limitations: a single operating regime (the calibrated subset), and
channel noise is independent across channels, whereas real sensors are correlated.

Demo only; never an input to model training (CLAUDE.md rule 8).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from app.ml.cmapss_profile import COLUMNS, SENSOR_COLUMNS
from app.services.ml.dataset_mapping import (
    CMAPSS_OP_SETTING_TYPES,
    CMAPSS_OP_SETTING_UNIT,
    CMAPSS_SENSOR_MAP,
)

CHANNELS: tuple[str, ...] = (*COLUMNS[2:5], *SENSOR_COLUMNS)


def channel_telemetry(column: str) -> tuple[str, str]:
    """(sensor_type, unit) the project schema uses for a C-MAPSS column."""
    if column in CMAPSS_OP_SETTING_TYPES:
        return CMAPSS_OP_SETTING_TYPES[column], CMAPSS_OP_SETTING_UNIT
    info = CMAPSS_SENSOR_MAP[column]
    return info.sensor_type, info.unit


@dataclass(frozen=True)
class SimEngine:
    life_cycles: int
    offsets: dict[str, float]


def draw_engine(calibration: dict[str, Any], rng: np.random.Generator) -> SimEngine:
    eng = calibration["engines"]
    quantiles = sorted((int(q), v) for q, v in eng["life_cycles_quantiles"].items())
    life = float(
        np.interp(rng.uniform(0, 100), [q for q, _ in quantiles], [v for _, v in quantiles])
    )
    offsets = {
        c: float(rng.normal(0.0, eng["channels"][c]["between_engine_std"])) for c in CHANNELS
    }
    return SimEngine(life_cycles=max(2, round(life)), offsets=offsets)


def cycle_values(
    calibration: dict[str, Any], engine: SimEngine, age: int, rng: np.random.Generator
) -> dict[str, float]:
    """All 24 channel values at engine age `age` cycles (age >= 1)."""
    if age < 1:
        raise ValueError("age must be >= 1")
    eng = calibration["engines"]
    health = min(1.0, (age - 1) / (engine.life_cycles - 1))
    progress = float(np.interp(health, eng["progress_fractions"], eng["progress_curve"]))
    values = {}
    for col in CHANNELS:
        ch = eng["channels"][col]
        values[col] = (
            ch["level"]
            + engine.offsets[col]
            + ch["end_shift"] * progress
            + float(rng.normal(0.0, ch["within_engine_std"]))
        )
    return values
