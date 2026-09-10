"""Synthetic training data for the offline risk model (issue 3.2).

No `maintenance_records`/historical-failure table exists in this repo (see
`AssetFeatureSet`'s docstring), so there is no real labeled failure history to train
against. Per PHASE_PLAN.md's "train offline on synthetic data with injected failure
patterns," this module builds synthetic `AssetFeatureSet` instances plus binary
failure labels from a known latent risk function, so the training script has
something with ground truth to fit and the test suite can confirm the model actually
recovers the injected signal (not just memorizing noise).

The injected pattern, deliberately mirroring PROJECT_PLAN.md §4.3's stated risk
factors: older assets, assets with more offline neighbors (cascading failure), and
assets showing recent (30-day window) vibration instability are more likely to fail.
Every other feature (other sensor types, the 90/365-day windows) is generated as
uncorrelated noise, on purpose - a model that overfits to noise columns instead of
the true signal should show up as a lower held-out AUC in the test suite.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from app.ml.feature_vector import SENSOR_TYPES, WINDOW_DAYS
from app.schemas.ml.asset_features import AssetFeatureSet, SensorWindowStats

# Coefficients for the latent risk logit - documented so tests can assert the sign
# and rough magnitude of each factor's learned importance, not just overall AUC.
AGE_DAYS_COEF = 0.0012
OFFLINE_NEIGHBOR_COEF = 0.9
VIBRATION_CV_COEF = 2.5  # coefficient of variation (stddev/mean) of 30-day vibration
VIBRATION_ANOMALY_COEF = 0.6
BASELINE_LOGIT = -4.0


@dataclass(frozen=True)
class SyntheticDataset:
    features: list[AssetFeatureSet]
    labels: np.ndarray  # shape (n,), values in {0, 1}
    failure_probabilities: np.ndarray  # shape (n,), the true injected P(failure)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _random_window_stats(
    rng: np.random.Generator, sensor_type: str, window_days: int
) -> SensorWindowStats:
    count = int(rng.integers(5, 500))
    mean = float(rng.uniform(10.0, 100.0))
    stddev = float(rng.uniform(0.5, 10.0))
    return SensorWindowStats(
        sensor_type=sensor_type,
        window_days=window_days,
        count=count,
        mean=mean,
        stddev=stddev,
        min=mean - 3 * stddev,
        max=mean + 3 * stddev,
        latest_value=float(rng.normal(mean, stddev)),
        anomaly_count=int(rng.poisson(1.0)),
    )


def generate_synthetic_dataset(n_samples: int, *, seed: int = 0) -> SyntheticDataset:
    rng = np.random.default_rng(seed)
    now = datetime.now(UTC)

    features: list[AssetFeatureSet] = []
    logits = np.zeros(n_samples)

    for i in range(n_samples):
        asset_age_days = int(rng.uniform(0, 4000))
        neighbor_count = int(rng.poisson(2))
        offline_count = int(rng.binomial(neighbor_count, 0.15)) if neighbor_count else 0
        remaining = max(neighbor_count - offline_count, 0)
        maintenance_count = int(rng.binomial(remaining, 0.1)) if remaining else 0

        windows: dict[int, dict[str, SensorWindowStats]] = {}
        vibration_30d_cv = 0.0
        vibration_30d_anomalies = 0
        for window_days in WINDOW_DAYS:
            window: dict[str, SensorWindowStats] = {}
            for sensor_type in SENSOR_TYPES:
                stats = _random_window_stats(rng, sensor_type, window_days)
                window[sensor_type] = stats
                if window_days == 30 and sensor_type == "vibration_mm_s":
                    vibration_30d_cv = (
                        stats.stddev / stats.mean if stats.mean and stats.stddev else 0.0
                    )
                    vibration_30d_anomalies = stats.anomaly_count
            windows[window_days] = window

        logit = (
            BASELINE_LOGIT
            + AGE_DAYS_COEF * asset_age_days
            + OFFLINE_NEIGHBOR_COEF * offline_count
            + VIBRATION_CV_COEF * vibration_30d_cv
            + VIBRATION_ANOMALY_COEF * vibration_30d_anomalies
        )
        logits[i] = logit

        features.append(
            AssetFeatureSet(
                asset_id=uuid.uuid4(),
                tenant_id=uuid.uuid4(),
                facility_id=uuid.uuid4(),
                computed_at=now,
                asset_status=str(rng.choice(["operational", "maintenance", "offline"])),
                asset_age_days=asset_age_days,
                dependency_neighbor_count=neighbor_count,
                dependency_neighbor_offline_count=offline_count,
                dependency_neighbor_maintenance_count=maintenance_count,
                windows=windows,
            )
        )

    probabilities = _sigmoid(logits)
    labels = rng.binomial(1, probabilities)

    return SyntheticDataset(
        features=features, labels=labels, failure_probabilities=probabilities
    )
