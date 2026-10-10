"""Training, evaluation and versioning for the vibration anomaly detector (Phase 3b).

Model: an Isolation Forest fitted on *healthy* early-life snapshots only, scored on
per-channel baseline-normalised features (`vibration_features`). A channel raises an
alarm after `K_CONSECUTIVE` consecutive snapshots whose anomaly score exceeds a threshold
taken from held-out healthy data.

Evaluation is leave-one-test-out over the three IMS tests: the forest and threshold are
fitted on the healthy windows of two tests and judged on the third, so no test is ever
scored by a model that saw its own run. The deployed model is then refitted on all three
tests' healthy windows. Two simple per-channel thresholds (RMS and kurtosis) are run with
the identical alarm rule as baselines the forest has to beat.

The forest is stored without pickle: its tree arrays go into an `.npz` (loaded with
`allow_pickle=False`) and are scored by `ForestScorer`, a small numpy re-implementation
that is tested for exact equality with scikit-learn's `score_samples`.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest

from app.core.config import settings
from app.core.storage import get_minio_client
from app.ml.train import _get_json, _put_json
from app.ml.vibration_features import (
    FEATURE_NAMES,
    Baseline,
    baseline_length,
    fit_baseline,
    normalize,
)

MODEL_PREFIX = "vibration_model"
LATEST_POINTER_KEY = f"{MODEL_PREFIX}/latest.json"

# --- fixed up front, NOT tuned on failure outcomes ---------------------------------------
N_ESTIMATORS = 200
FIT_FRACTION = 0.7  # share of each baseline window used to fit; the rest calibrates the threshold
THRESHOLD_QUANTILE = 0.995
K_CONSECUTIVE = 5  # consecutive snapshots above threshold (50 min at the 10-minute cadence)
HEALTHY_EVAL_FRACTION = (
    0.40  # false alarms are counted from the end of the baseline to 40 % of the run
)

# Documented failing bearings (NASA IMS readme): test -> {bearing: channels}
FAILING_BEARINGS: dict[str, dict[str, list[int]]] = {
    "1st_test": {"bearing_3": [5, 6], "bearing_4": [7, 8]},
    "2nd_test": {"bearing_1": [1]},
    "3rd_test": {"bearing_3": [3]},
}
TESTS: tuple[str, ...] = tuple(FAILING_BEARINGS)

LIMITATIONS = (
    "Trained on the NASA/IMS lab bearing rig (fixed speed and load). Detection lead times "
    "rest on four documented failure events, measured against the end of each recording; "
    "healthy channels also rise late in tests 2 and 3 (cross-talk), and test 3 shows baseline "
    "drift on healthy channels 2 and 4."
)


# ---- numpy isolation-forest scorer (no pickle) ---------------------------------------------


def _average_path_length(n: np.ndarray) -> np.ndarray:
    n = np.asarray(n, dtype=np.float64)
    out = np.zeros_like(n)
    out[n == 2] = 1.0
    big = n > 2
    out[big] = 2.0 * (np.log(n[big] - 1.0) + np.euler_gamma) - 2.0 * (n[big] - 1.0) / n[big]
    return out


@dataclass(frozen=True)
class ForestScorer:
    """Tree arrays of a fitted IsolationForest plus the maths to score with them."""

    children_left: tuple[np.ndarray, ...]
    children_right: tuple[np.ndarray, ...]
    feature: tuple[np.ndarray, ...]  # already mapped to the full feature index
    threshold: tuple[np.ndarray, ...]
    n_node_samples: tuple[np.ndarray, ...]
    max_samples: int
    n_features: int

    @classmethod
    def from_sklearn(cls, model: IsolationForest) -> ForestScorer:
        left, right, feat, thr, nns = [], [], [], [], []
        for estimator, features in zip(model.estimators_, model.estimators_features_, strict=True):
            tree = estimator.tree_
            left.append(tree.children_left.astype(np.int64))
            right.append(tree.children_right.astype(np.int64))
            mapped = np.where(
                tree.feature >= 0, np.asarray(features)[np.maximum(tree.feature, 0)], -2
            )
            feat.append(mapped.astype(np.int64))
            thr.append(tree.threshold.astype(np.float64))
            nns.append(tree.n_node_samples.astype(np.int64))
        return cls(
            tuple(left),
            tuple(right),
            tuple(feat),
            tuple(thr),
            tuple(nns),
            int(model.max_samples_),
            int(model.n_features_in_),
        )

    def score_samples(self, X: np.ndarray) -> np.ndarray:
        """Same values as `IsolationForest.score_samples` (higher = more normal)."""
        X = np.asarray(X, dtype=np.float64)
        if X.ndim != 2 or X.shape[1] != self.n_features:
            raise ValueError(f"expected an (n, {self.n_features}) matrix, got {X.shape}")
        if not np.isfinite(X).all():
            raise ValueError("features contain NaN or infinite values")
        depths = np.zeros(X.shape[0])
        rows = np.arange(X.shape[0])
        for left, right, feat, thr, nns in zip(
            self.children_left,
            self.children_right,
            self.feature,
            self.threshold,
            self.n_node_samples,
            strict=True,
        ):
            node = np.zeros(X.shape[0], dtype=np.int64)
            edges = np.zeros(X.shape[0])
            active = left[node] != -1
            while active.any():
                idx = rows[active]
                cur = node[idx]
                go_left = X[idx, feat[cur]] <= thr[cur]
                node[idx] = np.where(go_left, left[cur], right[cur])
                edges[idx] += 1.0
                active = left[node] != -1
            depths += edges + _average_path_length(nns[node])
        denominator = len(self.feature) * float(
            _average_path_length(np.array([self.max_samples]))[0]
        )
        return -(2.0 ** (-depths / denominator)) if denominator else -np.ones(X.shape[0])

    def anomaly_score(self, X: np.ndarray) -> np.ndarray:
        """Higher = more anomalous, in (0, 1)."""
        return -self.score_samples(X)

    def to_npz_bytes(self) -> bytes:
        arrays: dict[str, np.ndarray] = {
            "meta": np.array([self.max_samples, self.n_features, len(self.feature)], dtype=np.int64)
        }
        for i in range(len(self.feature)):
            arrays[f"left_{i}"] = self.children_left[i]
            arrays[f"right_{i}"] = self.children_right[i]
            arrays[f"feat_{i}"] = self.feature[i]
            arrays[f"thr_{i}"] = self.threshold[i]
            arrays[f"nns_{i}"] = self.n_node_samples[i]
        buffer = io.BytesIO()
        np.savez(buffer, **arrays)  # type: ignore[arg-type]
        return buffer.getvalue()

    @classmethod
    def from_npz_bytes(cls, data: bytes) -> ForestScorer:
        with np.load(io.BytesIO(data), allow_pickle=False) as npz:
            max_samples, n_features, n_trees = (int(v) for v in npz["meta"])
            return cls(
                tuple(npz[f"left_{i}"] for i in range(n_trees)),
                tuple(npz[f"right_{i}"] for i in range(n_trees)),
                tuple(npz[f"feat_{i}"] for i in range(n_trees)),
                tuple(npz[f"thr_{i}"] for i in range(n_trees)),
                tuple(npz[f"nns_{i}"] for i in range(n_trees)),
                max_samples,
                n_features,
            )


# ---- data ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ChannelSeries:
    """One channel's normalised feature series for one IMS test."""

    test: str
    channel: int
    z: np.ndarray  # (n_snapshots, n_features) z-scored against this channel's baseline
    times: np.ndarray  # epoch seconds, ascending
    baseline: Baseline
    n_baseline: int


