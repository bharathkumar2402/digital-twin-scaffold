"""Tests for RUL training: protocol integrity, metric correctness, versioning, and gates."""

import io

import numpy as np
import pytest

from app.ml import rul_train
from app.ml.cmapss_profile import COLUMNS, parse_cmapss_table, parse_rul_vector
from app.ml.rul_dataset import RulDataset, build_test_dataset, build_train_dataset
from app.ml.rul_features import RulFeatureConfig, fit_config, window_features
from app.ml.rul_train import (
    TrainedRulModel,
    cross_validate_by_engine,
    load_rul_model,
    model_bytes,
    nasa_score,
    regression_metrics,
    save_rul_model_to_minio,
    train_rul_model,
)
from scripts.acquire_cmapss import DEFAULT_OUTPUT_DIR
from scripts.train_rul_model import parse_args
from scripts.train_rul_model import run as run_script


def _engine(unit: int, life: int, seed: int) -> np.ndarray:
    """Synthetic engine whose sensors drift as it nears failure (a learnable signal)."""
    rng = np.random.default_rng(seed)
    rows = np.zeros((life, len(COLUMNS)))
    rows[:, 0] = unit
    rows[:, 1] = np.arange(1, life + 1)
    rows[:, 4] = 100.0
    wear = np.arange(life) / life
    rows[:, 5] = 518.67
    rows[:, 6] = 640 + 25 * wear + rng.normal(0, 0.3, life)
    rows[:, 7] = 1500 + 40 * wear**2 + rng.normal(0, 1.0, life)
    for c in range(8, 26):
        rows[:, c] = rng.normal(10 * c, 1, life)
    return rows


def _tables() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lives = [150, 170, 190, 160, 180, 200, 155, 175, 165, 185, 195, 145]
    train = np.vstack([_engine(i + 1, n, i) for i, n in enumerate(lives)])
    # test engines are cut short of failure; the remaining life is supplied separately
    cut = [(100, 60), (120, 55), (90, 70), (110, 45), (130, 40), (105, 65)]
    test_rows = [_engine(i + 1, full, 100 + i)[:seen] for i, (full, seen) in enumerate(cut)]
    rul = np.array([full - seen for full, seen in cut], dtype=np.float64)
    return train, np.vstack(test_rows), rul


@pytest.fixture(scope="module")
def datasets() -> tuple[RulDataset, RulDataset, RulFeatureConfig]:
    train_table, test_table, rul = _tables()
    cfg = fit_config(train_table)
    return build_train_dataset(train_table, cfg), build_test_dataset(test_table, rul, cfg), cfg


@pytest.fixture(scope="module")
def trained(datasets: tuple[RulDataset, RulDataset, RulFeatureConfig]) -> TrainedRulModel:
    train, test, cfg = datasets
    return train_rul_model(
        train, test, cfg, seed=0, n_splits=3, dataset_metadata={"dataset_name": "synthetic-test"}
    )


# ---- metrics -------------------------------------------------------------------------


def test_nasa_score_hand_computed_and_asymmetric() -> None:
    true = np.array([100.0, 100.0, 100.0])
    pred = np.array([100.0, 90.0, 110.0])  # exact, 10 early, 10 late
    expected = 0.0 + (np.exp(10 / 13) - 1) + (np.exp(10 / 10) - 1)
    assert nasa_score(true, pred) == pytest.approx(expected)
    assert nasa_score(true[:1], np.array([110.0])) > nasa_score(true[:1], np.array([90.0]))


def test_regression_metrics_hand_computed() -> None:
    true = np.array([10.0, 20.0, 30.0])
    capped = np.array([10.0, 20.0, 25.0])
    pred = np.array([12.0, 18.0, 25.0])
    m = regression_metrics(true, capped, pred)
    assert m["rmse_true"] == pytest.approx(np.sqrt((4 + 4 + 25) / 3))
    assert m["rmse_capped"] == pytest.approx(np.sqrt((4 + 4 + 0) / 3))
    assert m["mae_true"] == pytest.approx((2 + 2 + 5) / 3)
    assert m["mean_error_true"] == pytest.approx((2 - 2 - 5) / 3)


