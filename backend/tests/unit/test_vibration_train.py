"""Tests for the vibration anomaly detector: scoring maths, alarm logic, evaluation protocol."""

import io
from pathlib import Path

import numpy as np
import pytest
from sklearn.ensemble import IsolationForest

from app.ml import vibration_train
from app.ml.vibration_features import FEATURE_NAMES, fit_baseline
from app.ml.vibration_train import (
    FAILING_BEARINGS,
    K_CONSECUTIVE,
    TESTS,
    ChannelSeries,
    ForestScorer,
    count_alarm_onsets,
    evaluate_test,
    first_sustained,
    fit_detector,
    load_series,
    load_vibration_model,
    model_digest,
    save_vibration_model_to_minio,
    train_vibration_model,
)
from scripts.train_vibration_model import parse_args
from scripts.train_vibration_model import run as run_script

N_FEATURES = len(FEATURE_NAMES)
FEATURES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "processed" / "ims"


# ---- numpy scorer == scikit-learn -----------------------------------------------------------


def test_forest_scorer_equals_sklearn_exactly() -> None:
    rng = np.random.default_rng(0)
    X = rng.normal(size=(500, N_FEATURES))
    queries = rng.normal(size=(250, N_FEATURES)) * 3
    model = IsolationForest(n_estimators=40, random_state=2, n_jobs=1).fit(X)
    scorer = ForestScorer.from_sklearn(model)
    np.testing.assert_allclose(
        scorer.score_samples(queries), model.score_samples(queries), atol=1e-12
    )
    restored = ForestScorer.from_npz_bytes(scorer.to_npz_bytes())
    np.testing.assert_allclose(
        restored.score_samples(queries), model.score_samples(queries), atol=1e-12
    )
    np.testing.assert_allclose(
        restored.anomaly_score(queries), -model.score_samples(queries), atol=1e-12
    )


def test_scorer_rejects_bad_input_and_pickled_arrays() -> None:
    rng = np.random.default_rng(1)
    model = IsolationForest(n_estimators=5, random_state=0, n_jobs=1).fit(
        rng.normal(size=(100, N_FEATURES))
    )
    scorer = ForestScorer.from_sklearn(model)
    with pytest.raises(ValueError, match="expected an"):
        scorer.score_samples(np.zeros((3, 4)))
    bad = np.zeros((2, N_FEATURES))
    bad[0, 0] = np.nan
    with pytest.raises(ValueError, match="NaN or infinite"):
        scorer.score_samples(bad)
    # an .npz containing a pickled object array must be refused (no pickle on load)
    buffer = io.BytesIO()
    np.savez(buffer, meta=np.array([{"evil": 1}], dtype=object))
    with pytest.raises(ValueError, match="pickle"):
        ForestScorer.from_npz_bytes(buffer.getvalue())


# ---- alarm logic ----------------------------------------------------------------------------


def test_first_sustained_requires_consecutive_exceedances() -> None:
    e = np.array([1, 1, 0, 1, 1, 1, 0, 1], dtype=bool)
    assert first_sustained(e, 3) == 5
    assert first_sustained(e, 4) is None
    assert first_sustained(e, 2, start=3) == 4
    assert first_sustained(np.zeros(5, dtype=bool), 1) is None


def test_count_alarm_onsets_counts_episodes_not_samples() -> None:
    e = np.array([1] * 6 + [0] + [1] * 7 + [0] * 3 + [1, 1], dtype=bool)
    assert count_alarm_onsets(e, 3, 0, len(e)) == 2  # two long episodes; the final 2 are too short
    assert count_alarm_onsets(e, 3, 7, len(e)) == 1
    assert count_alarm_onsets(np.zeros(10, dtype=bool), 3, 0, 10) == 0


# ---- synthetic IMS-shaped data ----------------------------------------------------------------


def _series(
    test: str,
    channel: int,
    n: int,
    seed: int,
    onset: float | None = None,
    drift_alarm_at: float | None = None,
) -> ChannelSeries:
    """Gaussian z-scores; from `onset` (fraction of run) the signal ramps away from healthy."""
    rng = np.random.default_rng(seed)
    z = rng.normal(0, 1, (n, N_FEATURES))
    if onset is not None:
        start = int(onset * n)
        ramp = np.linspace(0.0, 8.0, n - start)[:, None]
        z[start:] += ramp
    if drift_alarm_at is not None:  # a short healthy-period excursion (false alarm)
        s = int(drift_alarm_at * n)
        z[s : s + 12] += 9.0
    times = 1.0e9 + 600.0 * np.arange(n)
    n_base = int(0.15 * n)
    return ChannelSeries(test, channel, z, times, fit_baseline(z[:n_base]), n_base)


