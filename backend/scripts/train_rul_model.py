"""Offline XGBoost Remaining Useful Life (RUL) training script (Phase 3b, task 3).

Trains on the real NASA C-MAPSS data (never on simulator output), evaluates with
group CV by engine plus a single official-test-set evaluation, refuses to publish a
model that misses the quality gates, and stores versioned artifacts in MinIO. Run with:

    python -m scripts.train_rul_model --subset FD001
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

from app.ml.cmapss_profile import (
    CITATION,
    DATASET_NAME,
    SOURCE,
    load_and_profile_cmapss,
    parse_cmapss_table,
    parse_rul_vector,
    rul_file,
    test_file,
    train_file,
)
from app.ml.rul_dataset import build_test_dataset, build_train_dataset
from app.ml.rul_features import RUL_CAP, fit_config
from app.ml.rul_train import TrainedRulModel, save_rul_model_to_minio, train_rul_model
from app.services.ml.dataset_mapping import CMAPSS_DATASET_VERSION
from scripts.acquire_cmapss import DEFAULT_OUTPUT_DIR

logger = logging.getLogger("train_rul_model")

SUBSETS = ("FD001", "FD002", "FD003", "FD004")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subset", choices=SUBSETS, default="FD001")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--rul-cap", type=int, default=RUL_CAP)
    parser.add_argument(
        "--max-test-rmse",
        type=float,
        default=20.0,
        help="Refuse to publish a model whose official-test RMSE (vs true RUL) exceeds this",
    )
    parser.add_argument(
        "--max-test-nasa-score",
        type=float,
        default=1500.0,
        help="Refuse to publish a model whose official-test NASA score exceeds this",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Train and evaluate without uploading artifacts to MinIO",
    )
    return parser.parse_args(argv)


def _dataset_metadata(subset: str, data_dir: Path) -> dict[str, Any]:
    profile = load_and_profile_cmapss(data_dir)
    files = [train_file(subset), test_file(subset), rul_file(subset)]
    return {
        "dataset_name": DATASET_NAME,
        "dataset_source": SOURCE,
        "dataset_citation": CITATION,
        "dataset_license": "NASA open data (public domain; see docs/DATASETS.md)",
        "dataset_version": CMAPSS_DATASET_VERSION,
        "subset": subset,
        "file_sha256": {f: profile.sha256[f] for f in files},
    }


def run(args: argparse.Namespace) -> TrainedRulModel:
    data_dir = Path(args.data_dir)
    subset: str = args.subset
    for name in (train_file(subset), test_file(subset), rul_file(subset)):
        if not (data_dir / name).is_file():
            raise SystemExit(
                f"{name} not found in {data_dir}. Run `python -m scripts.acquire_cmapss` first."
            )

    train_table = parse_cmapss_table((data_dir / train_file(subset)).read_text())
    test_table = parse_cmapss_table((data_dir / test_file(subset)).read_text())
    rul = parse_rul_vector((data_dir / rul_file(subset)).read_text())

    config = fit_config(train_table, rul_cap=args.rul_cap)
    logger.info(
        "%s: %d sensors kept, %d regime(s), %d features",
        subset,
        len(config.sensors),
        len(config.regime_centers),
        len(config.feature_names),
    )
    train = build_train_dataset(train_table, config)
    test = build_test_dataset(test_table, rul, config)

    trained = train_rul_model(
        train,
        test,
        config,
        seed=args.seed,
        dataset_metadata=_dataset_metadata(subset, data_dir),
    )
    m = trained.metrics
    logger.info(
        "CV RMSE (capped) %.2f +/- %.2f | test RMSE %.2f, MAE %.2f, bias %+.2f, NASA score %.0f",
        m["cv_rmse_capped_mean"],
        m["cv_rmse_capped_std"],
        m["test_rmse_true"],
        m["test_mae_true"],
        m["test_mean_error_true"],
        m["test_nasa_score"],
    )
    logger.info(
        "Baselines (test RMSE): median %.2f, cycle-only %.2f",
        m["baseline_median"]["rmse_true"],
        m["baseline_cycle_only"]["rmse_true"],
    )

    if m["test_rmse_true"] > args.max_test_rmse:
        raise SystemExit(
            f"Refusing to publish: test RMSE {m['test_rmse_true']:.2f} "
            f"above --max-test-rmse {args.max_test_rmse}"
        )
    if m["test_nasa_score"] > args.max_test_nasa_score:
        raise SystemExit(
            f"Refusing to publish: test NASA score {m['test_nasa_score']:.0f} "
            f"above --max-test-nasa-score {args.max_test_nasa_score}"
        )

    if not args.no_upload:
        version = save_rul_model_to_minio(trained, subset)
        logger.info("Saved RUL model %s/%s to MinIO", subset, version)
    else:
        logger.info("Skipped MinIO upload (--no-upload set)")
    return trained


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run(parse_args())
