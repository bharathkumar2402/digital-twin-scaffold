"""Offline Isolation Forest vibration-anomaly training script (Phase 3b, task 6).

Reads the committed IMS vibration-feature CSVs (task 3b.5), runs leave-one-test-out
evaluation against two single-feature threshold baselines, refuses to publish a detector
that misses the quality gates, and stores the versioned model in MinIO. Never trains on
simulator output. Run with:

    python -m scripts.train_vibration_model
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from app.ml.ims_profile import CITATION, DATASET_NAME, SOURCE
from app.ml.vibration_train import (
    TESTS,
    TrainedVibrationModel,
    load_series,
    save_vibration_model_to_minio,
    train_vibration_model,
)
from scripts.build_vibration_features import DEFAULT_FEATURES_DIR, features_path

logger = logging.getLogger("train_vibration_model")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features-dir", type=Path, default=DEFAULT_FEATURES_DIR)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--min-detected-bearings",
        type=int,
        default=3,
        help="Refuse to publish unless this many of the 4 failing bearings are detected "
        "(leave-one-test-out, alarm outside the early-life window)",
    )
    parser.add_argument(
        "--max-false-alarm-channel-fraction",
        type=float,
        default=0.25,
        help="Refuse to publish if more than this share of channels false-alarm early",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Train and evaluate without uploading artifacts to MinIO",
    )
    return parser.parse_args(argv)


def _dataset_metadata(features_dir: Path) -> dict[str, Any]:
    digests = {
        features_path(features_dir, t).name: hashlib.sha256(
            features_path(features_dir, t).read_bytes()
        ).hexdigest()
        for t in TESTS
    }
    archive_sha = None
    profile = features_dir / "profile.json"
    if profile.is_file():
        archive_sha = json.loads(profile.read_text()).get("archive_sha256")
    return {
        "dataset_name": DATASET_NAME,
        "dataset_source": SOURCE,
        "dataset_citation": CITATION,
        "dataset_license": "NASA open data (terms unverified; see docs/DATASETS.md)",
        "dataset_version": "ims_bearings_2026-10",
        "archive_sha256": archive_sha,
        "feature_file_sha256": digests,
    }


def run(args: argparse.Namespace) -> TrainedVibrationModel:
    features_dir = Path(args.features_dir)
    missing = [
        features_path(features_dir, t).name
        for t in TESTS
        if not features_path(features_dir, t).is_file()
    ]
    if missing:
        raise SystemExit(
            f"Missing {missing} in {features_dir}. "
            "Run `python -m scripts.build_vibration_features`."
        )

    trained = train_vibration_model(
        load_series(features_dir), seed=args.seed, dataset_metadata=_dataset_metadata(features_dir)
    )
    loto = trained.metrics["leave_one_test_out"]
    for name, result in loto.items():
        s = result["summary"]
        lead = s["median_lead_hours"]
        logger.info(
            "%-20s detected %d/%d | median lead %s h | false-alarm channels %d/%d",
            name,
            s["detected_after_healthy_window"],
            s["failing_bearings"],
            f"{lead:.0f}" if lead is not None else "n/a",
            s["false_alarm_channels"],
            s["total_channels"],
        )

    forest = loto["isolation_forest"]["summary"]
    if forest["detected_after_healthy_window"] < args.min_detected_bearings:
        raise SystemExit(
            f"Refusing to publish: detected {forest['detected_after_healthy_window']} bearings, "
            f"below --min-detected-bearings {args.min_detected_bearings}"
        )
    if forest["false_alarm_channel_fraction"] > args.max_false_alarm_channel_fraction:
        raise SystemExit(
            f"Refusing to publish: false-alarm channel fraction "
            f"{forest['false_alarm_channel_fraction']:.2f} above "
            f"--max-false-alarm-channel-fraction {args.max_false_alarm_channel_fraction}"
        )

    if not args.no_upload:
        version = save_vibration_model_to_minio(trained)
        logger.info("Saved vibration model %s to MinIO", version)
    else:
        logger.info("Skipped MinIO upload (--no-upload set)")
    return trained


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run(parse_args())