def _data(onset: float | None = 0.6, fa_test: str | None = None) -> dict[str, list[ChannelSeries]]:
    channels = {"1st_test": 8, "2nd_test": 4, "3rd_test": 4}
    failing = {
        c for chans in sum((list(v.values()) for v in FAILING_BEARINGS.values()), []) for c in chans
    }
    out: dict[str, list[ChannelSeries]] = {}
    for t_idx, test in enumerate(TESTS):
        series = []
        for ch in range(1, channels[test] + 1):
            is_failing = ch in {c for chans in FAILING_BEARINGS[test].values() for c in chans}
            fa = 0.25 if (test == fa_test and ch == 1) else None
            series.append(
                _series(
                    test,
                    ch,
                    400,
                    seed=100 * t_idx + ch,
                    onset=onset if is_failing else None,
                    drift_alarm_at=fa,
                )
            )
        out[test] = series
    assert failing  # sanity
    return out


def test_detector_finds_degradation_and_stays_quiet_on_healthy_channels() -> None:
    data = _data()
    train = [s for t in ("1st_test", "2nd_test") for s in data[t]]
    det = fit_detector(train, seed=0)
    res = evaluate_test(data["3rd_test"], det.scores, det.threshold)
    bearing = res["bearings"]["bearing_3"]
    assert bearing["detected"] and not bearing["alarm_during_healthy_window"]
    assert bearing["alarm_index"] >= int(0.6 * 400)
    assert bearing["lead_hours"] == pytest.approx((400 - 1 - bearing["alarm_index"]) * 600 / 3600)
    assert bearing["fraction_of_run_remaining"] == pytest.approx(
        (400 - bearing["alarm_index"]) / 400
    )
    assert res["false_alarm_channels"] == []


def test_missed_and_early_alarms_are_reported_honestly() -> None:
    healthy = _data(onset=None)
    train = [s for t in ("1st_test", "2nd_test") for s in healthy[t]]
    det = fit_detector(train, seed=0)
    assert evaluate_test(healthy["3rd_test"], det.scores, det.threshold)["bearings"][
        "bearing_3"
    ] == {"detected": False}
    noisy = _data(onset=0.6, fa_test="3rd_test")
    res = evaluate_test(noisy["3rd_test"], det.scores, det.threshold)
    assert 1 in res["false_alarm_channels"]
    assert res["false_alarm_onsets"] >= 1
    assert res["false_alarm_channel_fraction"] == pytest.approx(
        len(res["false_alarm_channels"]) / 4
    )


def test_threshold_is_calibrated_on_held_out_baseline_part_only() -> None:
    """Changing anything after the baseline window cannot change the fitted detector."""
    data = _data()
    train = [s for t in ("1st_test", "2nd_test") for s in data[t]]
    a = fit_detector(train, seed=0)
    changed = [
        ChannelSeries(
            s.test,
            s.channel,
            np.vstack([s.z[: s.n_baseline], s.z[s.n_baseline :] * 50]),
            s.times,
            s.baseline,
            s.n_baseline,
        )
        for s in train
    ]
    b = fit_detector(changed, seed=0)
    assert a.threshold == b.threshold
    assert a.scorer.to_npz_bytes() == b.scorer.to_npz_bytes()


def test_leave_one_test_out_never_scores_a_test_with_a_model_that_saw_its_run() -> None:
    data = _data()
    base = train_vibration_model(data, seed=0).metrics["leave_one_test_out"]["isolation_forest"][
        "per_test"
    ]
    tampered = {k: list(v) for k, v in data.items()}
    tampered["1st_test"] = [
        ChannelSeries(
            s.test,
            s.channel,
            np.vstack([s.z[: s.n_baseline], s.z[s.n_baseline :] * 50]),
            s.times,
            s.baseline,
            s.n_baseline,
        )
        for s in data["1st_test"]
    ]
    after = train_vibration_model(tampered, seed=0).metrics["leave_one_test_out"][
        "isolation_forest"
    ]["per_test"]
    # tests 2 and 3 are judged by models trained on test 1's *baseline window only*: unchanged
    assert after[1] == base[1]
    assert after[2] == base[2]
    assert after[0] != base[0]


def test_training_is_deterministic_and_reports_baselines() -> None:
    data = _data()
    a, b = train_vibration_model(data, seed=0), train_vibration_model(data, seed=0)
    assert model_digest(a) == model_digest(b)
    assert set(a.metrics["leave_one_test_out"]) == {
        "isolation_forest",
        "threshold_rms",
        "threshold_kurtosis",
    }
    summary = a.metrics["leave_one_test_out"]["isolation_forest"]["summary"]
    assert summary["failing_bearings"] == 4
    assert summary["detected_after_healthy_window"] == 4
    assert a.detector.k_consecutive == K_CONSECUTIVE


# ---- MinIO ---------------------------------------------------------------------------------


class _Resp:
    def __init__(self, data: bytes) -> None:
        self._d = data

    def read(self) -> bytes:
        return self._d

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass


class _FakeMinio:
    def __init__(self) -> None:
        self.buckets: set[str] = set()
        self.objects: dict[tuple[str, str], bytes] = {}

    def bucket_exists(self, b: str) -> bool:
        return b in self.buckets

    def make_bucket(self, b: str) -> None:
        self.buckets.add(b)

    def put_object(self, b: str, k: str, data: io.BytesIO, length: int) -> None:
        self.objects[(b, k)] = data.read()

    def get_object(self, b: str, k: str) -> _Resp:
        return _Resp(self.objects[(b, k)])


