"""Per-asset degradation state for the demo simulator (Phase 3b, task 3b.8).

Ties the calibrated engine telemetry (`engine_sim`) and bearing vibration (`vibration_sim`)
to one shared health trajectory per asset: an asset ages one cycle per tick, its 24 C-MAPSS
channels drift along the calibrated curve, and its accelerometer channels degrade at the same
run fraction. When an asset reaches the end of its drawn life it is replaced by a fresh one,
so a long demo keeps producing healthy-to-failing runs.

Demo only; never an input to model training (CLAUDE.md rule 8).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.ml.engine_sim import SimEngine, cycle_values, draw_engine
from app.ml.vibration_sim import draw_channel_scale, simulate_waveform


@dataclass
class _AssetState:
    engine: SimEngine
    age: int = 0
    channel_scales: dict[int, float] = field(default_factory=dict)


@dataclass(frozen=True)
class AssetTick:
    age: int
    health: float
    engine_values: dict[str, float]
    waveforms: dict[int, np.ndarray]


class DegradationSimulator:
    def __init__(
        self, calibration: dict[str, Any], *, seed: int | None = None, n_channels: int = 2
    ) -> None:
        if n_channels < 1:
            raise ValueError("n_channels must be >= 1")
        self._cal = calibration
        self._rng = np.random.default_rng(seed)
        self._n_channels = n_channels
        self._assets: dict[uuid.UUID, _AssetState] = {}

    def _new_state(self) -> _AssetState:
        return _AssetState(
            engine=draw_engine(self._cal, self._rng),
            channel_scales={
                c: draw_channel_scale(self._cal, self._rng) for c in range(self._n_channels)
            },
        )

    def tick(self, asset_id: uuid.UUID, *, with_waveforms: bool = True) -> AssetTick:
        state = self._assets.get(asset_id)
        if state is None or state.age >= state.engine.life_cycles:
            state = self._assets[asset_id] = self._new_state()
        state.age += 1
        health = min(1.0, (state.age - 1) / (state.engine.life_cycles - 1))
        waveforms = (
            {
                c: simulate_waveform(
                    self._cal, health, self._rng, channel_scale=state.channel_scales[c]
                )
                for c in range(self._n_channels)
            }
            if with_waveforms
            else {}
        )
        return AssetTick(
            age=state.age,
            health=health,
            engine_values=cycle_values(self._cal, state.engine, state.age, self._rng),
            waveforms=waveforms,
        )
