"""Offline training, evaluation, and MinIO versioning for the RUL regressor (Phase 3b).

Protocol (keeps the test set honest):
  1. Group K-fold CV *by engine* on the training engines - no engine ever appears on both
     sides of a split - to estimate error and choose the number of boosting rounds.
  2. Refit on all training engines with that round count.
  3. Evaluate exactly once on the official test set. The test set is only read by the
     evaluation step; nothing is fitted or tuned on it.

Two baselines are reported beside the model so the sensors have to earn their keep: the
training-median predictor and a model that only sees the engine's cycle count.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import sklearn
import xgboost
from sklearn.model_selection import GroupKFold
from xgboost import XGBRegressor

from app.core.config import settings
from app.core.storage import get_minio_client
from app.ml.rul_dataset import RulDataset
from app.ml.rul_features import RulFeatureConfig
from app.ml.train import _get_json, _put_json

MODEL_PREFIX = "rul_model"

DEFAULT_PARAMS: dict[str, Any] = {
    "learning_rate": 0.05,
    "max_depth": 5,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 5,
    "reg_lambda": 1.0,
}
MAX_ROUNDS = 800
EARLY_STOPPING_ROUNDS = 30

LIMITATIONS = (
    "Trained on simulated NASA C-MAPSS turbofan engines (not factory equipment); "
    "demonstrates run-to-failure learning on a real, cited benchmark. Labels are capped "
    "at rul_cap cycles."
)


def latest_pointer_key(subset: str) -> str:
    return f"{MODEL_PREFIX}/{subset}/latest.json"


def nasa_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """PHM08 asymmetric score: late predictions (pred > true) are penalized more."""
    d = np.asarray(y_pred, dtype=np.float64) - np.asarray(y_true, dtype=np.float64)
    return float(np.where(d < 0, np.exp(-d / 13.0) - 1.0, np.exp(d / 10.0) - 1.0).sum())


def regression_metrics(
    y_true: np.ndarray, y_capped: np.ndarray, y_pred: np.ndarray
) -> dict[str, float]:
    """Metrics vs the true (uncapped) RUL, plus RMSE vs the capped training target."""
    err = y_pred - y_true
    return {
        "rmse_true": float(np.sqrt(np.mean(err**2))),
        "rmse_capped": float(np.sqrt(np.mean((y_pred - y_capped) ** 2))),
        "mae_true": float(np.mean(np.abs(err))),
        "mean_error_true": float(np.mean(err)),  # > 0 means predicting late (dangerous side)
        "nasa_score": nasa_score(y_true, y_pred),
    }


@dataclass(frozen=True)
class TrainedRulModel:
    model: XGBRegressor
    config: RulFeatureConfig
    metrics: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


def _new_regressor(seed: int, n_estimators: int, early_stopping: bool) -> XGBRegressor:
    return XGBRegressor(
        n_estimators=n_estimators,
        random_state=seed,
        n_jobs=1,
        tree_method="hist",
        objective="reg:squarederror",
        early_stopping_rounds=EARLY_STOPPING_ROUNDS if early_stopping else None,
        **DEFAULT_PARAMS,
    )


def _clip(pred: np.ndarray, cap: int) -> np.ndarray:
    return np.clip(pred, 0.0, float(cap))


def cross_validate_by_engine(
    train: RulDataset, cap: int, *, seed: int, n_splits: int = 5
) -> tuple[dict[str, float], int]:
    """Group K-fold CV by engine. Returns (metrics, recommended boosting rounds)."""
    if np.unique(train.units).size < n_splits:
        raise ValueError(f"need at least {n_splits} engines for {n_splits}-fold CV")
    fold_rmse: list[float] = []
    best_rounds: list[int] = []
    for fit_idx, val_idx in GroupKFold(n_splits=n_splits).split(train.X, train.y, train.units):
        assert not set(train.units[fit_idx]) & set(train.units[val_idx]), "engine leaked"
        model = _new_regressor(seed, MAX_ROUNDS, early_stopping=True)
        model.fit(
            train.X[fit_idx],
            train.y[fit_idx],
            eval_set=[(train.X[val_idx], train.y[val_idx])],
            verbose=False,
        )
        pred = _clip(model.predict(train.X[val_idx]), cap)
        fold_rmse.append(float(np.sqrt(np.mean((pred - train.y[val_idx]) ** 2))))
        best_rounds.append(int(model.best_iteration) + 1)
    rounds = max(10, int(np.mean(best_rounds) * 1.1))
    return (
        {
            "cv_rmse_capped_mean": float(np.mean(fold_rmse)),
            "cv_rmse_capped_std": float(np.std(fold_rmse)),
            "cv_folds": float(n_splits),
        },
        rounds,
    )


def train_rul_model(
    train: RulDataset,
    test: RulDataset,
    config: RulFeatureConfig,
    *,
    seed: int = 0,
    n_splits: int = 5,
    dataset_metadata: dict[str, Any] | None = None,
) -> TrainedRulModel:
    """Run the full protocol: CV by engine -> refit -> single test evaluation + baselines."""
    cv_metrics, rounds = cross_validate_by_engine(
        train, config.rul_cap, seed=seed, n_splits=n_splits
    )

    model = _new_regressor(seed, rounds, early_stopping=False)
    model.fit(train.X, train.y, verbose=False)

    # --- the only place the test set is read ---
    pred = _clip(model.predict(test.X), config.rul_cap)
    test_metrics = regression_metrics(test.y_true, test.y, pred)

    median_pred = np.full(test.y.shape, float(np.median(train.y)))
    cycle_col = train.feature_names.index("cycle")
    cycle_model = _new_regressor(seed, rounds, early_stopping=False)
    cycle_model.fit(train.X[:, [cycle_col]], train.y, verbose=False)
    cycle_pred = _clip(cycle_model.predict(test.X[:, [cycle_col]]), config.rul_cap)

    metrics: dict[str, Any] = {
        **cv_metrics,
        **{f"test_{k}": v for k, v in test_metrics.items()},
        "baseline_median": regression_metrics(test.y_true, test.y, median_pred),
        "baseline_cycle_only": regression_metrics(test.y_true, test.y, cycle_pred),
        "n_estimators": rounds,
        "train_rows": int(train.X.shape[0]),
        "train_engines": int(np.unique(train.units).size),
        "test_engines": int(test.X.shape[0]),
    }
    metadata: dict[str, Any] = {
        **(dataset_metadata or {}),
        "model_type": "XGBRegressor",
        "target": f"remaining useful life in cycles, capped at {config.rul_cap}",
        "rul_cap": config.rul_cap,
        "feature_count": len(config.feature_names),
        "hyperparameters": {**DEFAULT_PARAMS, "n_estimators": rounds},
        "seed": seed,
        "cv_splits": n_splits,
        "trained_at": datetime.now(UTC).isoformat(),
        "library_versions": {
            "xgboost": xgboost.__version__,
            "scikit-learn": sklearn.__version__,
            "numpy": np.__version__,
        },
        "limitations": LIMITATIONS,
    }
    return TrainedRulModel(model=model, config=config, metrics=metrics, metadata=metadata)


def _model_bytes(model: XGBRegressor) -> bytes:
    return bytes(model.get_booster().save_raw(raw_format="ubj"))


def save_rul_model_to_minio(trained: TrainedRulModel, subset: str) -> str:
    """Write a content-addressed version of the model and repoint the subset's latest.json."""
    model_bytes = _model_bytes(trained.model)
    content_hash = hashlib.sha256(model_bytes).hexdigest()[:8]
    version = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{content_hash}"

    client = get_minio_client()
    bucket = settings.minio_models_bucket
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)

    prefix = f"{MODEL_PREFIX}/{subset}/{version}"
    metadata = {**trained.metadata, "model_version": version, "subset": subset}
    _put_json(client, bucket, f"{prefix}/feature_config.json", trained.config.to_dict())
    _put_json(client, bucket, f"{prefix}/feature_names.json", trained.config.feature_names)
    _put_json(client, bucket, f"{prefix}/metrics.json", trained.metrics)
    _put_json(client, bucket, f"{prefix}/metadata.json", metadata)
    client.put_object(
        bucket, f"{prefix}/model.ubj", io.BytesIO(model_bytes), length=len(model_bytes)
    )
    _put_json(client, bucket, latest_pointer_key(subset), {"version": version})
    return version


