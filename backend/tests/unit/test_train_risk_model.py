"""Unit tests for app.ml.train (issue 3.2) - model quality on synthetic data, and
the MinIO versioning/round-trip contract, with a fake in-memory MinIO client rather
than a real one (mirrors tests/unit/test_sandbox_storage.py's pattern)."""

import io
import json

import numpy as np
import pytest

from app.ml import train as train_module
from app.ml.feature_vector import FEATURE_NAMES, vectorize
from app.ml.synthetic_data import generate_synthetic_dataset
from app.ml.train import load_model, save_model_to_minio, train_risk_model


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
