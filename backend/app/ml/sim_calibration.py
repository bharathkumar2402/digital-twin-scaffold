"""Simulator calibration from the real datasets (Phase 3b, task 3b.8).

Derives every parameter the degradation simulator uses from the *real* IMS bearing features
and C-MAPSS run-to-failure trajectories, and records which statistic each one came from
(CLAUDE.md rule 8). This module only ever reads the real data; the simulator output is never
an input here or to any training code (enforced by `tests/unit/test_sim_calibration.py`).

Vibration (IMS): healthy RMS level/spread across channels, and the median RMS and kurtosis
growth, relative to each channel's own healthy baseline, at fixed fractions of the run for the
documented failing bearings. The growth curve is what the simulated bearing follows.

Engine telemetry (C-MAPSS, one subset): engine life distribution, per-sensor healthy level,
within- and between-engine spread, end-of-life level, and the shared progress curve of the
drift between them.
"""

from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path
from typing import Any

import numpy as np

from app.ml.cmapss_profile import (
    COLUMNS,
    SENSOR_COLUMNS,
    parse_cmapss_table,
    train_file,
)
from app.ml.cmapss_profile import (
    DATASET_NAME as CMAPSS_NAME,
)
from app.ml.vibration_features import baseline_length
from app.ml.vibration_train import FAILING_BEARINGS, TESTS

CALIBRATION_VERSION = 1
GROWTH_FRACTIONS: tuple[float, ...] = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0)
_WINDOW = 0.025  # +/- 2.5 % of the run around each fraction
_EARLY_FRACTION = 0.2  # share of an engine's life treated as its healthy level
_END_FRACTION = 0.05
_LIFE_QUANTILES = tuple(range(0, 101, 5))
CONSTANT_STD = 1e-9
IMS_NAME = "NASA IMS Bearing Dataset"


def _read_channels(path: Path) -> dict[int, dict[str, np.ndarray]]:
    with gzip.open(path, "rt", newline="") as handle:
        rows = list(csv.DictReader(handle))
    out: dict[int, dict[str, np.ndarray]] = {}
    for channel in sorted({int(r["channel"]) for r in rows}):
        chan = sorted(
            (r for r in rows if int(r["channel"]) == channel), key=lambda r: r["timestamp"]
        )
        out[channel] = {
            "rms": np.array([float(r["rms"]) for r in chan]),
            "kurtosis": np.array([float(r["kurtosis"]) for r in chan]),
        }
    return out


def _at_fraction(values: np.ndarray, fraction: float) -> float:
    """Median of `values` in a +/- window around `fraction` of the run (end-inclusive)."""
    n = len(values)
    lo = max(0, int(np.floor((fraction - _WINDOW) * n)))
    hi = min(n, max(lo + 1, int(np.ceil((fraction + _WINDOW) * n))))
    return float(np.median(values[lo:hi]))


def calibrate_vibration(features_dir: Path) -> dict[str, Any]:
    baseline_rms: list[float] = []
    baseline_cv: list[float] = []
    baseline_kurt: list[float] = []
    rms_growth: list[list[float]] = []
    kurt_growth: list[list[float]] = []
    for test in TESTS:
        path = features_dir / f"{test}_vibration_features.csv.gz"
        failing = {c for chans in FAILING_BEARINGS[test].values() for c in chans}
        for channel, series in _read_channels(path).items():
            n_base = baseline_length(len(series["rms"]))
            base_rms = series["rms"][:n_base]
            base_kurt = series["kurtosis"][:n_base]
            baseline_rms.append(float(base_rms.mean()))
            baseline_cv.append(float(base_rms.std() / base_rms.mean()))
            baseline_kurt.append(float(base_kurt.mean()))
            if channel in failing:
                rms_growth.append(
                    [
                        _at_fraction(series["rms"], f) / float(base_rms.mean())
                        for f in GROWTH_FRACTIONS
                    ]
                )
                kurt_growth.append(
                    [
                        _at_fraction(series["kurtosis"], f) / float(base_kurt.mean())
                        for f in GROWTH_FRACTIONS
                    ]
                )
    log_rms = np.log(baseline_rms)
    return {
        "source": f"{IMS_NAME} - {', '.join(TESTS)}",
        "baseline_fraction_rule": "first max(10, 15 %) of each channel's snapshots",
        "healthy_rms_median": float(np.median(baseline_rms)),
        "healthy_rms_log_spread_across_channels": float(log_rms.std()),
        "healthy_rms_within_channel_cv": float(np.median(baseline_cv)),
        "healthy_kurtosis_median": float(np.median(baseline_kurt)),
        "growth_fractions": list(GROWTH_FRACTIONS),
        "rms_growth_ratio": [float(x) for x in np.median(np.array(rms_growth), axis=0)],
        "kurtosis_growth_ratio": [float(x) for x in np.median(np.array(kurt_growth), axis=0)],
        "n_failing_channels": len(rms_growth),
        "n_channels": len(baseline_rms),
    }


