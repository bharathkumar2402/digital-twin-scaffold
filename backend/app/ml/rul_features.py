"""Feature extraction for the Remaining Useful Life (RUL) model (Phase 3b).

`window_features` is the single feature function used by BOTH training
(`rul_dataset`) and live serving, so a model trained here sees exactly the same
feature layout and math at inference time (no training/serving skew). It is
deliberately separate from `feature_vector.py`: that module's 238-column layout is
frozen by the trained risk model, and RUL uses cycle-based windows, not day-based ones.

A "cycle" is one snapshot of an asset's sensors. A window is the trailing rows up to
and including the current cycle - it never looks ahead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from app.ml.cmapss_profile import COLUMNS, SENSOR_COLUMNS

RUL_CAP = 125
SHORT_WINDOW = 5
LONG_WINDOW = 30
CONSTANT_STD_THRESHOLD = 1e-9
MIN_STD = 1e-6
# Max distance (in setting-1 / setting-3 units) to match a row to a known regime.
REGIME_MATCH_TOLERANCE = 2.0

_COL = {name: i for i, name in enumerate(COLUMNS)}
_PER_SENSOR_FEATURES = ("latest", "mean_short", "mean_long", "std_long", "slope_long")


@dataclass(frozen=True)
class RulFeatureConfig:
    """Everything fitted from training data that serving needs to reproduce features."""

    sensors: tuple[str, ...]
    regime_centers: tuple[tuple[float, float], ...]
    means: tuple[tuple[float, ...], ...]  # [regime][sensor]
    stds: tuple[tuple[float, ...], ...]  # [regime][sensor]
    rul_cap: int = RUL_CAP
    short_window: int = SHORT_WINDOW
    long_window: int = LONG_WINDOW

    @property
    def feature_names(self) -> list[str]:
        names = [f"{s}_{f}" for s in self.sensors for f in _PER_SENSOR_FEATURES]
        return [*names, "cycle", "window_len"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sensors": list(self.sensors),
            "regime_centers": [list(c) for c in self.regime_centers],
            "means": [list(m) for m in self.means],
            "stds": [list(s) for s in self.stds],
            "rul_cap": self.rul_cap,
            "short_window": self.short_window,
            "long_window": self.long_window,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RulFeatureConfig:
        return cls(
            sensors=tuple(data["sensors"]),
            regime_centers=tuple((float(c[0]), float(c[1])) for c in data["regime_centers"]),
            means=tuple(tuple(float(x) for x in m) for m in data["means"]),
            stds=tuple(tuple(float(x) for x in s) for s in data["stds"]),
            rul_cap=int(data["rul_cap"]),
            short_window=int(data["short_window"]),
            long_window=int(data["long_window"]),
        )


def _regime_keys(table: np.ndarray) -> np.ndarray:
    """Operating regime identity per row: settings 1 and 3 rounded to integers."""
    rounded = np.round(table[:, [_COL["op_setting_1"], _COL["op_setting_3"]]], decimals=0)
    return rounded + 0.0  # turns -0.0 into 0.0


def fit_config(
    train_table: np.ndarray,
    rul_cap: int = RUL_CAP,
    short_window: int = SHORT_WINDOW,
    long_window: int = LONG_WINDOW,
) -> RulFeatureConfig:
    """Fit sensor selection and per-regime scaling. Uses TRAINING data only."""
    if train_table.ndim != 2 or train_table.shape[1] != len(COLUMNS):
        raise ValueError(f"expected an (n, {len(COLUMNS)}) table, got {train_table.shape}")
    kept = tuple(
        s for s in SENSOR_COLUMNS if float(train_table[:, _COL[s]].std()) >= CONSTANT_STD_THRESHOLD
    )
    if not kept:
        raise ValueError("no non-constant sensors in training data")

    keys = _regime_keys(train_table)
    centers = sorted({(float(k[0]), float(k[1])) for k in keys})
    means: list[tuple[float, ...]] = []
    stds: list[tuple[float, ...]] = []
    for center in centers:
        mask = (keys[:, 0] == center[0]) & (keys[:, 1] == center[1])
        block = train_table[mask][:, [_COL[s] for s in kept]]
        means.append(tuple(float(x) for x in block.mean(axis=0)))
        stds.append(tuple(float(max(x, MIN_STD)) for x in block.std(axis=0)))
    return RulFeatureConfig(
        sensors=kept,
        regime_centers=tuple(centers),
        means=tuple(means),
        stds=tuple(stds),
        rul_cap=rul_cap,
        short_window=short_window,
        long_window=long_window,
    )


def _assign_regimes(table: np.ndarray, config: RulFeatureConfig) -> np.ndarray:
    """Index of the nearest known regime for every row; raise for an unseen regime."""
    centers = np.asarray(config.regime_centers, dtype=np.float64)
    settings = table[:, [_COL["op_setting_1"], _COL["op_setting_3"]]]
    dist = np.linalg.norm(settings[:, None, :] - centers[None, :, :], axis=2)
    nearest = dist.argmin(axis=1)
    if (dist.min(axis=1) > REGIME_MATCH_TOLERANCE).any():
        raise ValueError(
            "operating condition not seen in training data "
            f"(known regimes: {list(config.regime_centers)})"
        )
    return nearest


def normalize_rows(table: np.ndarray, config: RulFeatureConfig) -> np.ndarray:
    """Per-regime z-score of the kept sensors for every row."""
    regimes = _assign_regimes(table, config)
    means = np.asarray(config.means)[regimes]
    stds = np.asarray(config.stds)[regimes]
    values = table[:, [_COL[s] for s in config.sensors]]
    return (values - means) / stds


def window_features(window: np.ndarray, config: RulFeatureConfig) -> np.ndarray:
    """Features for the LAST row of `window` using only rows up to and including it.

    `window` is an (n, 26) array of consecutive cycles (oldest first). Rows beyond the
    trailing `long_window` are ignored; shorter histories use what exists.
    """
    if window.ndim != 2 or window.shape[1] != len(COLUMNS) or window.shape[0] < 1:
        raise ValueError(f"expected a non-empty (n, {len(COLUMNS)}) window, got {window.shape}")
    if not np.isfinite(window).all():
        raise ValueError("window contains NaN or infinite values")

    window = window[-config.long_window :]
    z = normalize_rows(window, config)
    n = z.shape[0]
    latest = z[-1]
    mean_short = z[-config.short_window :].mean(axis=0)
    mean_long = z.mean(axis=0)
    std_long = z.std(axis=0) if n > 1 else np.zeros(z.shape[1])
    if n > 1:
        t = np.arange(n, dtype=np.float64)
        t_centered = t - t.mean()
        slope = (t_centered[:, None] * (z - mean_long)).sum(axis=0) / (t_centered**2).sum()
    else:
        slope = np.zeros(z.shape[1])
    per_sensor = np.stack([latest, mean_short, mean_long, std_long, slope], axis=1).ravel()
    return np.concatenate([per_sensor, [window[-1, _COL["cycle"]], float(n)]])