def load_series(features_dir: Path) -> dict[str, list[ChannelSeries]]:
    """Read the 3b.5 feature CSVs and normalise every channel against its own baseline."""
    out: dict[str, list[ChannelSeries]] = {}
    for test in TESTS:
        path = features_dir / f"{test}_vibration_features.csv.gz"
        with gzip.open(path, "rt", newline="") as handle:
            rows = list(csv.DictReader(handle))
        series: list[ChannelSeries] = []
        for channel in sorted({int(r["channel"]) for r in rows}):
            chan = [r for r in rows if int(r["channel"]) == channel]
            raw = np.array([[float(r[n]) for n in FEATURE_NAMES] for r in chan])
            times = np.array([datetime.fromisoformat(r["timestamp"]).timestamp() for r in chan])
            n_base = baseline_length(len(raw))
            baseline = fit_baseline(raw[:n_base])
            series.append(
                ChannelSeries(test, channel, normalize(raw, baseline), times, baseline, n_base)
            )
        out[test] = series
    return out


# ---- detector -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Detector:
    scorer: ForestScorer
    threshold: float
    k_consecutive: int = K_CONSECUTIVE

    def scores(self, z: np.ndarray) -> np.ndarray:
        return self.scorer.anomaly_score(z)


def fit_detector(train: list[ChannelSeries], *, seed: int = 0) -> Detector:
    """Fit on the first part of each baseline window; calibrate the threshold on the rest."""
    fit_parts, calib_parts = [], []
    for s in train:
        split = int(FIT_FRACTION * s.n_baseline)
        fit_parts.append(s.z[:split])
        calib_parts.append(s.z[split : s.n_baseline])
    forest = IsolationForest(n_estimators=N_ESTIMATORS, random_state=seed, n_jobs=1)
    forest.fit(np.vstack(fit_parts))
    scorer = ForestScorer.from_sklearn(forest)
    threshold = float(np.quantile(scorer.anomaly_score(np.vstack(calib_parts)), THRESHOLD_QUANTILE))
    return Detector(scorer=scorer, threshold=threshold)


