"""Offline XGBoost risk-model training script (issues 3.2, 3.5).

Standalone script (not part of the FastAPI app, no DB session) that loads the
real AI4I 2020 Predictive Maintenance dataset (or synthetic fallback), extracts
the unified 238-dimensional feature representation, trains an XGBoost classifier,
evaluates metrics against held-out ROC-AUC and PR-AUC gates, and stores versioned
model artifacts in MinIO (`app.ml.train.save_model_to_minio`). Run with:

    python -m scripts.train_risk_model --seed 0
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np

from app.ml.feature_vector import vectorize
from app.ml.synthetic_data import generate_synthetic_dataset
from app.ml.train import (
    DEFAULT_DATASET_METADATA,
    TrainedModel,
    save_model_to_minio,
    train_risk_model,
)
from app.services.feature_engineering_service import (
    build_training_dataset_from_mapped_records,
)
from app.services.ml.dataset_mapping import map_ai4i2020_dataset

logger = logging.getLogger("train_risk_model")
DEFAULT_DATASET_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "raw" / "ai4i2020.csv"
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-path",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help="Path to the real AI4I 2020 CSV dataset (default: backend/data/raw/ai4i2020.csv)",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Train on synthetic data instead of real dataset",
    )
    parser.add_argument(
        "--num-samples",
        type=int,
        default=5000,
        help="Number of synthetic assets to generate (only used if --synthetic is set)",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--min-test-auc",
        type=float,
        default=0.80,
        help="Refuse to publish a model that doesn't clear this held-out ROC-AUC",
    )
    parser.add_argument(
        "--min-test-pr-auc",
        type=float,
        default=0.40,
        help="Refuse to publish a model that doesn't clear this held-out PR-AUC",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Train and evaluate without uploading artifacts to MinIO",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> TrainedModel:
    if args.synthetic:
        logger.info("Generating %d synthetic samples (seed=%d)", args.num_samples, args.seed)
        dataset = generate_synthetic_dataset(args.num_samples, seed=args.seed)
        X = np.stack([vectorize(f) for f in dataset.features])
        y = dataset.labels
        metadata = None
    else:
        dataset_path = Path(args.dataset_path)
        if not dataset_path.is_file():
            raise SystemExit(
                f"Dataset not found at {dataset_path}. Run scripts/acquire_dataset.py first."
            )
        logger.info("Loading AI4I 2020 dataset from %s", dataset_path)
        records = map_ai4i2020_dataset(dataset_path)
        logger.info("Building training features from %d mapped machine records", len(records))
        X, y, _ = build_training_dataset_from_mapped_records(records)
        metadata = DEFAULT_DATASET_METADATA

    trained = train_risk_model(X, y, seed=args.seed, dataset_metadata=metadata)
    logger.info("Trained model metrics: %s", trained.metrics)

    if trained.metrics["test_auc"] < args.min_test_auc:
        raise SystemExit(
            f"Refusing to publish: test ROC-AUC {trained.metrics['test_auc']:.3f} "
            f"below --min-test-auc {args.min_test_auc}"
        )
    if trained.metrics["test_pr_auc"] < args.min_test_pr_auc:
        raise SystemExit(
            f"Refusing to publish: test PR-AUC {trained.metrics['test_pr_auc']:.3f} "
            f"below --min-test-pr-auc {args.min_test_pr_auc}"
        )

    if not args.no_upload:
        version = save_model_to_minio(trained)
        logger.info("Saved model version %s to MinIO", version)
    else:
        logger.info("Skipped MinIO upload (--no-upload set)")

    return trained


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run(parse_args())

