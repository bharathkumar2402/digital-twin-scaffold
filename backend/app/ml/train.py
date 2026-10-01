"""Trains and versions the offline XGBoost risk-classification model (issues 3.2, 3.5).

Model artifacts are stored in MinIO (`Settings.minio_models_bucket`), not committed
to the repo or the database - `risk_scores.model_version` (PROJECT_PLAN.md §5) will
reference the version string this module produces, and inference services read
the artifacts back via `load_model`.
"""

import hashlib
import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
from minio import Minio
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

from app.core.config import settings
from app.core.storage import get_minio_client
from app.ml.feature_vector import FEATURE_NAMES

MODEL_PREFIX = "risk_model"
LATEST_POINTER_KEY = f"{MODEL_PREFIX}/latest.json"

DEFAULT_DATASET_METADATA: dict[str, object] = {
    "dataset_name": "AI4I 2020 Predictive Maintenance Dataset",
    "dataset_source": "UCI Machine Learning Repository (ID: 601)",
    "dataset_citation": (
        "Matzka, S. (2020). Explainable Artificial Intelligence for "
        "Predictive Maintenance Applications. Third International "
        "Conference on Industrial Cyber-Physical Systems (ICPS)."
    ),
    "dataset_license": "Creative Commons Attribution 4.0 International (CC BY 4.0)",
    "dataset_version": "1.0 (September 2026)",
    "target_column": "Machine failure",
    "feature_count": len(FEATURE_NAMES),
}


@dataclass(frozen=True)
class TrainedModel:
    model: XGBClassifier
    metrics: dict[str, float]
    metadata: dict[str, object] | None = None


def train_risk_model(
    X: np.ndarray,
    y: np.ndarray,
    *,
    seed: int = 0,
    test_size: float = 0.2,
    scale_pos_weight: float | None = None,
    dataset_metadata: dict[str, object] | None = None,
) -> TrainedModel:
    """Fits an XGBoost binary classifier; `X` columns must line up with
    `app.ml.feature_vector.FEATURE_NAMES`. Returns the fitted model plus held-out
    metrics - including ROC-AUC, PR-AUC, precision, recall, and F1 to address
    rare failure class imbalance."""
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=seed, stratify=y
    )

    if scale_pos_weight is None:
        pos_count = int(np.sum(y_train == 1))
        neg_count = int(np.sum(y_train == 0))
        computed_spw = float(neg_count / pos_count) if pos_count > 0 else 1.0
    else:
        computed_spw = scale_pos_weight

    model = XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        scale_pos_weight=computed_spw,
        eval_metric="logloss",
        random_state=seed,
    )
    model.fit(X_train, y_train)

    test_probabilities = model.predict_proba(X_test)[:, 1]
    test_preds = (test_probabilities >= 0.5).astype(int)

    precision = float(precision_score(y_test, test_preds, zero_division=0))
    recall = float(recall_score(y_test, test_preds, zero_division=0))
    f1 = float(f1_score(y_test, test_preds, zero_division=0))
    if len(np.unique(y_test)) > 1:
        pr_auc = float(average_precision_score(y_test, test_probabilities))
        roc_auc = float(roc_auc_score(y_test, test_probabilities))
    else:
        pr_auc = 0.0
        roc_auc = 0.0

    metrics: dict[str, float] = {
        "train_samples": float(len(X_train)),
        "test_samples": float(len(X_test)),
        "test_auc": roc_auc,
        "test_pr_auc": pr_auc,
        "test_precision": precision,
        "test_recall": recall,
        "test_f1": f1,
        "test_positive_rate": float(np.mean(y_test)),
        "scale_pos_weight": float(computed_spw),
    }

    meta = dict(dataset_metadata or DEFAULT_DATASET_METADATA)
    meta["training_date"] = datetime.now(UTC).isoformat()
    meta["train_samples"] = len(X_train)
    meta["test_samples"] = len(X_test)

    return TrainedModel(model=model, metrics=metrics, metadata=meta)


def _model_bytes(model: XGBClassifier) -> bytes:
    booster = model.get_booster()
    return bytes(booster.save_raw(raw_format="ubj"))


def save_model_to_minio(trained: TrainedModel) -> str:
    """Writes `model.ubj` + `feature_names.json` + `metrics.json` + `metadata.json`
    under a content-addressed version prefix, then repoints `latest.json` at it. The version
    string embeds a UTC timestamp (for human-readable ordering) plus a short hash of
    the model bytes - two runs producing distinct models get distinct versions even
    within the same second; re-saving byte-identical model output is idempotent and
    intentionally reuses the same version rather than manufacturing a fake distinct
    one."""
    model_bytes = _model_bytes(trained.model)
    content_hash = hashlib.sha256(model_bytes).hexdigest()[:8]
    version = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{content_hash}"

    client = get_minio_client()
    bucket = settings.minio_models_bucket
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)

    version_prefix = f"{MODEL_PREFIX}/{version}"
    _put_json(client, bucket, f"{version_prefix}/feature_names.json", FEATURE_NAMES)
    _put_json(client, bucket, f"{version_prefix}/metrics.json", trained.metrics)
    if trained.metadata is not None:
        _put_json(client, bucket, f"{version_prefix}/metadata.json", trained.metadata)
    client.put_object(
        bucket,
        f"{version_prefix}/model.ubj",
        io.BytesIO(model_bytes),
        length=len(model_bytes),
    )
    _put_json(client, bucket, LATEST_POINTER_KEY, {"version": version})

    return version


def _put_json(client: Minio, bucket: str, key: str, payload: object) -> None:
    body = json.dumps(payload).encode("utf-8")
    client.put_object(bucket, key, io.BytesIO(body), length=len(body))


def _get_json(client: Minio, bucket: str, key: str) -> object:
    response = client.get_object(bucket, key)
    try:
        return json.loads(response.read())
    finally:
        response.close()
        response.release_conn()


def load_model(version: str | None = None) -> tuple[XGBClassifier, list[str], dict[str, float]]:
    """Loads a versioned model back from MinIO - `version=None` follows the
    `latest.json` pointer. Round-trip counterpart to `save_model_to_minio`, used by
    this task's own verification and by 3.3/3.6's inference service."""
    client = get_minio_client()
    bucket = settings.minio_models_bucket

    if version is None:
        pointer = _get_json(client, bucket, LATEST_POINTER_KEY)
        assert isinstance(pointer, dict)
        version = pointer["version"]

    version_prefix = f"{MODEL_PREFIX}/{version}"
    feature_names = _get_json(client, bucket, f"{version_prefix}/feature_names.json")
    metrics = _get_json(client, bucket, f"{version_prefix}/metrics.json")

    model_response = client.get_object(bucket, f"{version_prefix}/model.ubj")
    try:
        model_bytes = model_response.read()
    finally:
        model_response.close()
        model_response.release_conn()

    model = XGBClassifier()
    model.load_model(bytearray(model_bytes))

    assert isinstance(feature_names, list)
    assert isinstance(metrics, dict)
    return model, feature_names, metrics


def load_model_metadata(version: str | None = None) -> dict[str, object] | None:
    """Loads model metadata (dataset name, citation, license) for auditability."""
    client = get_minio_client()
    bucket = settings.minio_models_bucket

    if version is None:
        pointer = _get_json(client, bucket, LATEST_POINTER_KEY)
        assert isinstance(pointer, dict)
        version = pointer["version"]

    version_prefix = f"{MODEL_PREFIX}/{version}"
    try:
        meta = _get_json(client, bucket, f"{version_prefix}/metadata.json")
        return meta if isinstance(meta, dict) else None
    except Exception:
        return None