def first_sustained(exceeds: np.ndarray, k: int, start: int = 0) -> int | None:
    """Index at which `k` consecutive True values (at or after `start`) first complete."""
    run = 0
    for i in range(start, len(exceeds)):
        run = run + 1 if exceeds[i] else 0
        if run >= k:
            return i
    return None


def count_alarm_onsets(exceeds: np.ndarray, k: int, start: int, stop: int) -> int:
    """Number of distinct sustained-alarm episodes completing within [start, stop)."""
    onsets, run, in_alarm = 0, 0, False
    for i in range(start, stop):
        run = run + 1 if exceeds[i] else 0
        if run >= k and not in_alarm:
            onsets += 1
            in_alarm = True
        if run == 0:
            in_alarm = False
    return onsets


ScoreFn = Callable[[np.ndarray], np.ndarray]


def evaluate_test(
    series: list[ChannelSeries], score_fn: ScoreFn, threshold: float, k: int = K_CONSECUTIVE
) -> dict[str, Any]:
    """False alarms on early life and detection lead time per documented failing bearing."""
    test = series[0].test
    by_channel = {s.channel: s for s in series}
    fa_channels: list[int] = []
    fa_onsets = 0
    healthy_snapshots = 0
    first_alarm: dict[int, int | None] = {}
    late_alarm_nonfailing: list[int] = []
    failing_channels = {c for chans in FAILING_BEARINGS[test].values() for c in chans}
    for s in series:
        n = len(s.z)
        exceeds = score_fn(s.z) > threshold
        stop = int(HEALTHY_EVAL_FRACTION * n)
        onsets = count_alarm_onsets(exceeds, k, s.n_baseline, stop)
        fa_onsets += onsets
        healthy_snapshots += max(0, stop - s.n_baseline)
        if onsets:
            fa_channels.append(s.channel)
        first_alarm[s.channel] = first_sustained(exceeds, k, start=s.n_baseline)
        if (
            s.channel not in failing_channels
            and first_sustained(exceeds, k, start=stop) is not None
        ):
            late_alarm_nonfailing.append(s.channel)

    bearings: dict[str, Any] = {}
    for bearing, channels in FAILING_BEARINGS[test].items():
        candidates: list[tuple[int, int]] = []
        for c in channels:
            alarm = first_alarm[c]
            if alarm is not None:
                candidates.append((alarm, c))
        if not candidates:
            bearings[bearing] = {"detected": False}
            continue
        idx, channel = min(candidates)
        s = by_channel[channel]
        n = len(s.z)
        # Post-hoc diagnostic (added after seeing a spurious mid-run alarm in test 3; NOT used
        # for any threshold or gate): a real degradation keeps the score above threshold.
        persistence = float((score_fn(s.z)[idx:] > threshold).mean())
        bearings[bearing] = {
            "detected": True,
            "channel": channel,
            "persistence_after_alarm": persistence,
            "alarm_index": int(idx),
            "lead_hours": float((s.times[-1] - s.times[int(idx)]) / 3600.0),
            "fraction_of_run_remaining": float((n - int(idx)) / n),
            "alarm_during_healthy_window": bool(int(idx) < int(HEALTHY_EVAL_FRACTION * n)),
        }
    return {
        "test": test,
        "channels": len(series),
        "false_alarm_channels": sorted(fa_channels),
        "false_alarm_channel_fraction": len(fa_channels) / len(series),
        "false_alarm_onsets": fa_onsets,
        "false_alarm_onsets_per_1000_snapshots": 1000.0 * fa_onsets / max(1, healthy_snapshots),
        "bearings": bearings,
        "nonfailing_channels_alarming_late": sorted(late_alarm_nonfailing),
    }