@pytest.fixture
def fake_minio(monkeypatch: pytest.MonkeyPatch) -> _FakeMinio:
    client = _FakeMinio()
    monkeypatch.setattr(vibration_train, "get_minio_client", lambda: client)
    return client


def test_minio_round_trip_preserves_scores_threshold_and_metadata(fake_minio: _FakeMinio) -> None:
    trained = train_vibration_model(
        _data(), seed=0, dataset_metadata={"dataset_name": "synthetic-test"}
    )
    version = save_vibration_model_to_minio(trained)
    detector, metrics, metadata = load_vibration_model()
    X = np.random.default_rng(5).normal(size=(50, N_FEATURES)) * 2
    np.testing.assert_array_equal(detector.scores(X), trained.detector.scores(X))
    assert detector.threshold == trained.detector.threshold
    assert detector.k_consecutive == trained.detector.k_consecutive
    assert metadata["model_version"] == version
    assert metadata["dataset_name"] == "synthetic-test"
    assert metrics["leave_one_test_out"]["isolation_forest"]["summary"]["failing_bearings"] == 4
    assert not any(k.endswith(".pkl") for _, k in fake_minio.objects)


def test_load_rejects_a_different_feature_layout(fake_minio: _FakeMinio) -> None:
    import json

    save_vibration_model_to_minio(train_vibration_model(_data(), seed=0))
    key = next(k for _, k in fake_minio.objects if k.endswith("detector_config.json"))
    config = json.loads(fake_minio.objects[("ml-models", key)])
    config["feature_names"] = config["feature_names"][:-1]
    fake_minio.objects[("ml-models", key)] = json.dumps(config).encode()
    with pytest.raises(ValueError, match="different feature layout"):
        load_vibration_model()


# ---- real IMS features ------------------------------------------------------------------------

_have_features = pytest.mark.skipif(
    not all((FEATURES_DIR / f"{t}_vibration_features.csv.gz").is_file() for t in TESTS),
    reason="vibration features missing; run `python -m scripts.build_vibration_features`",
)


@_have_features
def test_real_ims_leave_one_test_out_results() -> None:
    trained = train_vibration_model(load_series(FEATURES_DIR), seed=0)
    loto = trained.metrics["leave_one_test_out"]
    forest = loto["isolation_forest"]["summary"]
    assert forest["failing_bearings"] == 4
    assert forest["detected_after_healthy_window"] == 4
    assert forest["false_alarm_channel_fraction"] <= 0.25
    assert forest["median_lead_hours"] > 24
    assert forest["median_persistence_after_alarm"] > 0.9
    # the kurtosis-only baseline alarms inside the early-life window on set 1 (a false alarm)
    kurt = loto["threshold_kurtosis"]["summary"]
    assert kurt["detected_after_healthy_window"] < forest["detected_after_healthy_window"]
    # each held-out test is judged only by a model that never saw that test
    assert [r["test"] for r in loto["isolation_forest"]["per_test"]] == list(TESTS)


@_have_features
def test_real_results_across_seeds_what_is_stable_and_what_is_not() -> None:
    """Detection, false alarms and tests 2/3 lead times hold across seeds; set 1 bearing 4 does not.

    Measured over 12 seeds (docs/DATASETS.md): detection 4/4 in 11 of 12 seeds, false-alarm
    channels 2-4 of 16, median lead 74-174 h. The swing comes from 1st_test bearing_4
    (about 157 h or about 270 h depending on the seed), so lead time there is NOT asserted.
    """
    data = load_series(FEATURES_DIR)
    runs = [
        train_vibration_model(data, seed=s).metrics["leave_one_test_out"]["isolation_forest"]
        for s in (0, 7)
    ]
    for run in runs:
        assert run["summary"]["detected_after_healthy_window"] >= 3
        assert run["summary"]["false_alarm_channel_fraction"] <= 0.25
    bearing_1 = [r["per_test"][1]["bearings"]["bearing_1"]["lead_hours"] for r in runs]
    bearing_3 = [r["per_test"][2]["bearings"]["bearing_3"]["lead_hours"] for r in runs]
    assert abs(bearing_1[0] - bearing_1[1]) < 5
    assert abs(bearing_3[0] - bearing_3[1]) < 10


@_have_features
def test_script_refuses_to_publish_when_gates_fail(fake_minio: _FakeMinio) -> None:
    with pytest.raises(SystemExit, match="Refusing to publish: detected"):
        run_script(parse_args(["--min-detected-bearings", "5"]))
    with pytest.raises(SystemExit, match="false-alarm channel fraction"):
        run_script(parse_args(["--max-false-alarm-channel-fraction", "0.0"]))
    assert fake_minio.objects == {}


@_have_features
def test_script_publishes_with_audit_metadata(fake_minio: _FakeMinio) -> None:
    run_script(parse_args([]))
    _, _, metadata = load_vibration_model()
    assert metadata["dataset_name"].startswith("NASA/IMS")
    assert set(metadata["feature_file_sha256"]) == {f"{t}_vibration_features.csv.gz" for t in TESTS}
    assert len(metadata["archive_sha256"]) == 64
    assert "four documented failure events" in metadata["limitations"]
