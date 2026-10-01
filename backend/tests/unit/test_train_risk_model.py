"""Unit tests for app.ml.train (issue 3.2) - model quality on synthetic data, and
the MinIO versioning/round-trip contract, with a fake in-memory MinIO client rather
than a real one (mirrors tests/unit/test_sandbox_storage.py's pattern)."""

import io
import json
from pathlib import Path

import numpy as np
import pytest

from app.ml import train as train_module
from app.ml.feature_vector import FEATURE_NAMES, vectorize
from app.ml.synthetic_data import generate_synthetic_dataset
from app.ml.train import (
    load_model,
    load_model_metadata,
    save_model_to_minio,
    train_risk_model,
)
from app.services.feature_engineering_service import (
    build_training_dataset_from_mapped_records,
)
from app.services.ml.dataset_mapping import map_ai4i2020_dataset
from scripts.train_risk_model import parse_args
from scripts.train_risk_model import run as run_train_script

RAW_CSV_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "raw" / "ai4i2020.csv"
)


class _FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.closed = False

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        pass


class _FakeMinioClient:
    def __init__(self) -> None:
        self.existing_buckets: set[str] = set()
        self.objects: dict[tuple[str, str], bytes] = {}

    def bucket_exists(self, bucket: str) -> bool:
        return bucket in self.existing_buckets

    def make_bucket(self, bucket: str) -> None:
        self.existing_buckets.add(bucket)

    def put_object(self, bucket: str, key: str, data: io.BytesIO, length: int) -> None:
        self.objects[(bucket, key)] = data.read()

    def get_object(self, bucket: str, key: str) -> _FakeResponse:
        return _FakeResponse(self.objects[(bucket, key)])


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeMinioClient:
    client = _FakeMinioClient()
    monkeypatch.setattr(train_module, "get_minio_client", lambda: client)
    return client


@pytest.fixture(scope="module")
def trained() -> tuple[np.ndarray, np.ndarray, train_module.TrainedModel]:
    dataset = generate_synthetic_dataset(3000, seed=0)
    X = np.stack([vectorize(f) for f in dataset.features])
    return X, dataset.labels, train_risk_model(X, dataset.labels, seed=0)


def test_trained_model_beats_random_guessing_on_held_out_auc(
    trained: tuple[np.ndarray, np.ndarray, train_module.TrainedModel],
) -> None:
    _, _, result = trained
    assert result.metrics["test_auc"] > 0.75


def test_metrics_include_sample_counts(
    trained: tuple[np.ndarray, np.ndarray, train_module.TrainedModel],
) -> None:
    _, y, result = trained
    assert result.metrics["train_samples"] + result.metrics["test_samples"] == len(y)


def test_save_model_creates_bucket_and_writes_expected_keys(
    fake_client: _FakeMinioClient,
    trained: tuple[np.ndarray, np.ndarray, train_module.TrainedModel],
) -> None:
    _, _, result = trained
    version = save_model_to_minio(result)

    bucket = train_module.settings.minio_models_bucket
    assert bucket in fake_client.existing_buckets
    prefix = f"{train_module.MODEL_PREFIX}/{version}"
    assert (bucket, f"{prefix}/model.ubj") in fake_client.objects
    assert (bucket, f"{prefix}/feature_names.json") in fake_client.objects
    assert (bucket, f"{prefix}/metrics.json") in fake_client.objects
    assert (bucket, f"{prefix}/metadata.json") in fake_client.objects
    assert (bucket, train_module.LATEST_POINTER_KEY) in fake_client.objects


def test_latest_pointer_references_the_saved_version(
    fake_client: _FakeMinioClient,
    trained: tuple[np.ndarray, np.ndarray, train_module.TrainedModel],
) -> None:
    _, _, result = trained
    version = save_model_to_minio(result)

    bucket = train_module.settings.minio_models_bucket
    pointer = json.loads(fake_client.objects[(bucket, train_module.LATEST_POINTER_KEY)])
    assert pointer["version"] == version