def load_rul_model(
    subset: str, version: str | None = None
) -> tuple[XGBRegressor, RulFeatureConfig, dict[str, Any], dict[str, Any]]:
    """Load (model, feature config, metrics, metadata); `version=None` follows latest.json."""
    client = get_minio_client()
    bucket = settings.minio_models_bucket
    if version is None:
        pointer = _get_json(client, bucket, latest_pointer_key(subset))
        assert isinstance(pointer, dict)
        version = str(pointer["version"])
    prefix = f"{MODEL_PREFIX}/{subset}/{version}"

    config_json = _get_json(client, bucket, f"{prefix}/feature_config.json")
    metrics = _get_json(client, bucket, f"{prefix}/metrics.json")
    metadata = _get_json(client, bucket, f"{prefix}/metadata.json")
    assert isinstance(config_json, dict)
    assert isinstance(metrics, dict)
    assert isinstance(metadata, dict)

    response = client.get_object(bucket, f"{prefix}/model.ubj")
    try:
        model_bytes = response.read()
    finally:
        response.close()
        response.release_conn()
    model = XGBRegressor()
    model.load_model(bytearray(model_bytes))
    return model, RulFeatureConfig.from_dict(config_json), metrics, metadata


def model_bytes(trained: TrainedRulModel) -> bytes:
    """Serialized model bytes (for determinism / test-set isolation checks)."""
    return _model_bytes(trained.model)
