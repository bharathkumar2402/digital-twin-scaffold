"""Builds RUL training/test matrices from C-MAPSS tables using `window_features`.

Every row keeps its engine (`units`) so the training task (3b.3) can split by engine -
rows from one engine must never land on both sides of a train/validation split.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.ml.cmapss_profile import COLUMNS
from app.ml.rul_features import RulFeatureConfig, window_features

_UNIT = COLUMNS.index("unit")


@dataclass(frozen=True)
class RulDataset:
    """Feature matrix with capped labels (`y`), uncapped labels (`y_true`), and provenance."""

    X: np.ndarray
    y: np.ndarray
    y_true: np.ndarray
    units: np.ndarray
    cycles: np.ndarray
    feature_names: list[str]


def _engine_blocks(table: np.ndarray) -> list[np.ndarray]:
    units = table[:, _UNIT].astype(int)
    return [table[units == u] for u in np.unique(units)]


def build_train_dataset(train_table: np.ndarray, config: RulFeatureConfig) -> RulDataset:
    """One row per engine per cycle; label = remaining cycles to that engine's failure."""
    feats: list[np.ndarray] = []
    y_true: list[float] = []
    units: list[int] = []
    cycles: list[int] = []
    for block in _engine_blocks(train_table):
        last_cycle = int(block[-1, 1])
        for i in range(block.shape[0]):
            start = max(0, i + 1 - config.long_window)
            feats.append(window_features(block[start : i + 1], config))
            y_true.append(float(last_cycle - int(block[i, 1])))
            units.append(int(block[i, _UNIT]))
            cycles.append(int(block[i, 1]))
    truth = np.asarray(y_true)
    return RulDataset(
        X=np.vstack(feats),
        y=np.minimum(truth, config.rul_cap),
        y_true=truth,
        units=np.asarray(units),
        cycles=np.asarray(cycles),
        feature_names=config.feature_names,
    )


def build_test_dataset(
    test_table: np.ndarray, rul_vector: np.ndarray, config: RulFeatureConfig
) -> RulDataset:
    """One row per test engine (its final window); labels come from the RUL file."""
    blocks = _engine_blocks(test_table)
    if len(blocks) != rul_vector.size:
        raise ValueError(
            f"RUL file has {rul_vector.size} entries but test table has {len(blocks)} engines"
        )
    feats = [window_features(b[-config.long_window :], config) for b in blocks]
    return RulDataset(
        X=np.vstack(feats),
        y=np.minimum(rul_vector, config.rul_cap),
        y_true=rul_vector.astype(np.float64),
        units=np.asarray([int(b[0, _UNIT]) for b in blocks]),
        cycles=np.asarray([int(b[-1, 1]) for b in blocks]),
        feature_names=config.feature_names,
    )
