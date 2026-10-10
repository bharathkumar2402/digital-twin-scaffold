"""Batch-extract vibration features from the raw IMS snapshots (Phase 3b, task 5).

Streams every snapshot of each IMS test, runs `vibration_features` on every channel, and
writes one gzip CSV per test with one row per (file, channel). Features are left RAW here;
per-channel baseline normalisation is applied by the consumer (task 3b.6) so the baseline
window stays an explicit modelling choice.

    python -m scripts.build_vibration_features
"""

from __future__ import annotations

import argparse
import csv
import gzip
import logging
import sys
from pathlib import Path

from app.ml.ims_profile import iter_snapshot_paths, parse_snapshot_text, parse_snapshot_timestamp
from app.ml.vibration_features import FEATURE_NAMES, vibration_features
from scripts.acquire_ims import DEFAULT_OUTPUT_DIR, TEST_SETS

logger = logging.getLogger(__name__)

DEFAULT_FEATURES_DIR = Path(__file__).resolve().parent.parent / "data" / "processed" / "ims"
HEADER = ["file", "timestamp", "channel", *FEATURE_NAMES]


def features_path(output_dir: Path, test: str) -> Path:
    return output_dir / f"{test}_vibration_features.csv.gz"


def build_test_features(test_dir: Path, out_path: Path) -> tuple[int, dict[str, str]]:
    """Write features for every readable snapshot in `test_dir`; returns (rows, errors)."""
    paths, _ = iter_snapshot_paths(test_dir)
    errors: dict[str, str] = {}
    rows = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for i, path in enumerate(paths):
            try:
                signal = parse_snapshot_text(path.read_text(), str(path))
                per_channel = [vibration_features(signal[:, ch]) for ch in range(signal.shape[1])]
            except ValueError as exc:
                errors[path.name] = str(exc)
                continue
            stamp = parse_snapshot_timestamp(path.name).isoformat()
            for ch, feats in enumerate(per_channel, start=1):
                writer.writerow([path.name, stamp, ch, *(f"{v:.8g}" for v in feats)])
                rows += 1
            if (i + 1) % 500 == 0:
                logger.info("  %s: %d/%d snapshots", test_dir.name, i + 1, len(paths))
    return rows, errors


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Extract vibration features from IMS snapshots.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_FEATURES_DIR)
    args = parser.parse_args()

    for test in TEST_SETS:
        test_dir = args.data_dir / test
        if not test_dir.is_dir():
            logger.error("%s not found. Run `python -m scripts.acquire_ims` first.", test_dir)
            return 1
        rows, errors = build_test_features(test_dir, features_path(args.output_dir, test))
        logger.info("%s: wrote %d rows (%d unreadable files)", test, rows, len(errors))
        if errors:
            logger.warning("unreadable: %s", dict(list(errors.items())[:5]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