def _feature_scorer(name: str) -> ScoreFn:
    idx = FEATURE_NAMES.index(name)
    return lambda z: z[:, idx]


def fit_feature_threshold(train: list[ChannelSeries], name: str) -> float:
    """Threshold for the single-feature baseline, calibrated exactly like the forest's."""
    idx = FEATURE_NAMES.index(name)
    calib = np.concatenate(
        [s.z[int(FIT_FRACTION * s.n_baseline) : s.n_baseline, idx] for s in train]
    )
    return float(np.quantile(calib, THRESHOLD_QUANTILE))


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-test results into headline numbers."""
    bearings = [b for r in results for b in r["bearings"].values()]
    detected = [b for b in bearings if b["detected"] and not b["alarm_during_healthy_window"]]
    channels = sum(r["channels"] for r in results)
    fa = sum(len(r["false_alarm_channels"]) for r in results)
    return {
        "median_persistence_after_alarm": (
            float(np.median([b["persistence_after_alarm"] for b in detected])) if detected else None
        ),
        "failing_bearings": len(bearings),
        "detected_after_healthy_window": len(detected),
        "missed": sum(1 for b in bearings if not b["detected"]),
        "median_lead_hours": float(np.median([b["lead_hours"] for b in detected]))
        if detected
        else None,
        "median_fraction_of_run_remaining": (
            float(np.median([b["fraction_of_run_remaining"] for b in detected]))
            if detected
            else None
        ),
        "false_alarm_channels": fa,
        "total_channels": channels,
        "false_alarm_channel_fraction": fa / channels,
    }


@dataclass(frozen=True)
class TrainedVibrationModel:
    detector: Detector
    metrics: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


def train_vibration_model(
    data: dict[str, list[ChannelSeries]],
    *,
    seed: int = 0,
    dataset_metadata: dict[str, Any] | None = None,
) -> TrainedVibrationModel:
    """Leave-one-test-out evaluation, then refit the deployable detector on all three tests."""
    forest_results: list[dict[str, Any]] = []
    baseline_results: dict[str, list[dict[str, Any]]] = {"rms": [], "kurtosis": []}
    for held_out in TESTS:
        train = [s for t in TESTS if t != held_out for s in data[t]]
        detector = fit_detector(train, seed=seed)
        forest_results.append(evaluate_test(data[held_out], detector.scores, detector.threshold))
        for name in baseline_results:
            baseline_results[name].append(
                evaluate_test(
                    data[held_out], _feature_scorer(name), fit_feature_threshold(train, name)
                )
            )

    final = fit_detector([s for t in TESTS for s in data[t]], seed=seed)
    metrics: dict[str, Any] = {
        "leave_one_test_out": {
            "isolation_forest": {"summary": summarize(forest_results), "per_test": forest_results},
            **{
                f"threshold_{name}": {"summary": summarize(res), "per_test": res}
                for name, res in baseline_results.items()
            },
        },
        "threshold": final.threshold,
        "k_consecutive": final.k_consecutive,
        "threshold_quantile": THRESHOLD_QUANTILE,
        "fit_fraction": FIT_FRACTION,
        "healthy_eval_fraction": HEALTHY_EVAL_FRACTION,
    }
    metadata: dict[str, Any] = {
        **(dataset_metadata or {}),
        "model_type": "IsolationForest (numpy-scored, stored without pickle)",
        "feature_names": FEATURE_NAMES,
        "hyperparameters": {"n_estimators": N_ESTIMATORS, "max_samples": "auto"},
        "seed": seed,
        "trained_at": datetime.now(UTC).isoformat(),
        "library_versions": {"scikit-learn": sklearn.__version__, "numpy": np.__version__},
        "normalisation": "per-channel z-score against that channel's first 15 % of snapshots",
        "limitations": LIMITATIONS,
    }
    return TrainedVibrationModel(detector=final, metrics=metrics, metadata=metadata)


# ---- MinIO ----------------------------------------------------------------------------------


def save_vibration_model_to_minio(trained: TrainedVibrationModel) -> str:
    npz = trained.detector.scorer.to_npz_bytes()
    version = (
        f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{hashlib.sha256(npz).hexdigest()[:8]}"
    )
    client = get_minio_client()
    bucket = settings.minio_models_bucket
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    prefix = f"{MODEL_PREFIX}/{version}"
    config = {
        "threshold": trained.detector.threshold,
        "k_consecutive": trained.detector.k_consecutive,
        "feature_names": FEATURE_NAMES,
    }
    _put_json(client, bucket, f"{prefix}/detector_config.json", config)
    _put_json(client, bucket, f"{prefix}/metrics.json", trained.metrics)
    _put_json(
        client, bucket, f"{prefix}/metadata.json", {**trained.metadata, "model_version": version}
    )
    client.put_object(bucket, f"{prefix}/forest.npz", io.BytesIO(npz), length=len(npz))
    _put_json(client, bucket, LATEST_POINTER_KEY, {"version": version})
    return version


def load_vibration_model(
    version: str | None = None,
) -> tuple[Detector, dict[str, Any], dict[str, Any]]:
    """Load (detector, metrics, metadata); `version=None` follows latest.json."""
    client = get_minio_client()
    bucket = settings.minio_models_bucket
    if version is None:
        pointer = _get_json(client, bucket, LATEST_POINTER_KEY)
        assert isinstance(pointer, dict)
        version = str(pointer["version"])
    prefix = f"{MODEL_PREFIX}/{version}"
    config = _get_json(client, bucket, f"{prefix}/detector_config.json")
    metrics = _get_json(client, bucket, f"{prefix}/metrics.json")
    metadata = _get_json(client, bucket, f"{prefix}/metadata.json")
    assert isinstance(config, dict) and isinstance(metrics, dict) and isinstance(metadata, dict)
    if config["feature_names"] != FEATURE_NAMES:
        raise ValueError("stored model was trained on a different feature layout")
    response = client.get_object(bucket, f"{prefix}/forest.npz")
    try:
        data = response.read()
    finally:
        response.close()
        response.release_conn()
    detector = Detector(
        scorer=ForestScorer.from_npz_bytes(data),
        threshold=float(config["threshold"]),
        k_consecutive=int(config["k_consecutive"]),
    )
    return detector, metrics, metadata


def model_digest(trained: TrainedVibrationModel) -> str:
    """Stable hash of the forest, for determinism checks."""
    return hashlib.sha256(trained.detector.scorer.to_npz_bytes()).hexdigest()