# ---- protocol integrity ----------------------------------------------------------------


def test_cv_never_shares_an_engine_across_folds(
    datasets: tuple[RulDataset, RulDataset, RulFeatureConfig],
) -> None:
    # cross_validate_by_engine asserts disjoint engines internally; it must run clean.
    train, _, cfg = datasets
    metrics, rounds = cross_validate_by_engine(train, cfg.rul_cap, seed=0, n_splits=3)
    assert metrics["cv_folds"] == 3
    assert rounds >= 10


def test_cv_requires_enough_engines(
    datasets: tuple[RulDataset, RulDataset, RulFeatureConfig],
) -> None:
    train, _, cfg = datasets
    with pytest.raises(ValueError, match="at least 20 engines"):
        cross_validate_by_engine(train, cfg.rul_cap, seed=0, n_splits=20)


def test_test_set_is_never_used_for_fitting(
    datasets: tuple[RulDataset, RulDataset, RulFeatureConfig], trained: TrainedRulModel
) -> None:
    train, test, cfg = datasets
    scrambled = RulDataset(
        X=test.X * 7.0 + 3.0,
        y=test.y[::-1].copy(),
        y_true=test.y_true[::-1].copy(),
        units=test.units,
        cycles=test.cycles,
        feature_names=test.feature_names,
    )
    other = train_rul_model(train, scrambled, cfg, seed=0, n_splits=3)
    assert model_bytes(other) == model_bytes(trained)
    assert other.metrics["cv_rmse_capped_mean"] == trained.metrics["cv_rmse_capped_mean"]
    assert other.metrics["test_rmse_true"] != trained.metrics["test_rmse_true"]


def test_training_is_deterministic(
    datasets: tuple[RulDataset, RulDataset, RulFeatureConfig], trained: TrainedRulModel
) -> None:
    train, test, cfg = datasets
    again = train_rul_model(train, test, cfg, seed=0, n_splits=3)
    assert model_bytes(again) == model_bytes(trained)


def test_model_beats_both_baselines_on_learnable_data(trained: TrainedRulModel) -> None:
    m = trained.metrics
    assert m["test_rmse_true"] < m["baseline_median"]["rmse_true"]
    assert m["test_rmse_true"] < m["baseline_cycle_only"]["rmse_true"] * 1.1
    assert m["test_nasa_score"] < m["baseline_median"]["nasa_score"]


def test_predictions_are_clipped_to_valid_range(
    datasets: tuple[RulDataset, RulDataset, RulFeatureConfig], trained: TrainedRulModel
) -> None:
    _, test, cfg = datasets
    raw = trained.model.predict(test.X * 50.0)  # wildly out-of-range features
    clipped = rul_train._clip(raw, cfg.rul_cap)
    assert clipped.min() >= 0.0
    assert clipped.max() <= cfg.rul_cap


def test_metadata_records_audit_fields(trained: TrainedRulModel) -> None:
    md = trained.metadata
    assert md["dataset_name"] == "synthetic-test"
    assert md["rul_cap"] == 125
    assert md["seed"] == 0
    assert md["hyperparameters"]["n_estimators"] == trained.metrics["n_estimators"]
    assert {"xgboost", "scikit-learn", "numpy"} <= set(md["library_versions"])
    assert "simulated" in md["limitations"]


# ---- MinIO versioning ----------------------------------------------------------------


class _FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass


class _FakeMinioClient:
    def __init__(self) -> None:
        self.buckets: set[str] = set()
        self.objects: dict[tuple[str, str], bytes] = {}

    def bucket_exists(self, bucket: str) -> bool:
        return bucket in self.buckets

    def make_bucket(self, bucket: str) -> None:
        self.buckets.add(bucket)

    def put_object(self, bucket: str, key: str, data: io.BytesIO, length: int) -> None:
        self.objects[(bucket, key)] = data.read()

    def get_object(self, bucket: str, key: str) -> _FakeResponse:
        return _FakeResponse(self.objects[(bucket, key)])


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeMinioClient:
    client = _FakeMinioClient()
    monkeypatch.setattr(rul_train, "get_minio_client", lambda: client)
    return client


def test_minio_round_trip_preserves_predictions_config_and_metadata(
    fake_client: _FakeMinioClient,
    trained: TrainedRulModel,
    datasets: tuple[RulDataset, RulDataset, RulFeatureConfig],
) -> None:
    _, test, cfg = datasets
    version = save_rul_model_to_minio(trained, "FD001")
    model, loaded_cfg, metrics, metadata = load_rul_model("FD001")
    np.testing.assert_array_equal(model.predict(test.X), trained.model.predict(test.X))
    assert loaded_cfg == cfg
    assert metrics["n_estimators"] == trained.metrics["n_estimators"]
    assert metadata["model_version"] == version
    assert metadata["subset"] == "FD001"
    assert load_rul_model("FD001", version)[3]["model_version"] == version


def test_subsets_are_stored_separately(
    fake_client: _FakeMinioClient, trained: TrainedRulModel
) -> None:
    v1 = save_rul_model_to_minio(trained, "FD001")
    save_rul_model_to_minio(trained, "FD002")
    keys = {key for _, key in fake_client.objects}
    assert f"rul_model/FD001/{v1}/model.ubj" in keys
    assert "rul_model/FD002/latest.json" in keys
    assert load_rul_model("FD001")[3]["subset"] == "FD001"


# ---- script gates and real data -----------------------------------------------------------

_have_data = pytest.mark.skipif(
    not (DEFAULT_OUTPUT_DIR / "train_FD001.txt").is_file(),
    reason="C-MAPSS not downloaded; run `python -m scripts.acquire_cmapss`",
)


@_have_data
def test_script_refuses_to_publish_when_gate_fails(fake_client: _FakeMinioClient) -> None:
    args = parse_args(["--subset", "FD001", "--max-test-rmse", "1.0"])
    with pytest.raises(SystemExit, match="Refusing to publish: test RMSE"):
        run_script(args)
    assert fake_client.objects == {}


@_have_data
def test_real_fd001_beats_baselines_and_uploads(fake_client: _FakeMinioClient) -> None:
    trained = run_script(parse_args(["--subset", "FD001"]))
    m = trained.metrics
    assert m["test_rmse_true"] < 20.0
    assert m["test_rmse_true"] < 0.7 * m["baseline_cycle_only"]["rmse_true"]
    assert m["test_rmse_true"] < 0.5 * m["baseline_median"]["rmse_true"]
    assert m["test_nasa_score"] < m["baseline_cycle_only"]["nasa_score"]
    assert abs(m["test_mean_error_true"]) < 5.0  # no large systematic late/early bias
    meta = load_rul_model("FD001")[3]
    assert meta["dataset_name"].startswith("NASA C-MAPSS")
    assert set(meta["file_sha256"]) == {"train_FD001.txt", "test_FD001.txt", "RUL_FD001.txt"}


@_have_data
def test_real_features_match_serving_path_for_test_engines() -> None:
    """The matrix fed to the model for each test engine equals the serving computation."""
    root = DEFAULT_OUTPUT_DIR
    train = parse_cmapss_table((root / "train_FD001.txt").read_text())
    test = parse_cmapss_table((root / "test_FD001.txt").read_text())
    rul = parse_rul_vector((root / "RUL_FD001.txt").read_text())
    cfg = fit_config(train)
    ds = build_test_dataset(test, rul, cfg)
    engine_5 = test[test[:, 0] == 5]
    np.testing.assert_array_equal(ds.X[4], window_features(engine_5, cfg))
