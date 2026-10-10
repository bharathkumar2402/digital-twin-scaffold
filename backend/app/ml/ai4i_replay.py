"""Replay real AI4I 2020 records as an asset's live snapshot (demo / end-to-end tests).

The live demo has no real facility feed. Instead of inventing "failure" values, an asset's
latest snapshot can be a real AI4I record: a held-out one (the rows the risk model's own
train/test split set aside, so the model never fitted them), healthy or failing.

This only reads the real dataset and is demo-feed code: it must never feed model training
(CLAUDE.md rule 8; enforced by the import-scan tests).
"""

from __future__ import annotations

import csv
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.model_selection import train_test_split

from app.schemas.requests.telemetry import SensorReadingIn
from app.services.ml.dataset_mapping import map_ai4i2020_row

# Must equal the split `app.ml.train.train_risk_model` uses (same test_size / seed /
# stratification), so these are the rows the published model was evaluated on.
DEFAULT_SEED = 0
DEFAULT_TEST_SIZE = 0.2


def heldout_rows(
    csv_path: Path | str, *, seed: int = DEFAULT_SEED, test_size: float = DEFAULT_TEST_SIZE
) -> list[dict[str, str]]:
    """The AI4I rows held out from training, in file order."""
    with Path(csv_path).open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    y = np.array([int(map_ai4i2020_row(r).is_failure) for r in rows])
    _, test_idx = train_test_split(
        np.arange(len(rows)), test_size=test_size, random_state=seed, stratify=y
    )
    return [rows[i] for i in sorted(int(i) for i in test_idx)]


def pick_row(rows: list[dict[str, str]], *, failure: bool, nth: int = 0) -> dict[str, str]:
    """The `nth` row (file order) whose machine-failure label equals `failure`."""
    matching = [r for r in rows if bool(map_ai4i2020_row(r).is_failure) is failure]
    if nth >= len(matching):
        raise IndexError(f"only {len(matching)} matching held-out rows, asked for #{nth}")
    return matching[nth]


def replay_readings(
    row: dict[str, str], asset_id: uuid.UUID, stamp: datetime
) -> list[SensorReadingIn]:
    """The record's sensor readings, all stamped `stamp`, attributed to `asset_id`."""
    record = map_ai4i2020_row(row, base_timestamp=stamp, interval_seconds=0)
    return [
        SensorReadingIn(
            asset_id=asset_id,
            sensor_type=r.sensor_type,
            value=r.value,
            unit=r.unit,
            timestamp=stamp,
        )
        for r in record.readings
    ]


def describe(row: dict[str, str]) -> dict[str, Any]:
    """Label facts for display: failure flag and active failure modes."""
    record = map_ai4i2020_row(row)
    return {
        "udi": record.udi,
        "product_id": record.product_id,
        "failure": record.is_failure,
        "failure_modes": record.failure_modes,
    }