def calibrate_engines(data_dir: Path, subset: str) -> dict[str, Any]:
    table = parse_cmapss_table((data_dir / train_file(subset)).read_text(), train_file(subset))
    unit_col, cycle_col = COLUMNS.index("unit"), COLUMNS.index("cycle")
    columns = [*COLUMNS[2:5], *SENSOR_COLUMNS]
    idx = [COLUMNS.index(c) for c in columns]
    lives: list[int] = []
    early_means, early_stds, end_means = [], [], []
    progress_samples: list[np.ndarray] = []
    for unit in np.unique(table[:, unit_col]):
        eng = table[table[:, unit_col] == unit]
        eng = eng[np.argsort(eng[:, cycle_col])]
        n = len(eng)
        lives.append(n)
        data = eng[:, idx]
        n_early = max(5, int(_EARLY_FRACTION * n))
        n_end = max(3, int(_END_FRACTION * n))
        early_means.append(data[:n_early].mean(axis=0))
        early_stds.append(data[:n_early].std(axis=0))
        end_means.append(data[-n_end:].mean(axis=0))
        progress_samples.append(
            np.array([data[max(0, min(n - 1, int(f * (n - 1))))] for f in GROWTH_FRACTIONS[1:]])
        )
    early = np.array(early_means)
    end = np.array(end_means)
    level = early.mean(axis=0)
    between = early.std(axis=0)
    within = np.array(early_stds).mean(axis=0)
    shift = end.mean(axis=0) - level
    # Shared progress curve: fraction of the total drift reached at each run fraction,
    # median over sensors whose drift is large compared with their healthy spread.
    informative = np.abs(shift) > 3 * np.maximum(within, CONSTANT_STD)
    curves = []
    mean_traj = np.mean(progress_samples, axis=0)
    for j in np.flatnonzero(informative):
        curves.append((mean_traj[:, j] - level[j]) / shift[j])
    progress = np.median(np.array(curves), axis=0) if curves else np.linspace(0.0, 1.0, 7)
    progress = np.clip(np.concatenate([[0.0], progress]), 0.0, None)
    progress = np.maximum.accumulate(progress)
    progress = progress / progress[-1] if progress[-1] > 0 else progress
    return {
        "source": f"{CMAPSS_NAME} - train_{subset}.txt",
        "subset": subset,
        "n_engines": len(lives),
        "life_cycles_quantiles": {str(q): float(np.percentile(lives, q)) for q in _LIFE_QUANTILES},
        "channels": {
            col: {
                "level": float(level[i]),
                "between_engine_std": float(between[i]),
                "within_engine_std": float(within[i]),
                "end_shift": float(shift[i]),
            }
            for i, col in enumerate(columns)
        },
        "progress_fractions": list(GROWTH_FRACTIONS),
        "progress_curve": [float(x) for x in progress],
        "n_informative_sensors": int(informative.sum()),
    }


def calibrate(features_dir: Path, cmapss_dir: Path, subset: str) -> dict[str, Any]:
    return {
        "calibration_version": CALIBRATION_VERSION,
        "vibration": calibrate_vibration(features_dir),
        "engines": calibrate_engines(cmapss_dir, subset),
    }


def save_calibration(calibration: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(calibration, indent=2, sort_keys=True))


def load_calibration(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    if data.get("calibration_version") != CALIBRATION_VERSION:
        raise ValueError(f"unsupported calibration version in {path}")
    assert isinstance(data, dict)
    return data
