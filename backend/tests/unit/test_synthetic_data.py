"""Unit tests for app.ml.synthetic_data - confirms the injected failure pattern is
actually present in the generated dataset, not just that generation runs."""

import numpy as np

from app.ml.synthetic_data import generate_synthetic_dataset


def test_generates_requested_sample_count() -> None:
    dataset = generate_synthetic_dataset(200, seed=1)
    assert len(dataset.features) == 200
    assert dataset.labels.shape == (200,)
    assert dataset.failure_probabilities.shape == (200,)


def test_labels_are_binary() -> None:
    dataset = generate_synthetic_dataset(200, seed=1)
    assert set(np.unique(dataset.labels)).issubset({0, 1})


def test_is_deterministic_given_a_seed() -> None:
    a = generate_synthetic_dataset(50, seed=42)
    b = generate_synthetic_dataset(50, seed=42)
    assert np.array_equal(a.labels, b.labels)
    assert np.allclose(a.failure_probabilities, b.failure_probabilities)


def test_different_seeds_produce_different_datasets() -> None:
    a = generate_synthetic_dataset(50, seed=1)
    b = generate_synthetic_dataset(50, seed=2)
    assert not np.array_equal(a.labels, b.labels)


def test_older_assets_have_higher_failure_probability_on_average() -> None:
    dataset = generate_synthetic_dataset(2000, seed=3)
    ages = np.array([f.asset_age_days for f in dataset.features])
    old_mask = ages > np.median(ages)
    young_mask = ~old_mask
    assert dataset.failure_probabilities[old_mask].mean() > (
        dataset.failure_probabilities[young_mask].mean()
    )


def test_offline_neighbors_raise_failure_probability_on_average() -> None:
    dataset = generate_synthetic_dataset(2000, seed=4)
    offline_counts = np.array(
        [f.dependency_neighbor_offline_count for f in dataset.features]
    )
    has_offline_neighbor = offline_counts > 0
    assert dataset.failure_probabilities[has_offline_neighbor].mean() > (
        dataset.failure_probabilities[~has_offline_neighbor].mean()
    )


def test_high_vibration_instability_raises_failure_probability_on_average() -> None:
    dataset = generate_synthetic_dataset(2000, seed=5)
    cvs = np.array(
        [
            (f.windows[30]["vibration_mm_s"].stddev / f.windows[30]["vibration_mm_s"].mean)
            for f in dataset.features
        ]
    )
    unstable_mask = cvs > np.median(cvs)
    assert dataset.failure_probabilities[unstable_mask].mean() > (
        dataset.failure_probabilities[~unstable_mask].mean()
    )
