"""Offline XGBoost risk-model training script (issue 3.2).

Standalone script (not part of the FastAPI app, no DB session) that generates a
synthetic dataset with injected failure patterns (`app.ml.synthetic_data`), trains
an XGBoost classifier on it, and stores the versioned model artifacts in MinIO
(`app.ml.train.save_model_to_minio`). Run with:

    python -m scripts.train_risk_model --num-samples 5000 --seed 0
"""

from __future__ import annotations

import argparse
import logging

import numpy as np

from app.ml.feature_vector import vectorize
from app.ml.synthetic_data import generate_synthetic_dataset
from app.ml.train import TrainedModel, save_model_to_minio, train_risk_model

logger = logging.getLogger("train_risk_model")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--num-samples", type=int, default=5000, help="Number of synthetic assets to generate"
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--min-test-auc",
        type=float,
        default=0.75,
        help="Refuse to publish a model that doesn't clear this held-out AUC",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> TrainedModel:
    logger.info("Generating %d synthetic samples (seed=%d)", args.num_samples, args.seed)
    dataset = generate_synthetic_dataset(args.num_samples, seed=args.seed)
    X = np.stack([vectorize(f) for f in dataset.features])

    trained = train_risk_model(X, dataset.labels, seed=args.seed)
    logger.info("Trained model metrics: %s", trained.metrics)

    if trained.metrics["test_auc"] < args.min_test_auc:
        raise SystemExit(
            f"Refusing to publish: test AUC {trained.metrics['test_auc']:.3f} "
            f"below --min-test-auc {args.min_test_auc}"
        )

    version = save_model_to_minio(trained)
    logger.info("Saved model version %s to MinIO", version)
    return trained


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run(parse_args())