def test_resaving_the_identical_model_is_idempotent_on_version(
    fake_client: _FakeMinioClient,
    trained: tuple[np.ndarray, np.ndarray, train_module.TrainedModel],
) -> None:
    _, _, result = trained
    version_a = save_model_to_minio(result)
    version_b = save_model_to_minio(result)
    assert version_a == version_b


def test_distinct_models_get_distinct_versions(
    fake_client: _FakeMinioClient,
) -> None:
    dataset = generate_synthetic_dataset(500, seed=10)
    X = np.stack([vectorize(f) for f in dataset.features])
    result_a = train_risk_model(X, dataset.labels, seed=1)
    result_b = train_risk_model(X, dataset.labels, seed=2)

    version_a = save_model_to_minio(result_a)
    version_b = save_model_to_minio(result_b)
    assert version_a != version_b


def test_load_model_round_trips_predictions(
    fake_client: _FakeMinioClient,
    trained: tuple[np.ndarray, np.ndarray, train_module.TrainedModel],
) -> None:
    X, _, result = trained
    save_model_to_minio(result)

    loaded_model, feature_names, metrics = load_model()

    assert feature_names == FEATURE_NAMES
    assert metrics == result.metrics
    original_predictions = result.model.predict_proba(X[:10])[:, 1]
    loaded_predictions = loaded_model.predict_proba(X[:10])[:, 1]
    assert np.allclose(original_predictions, loaded_predictions)


def test_load_model_with_explicit_version_bypasses_latest_pointer(
    fake_client: _FakeMinioClient,
    trained: tuple[np.ndarray, np.ndarray, train_module.TrainedModel],
) -> None:
    _, _, result = trained
    version_a = save_model_to_minio(result)
    save_model_to_minio(result)  # becomes latest

    _, _, metrics = load_model(version=version_a)
    assert metrics == result.metrics


def test_train_risk_model_on_real_ai4i_dataset() -> None:
    """Verifies that training on the real AI4I 2020 dataset achieves high held-out AUC
    and PR-AUC, resolving the class imbalance of 3.39% failures."""
    if not RAW_CSV_PATH.is_file():
        pytest.skip(f"Raw CSV not found at {RAW_CSV_PATH}")

    records = map_ai4i2020_dataset(RAW_CSV_PATH)
    X, y, _ = build_training_dataset_from_mapped_records(records)

    trained = train_risk_model(X, y, seed=0)

    # Asserts on held-out metrics
    assert trained.metrics["test_auc"] > 0.90
    assert trained.metrics["test_pr_auc"] > 0.70
    assert trained.metrics["test_f1"] > 0.60
    assert trained.metrics["test_recall"] > 0.60
    assert trained.metrics["scale_pos_weight"] > 10.0

    # Asserts on audit metadata
    assert trained.metadata is not None
    assert trained.metadata["dataset_name"] == "AI4I 2020 Predictive Maintenance Dataset"
    assert "UCI" in str(trained.metadata["dataset_source"])


def test_save_and_load_model_metadata(fake_client: _FakeMinioClient) -> None:
    """Verifies that model metadata is persisted and retrievable for auditability."""
    X = np.zeros((20, len(FEATURE_NAMES)))
    y = np.array([0] * 16 + [1] * 4)
    trained = train_risk_model(X, y, seed=0)
    version = save_model_to_minio(trained)

    meta = load_model_metadata(version)
    assert meta is not None
    assert meta["dataset_name"] == "AI4I 2020 Predictive Maintenance Dataset"
    assert meta["target_column"] == "Machine failure"


def test_train_risk_model_cli_real_dataset_no_upload() -> None:
    """Verifies train_risk_model CLI execution on the real dataset with --no-upload."""
    if not RAW_CSV_PATH.is_file():
        pytest.skip(f"Raw CSV not found at {RAW_CSV_PATH}")

    args = parse_args(["--dataset-path", str(RAW_CSV_PATH), "--no-upload", "--seed", "42"])
    trained = run_train_script(args)

    assert trained.metrics["test_auc"] > 0.80
    assert trained.metrics["test_pr_auc"] > 0.40

