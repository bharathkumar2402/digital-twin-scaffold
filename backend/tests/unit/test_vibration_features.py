"""Tests for vibration feature extraction, baseline normalisation, and the batch builder."""

import csv
import gzip
from pathlib import Path

import numpy as np
import pytest

from app.ml.vibration_features import (
    BASELINE_MIN_SNAPSHOTS,
    DEFECT_FREQUENCIES_HZ,
    FEATURE_NAMES,
    N_SAMPLES,
    SAMPLE_RATE_HZ,
    Baseline,
    baseline_length,
    fit_baseline,
    normalize,
    vibration_features,
)
from scripts.build_vibration_features import build_test_features

T = np.arange(N_SAMPLES) / SAMPLE_RATE_HZ
IDX = {name: i for i, name in enumerate(FEATURE_NAMES)}


def _feat(signal: np.ndarray) -> dict[str, float]:
    return dict(zip(FEATURE_NAMES, vibration_features(signal).tolist(), strict=True))


def _impacts(rate_hz: float, seed: int = 0, noise: float = 0.05) -> np.ndarray:
    """Impulse train at `rate_hz`, each impact ringing a decaying 5 kHz resonance."""
    rng = np.random.default_rng(seed)
    signal = rng.normal(0, noise, N_SAMPLES)
    ring_t = np.arange(0, 0.004, 1 / SAMPLE_RATE_HZ)
    ring = np.exp(-ring_t / 0.0006) * np.sin(2 * np.pi * 5000 * ring_t)
    for k in range(int(rate_hz * N_SAMPLES / SAMPLE_RATE_HZ)):
        start = int(round(k / rate_hz * SAMPLE_RATE_HZ))
        end = min(N_SAMPLES, start + ring.size)
        signal[start:end] += ring[: end - start]
    return signal


def test_defect_frequencies_match_the_literature_values() -> None:
    assert DEFECT_FREQUENCIES_HZ["bpfo"] == pytest.approx(236.4, abs=0.5)
    assert DEFECT_FREQUENCIES_HZ["bpfi"] == pytest.approx(296.9, abs=0.5)
    assert DEFECT_FREQUENCIES_HZ["bsf"] == pytest.approx(139.9, abs=0.5)


def test_sine_wave_known_values() -> None:
    f = _feat(np.sin(2 * np.pi * 1000 * T))
    assert f["rms"] == pytest.approx(1 / np.sqrt(2), rel=1e-6)
    assert f["peak"] == pytest.approx(1.0, rel=1e-3)
    assert f["crest"] == pytest.approx(np.sqrt(2), rel=1e-3)
    assert f["skew"] == pytest.approx(0.0, abs=1e-6)
    assert f["kurtosis"] == pytest.approx(1.5, abs=1e-3)
    assert f["band_5"] == pytest.approx(1.0, abs=1e-3)  # 1 kHz sits in the 972-2114 Hz band
    assert sum(f[f"band_{i}"] for i in range(8)) == pytest.approx(1.0, abs=1e-6)


def test_gaussian_noise_has_kurtosis_three() -> None:
    f = _feat(np.random.default_rng(3).normal(0, 1, N_SAMPLES))
    assert f["kurtosis"] == pytest.approx(3.0, abs=0.25)
    assert f["skew"] == pytest.approx(0.0, abs=0.1)


@pytest.mark.parametrize(
    ("rate_key", "other_keys"),
    [("bpfo", ("bpfi", "bsf")), ("bpfi", ("bpfo", "bsf")), ("bsf", ("bpfo", "bpfi"))],
)
def test_envelope_spectrum_exposes_the_matching_defect_frequency(
    rate_key: str, other_keys: tuple[str, str]
) -> None:
    healthy = _feat(np.random.default_rng(1).normal(0, 0.3, N_SAMPLES))
    faulty = _feat(_impacts(DEFECT_FREQUENCIES_HZ[rate_key]))
    own = f"env_{rate_key}"
    assert faulty[own] > 0.3
    assert faulty[own] > 8 * healthy[own]
    for other in other_keys:
        assert faulty[own] > 3 * faulty[f"env_{other}"]
    # impulsive faults also raise kurtosis and crest factor
    assert faulty["kurtosis"] > 6.0
    assert faulty["crest"] > healthy["crest"]


def test_ratio_features_are_scale_invariant_and_amplitude_features_scale() -> None:
    sig = _impacts(DEFECT_FREQUENCIES_HZ["bpfo"])
    a, b = vibration_features(sig), vibration_features(sig * 10.0)
    for name in (
        "crest",
        "skew",
        "kurtosis",
        *[n for n in FEATURE_NAMES if n.startswith(("band_", "env_"))],
    ):
        assert b[IDX[name]] == pytest.approx(a[IDX[name]], rel=1e-6, abs=1e-9)
    for name in ("rms", "peak", "std"):
        assert b[IDX[name]] == pytest.approx(10 * a[IDX[name]], rel=1e-6)


def test_dead_sensor_returns_zeros_not_nan() -> None:
    feats = vibration_features(np.full(N_SAMPLES, 2.5))
    assert feats.tolist() == [0.0] * len(FEATURE_NAMES)


def test_invalid_signals_are_rejected() -> None:
    with pytest.raises(ValueError, match="expected 20480 samples"):
        vibration_features(np.zeros(1000))
    with pytest.raises(ValueError, match="1-D"):
        vibration_features(np.zeros((N_SAMPLES, 2)))
    bad = np.random.default_rng(0).normal(size=N_SAMPLES)
    bad[5] = np.nan
    with pytest.raises(ValueError, match="NaN or infinite"):
        vibration_features(bad)


def test_deterministic() -> None:
    sig = _impacts(DEFECT_FREQUENCIES_HZ["bpfi"], seed=4)
    np.testing.assert_array_equal(vibration_features(sig), vibration_features(sig.copy()))


# ---- baseline ---------------------------------------------------------------------------


def _feature_matrix(n: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.vstack([vibration_features(rng.normal(0, 0.5, N_SAMPLES)) for _ in range(n)])


def test_baseline_normalises_its_own_window_to_zero_mean_unit_std() -> None:
    feats = _feature_matrix(20)
    z = normalize(feats, fit_baseline(feats))
    np.testing.assert_allclose(z.mean(axis=0), 0.0, atol=1e-9)
    varying = feats.std(axis=0) > 1e-9
    np.testing.assert_allclose(z.std(axis=0)[varying], 1.0, rtol=1e-6)


def test_baseline_uses_only_the_rows_it_is_given() -> None:
    feats = _feature_matrix(30)
    early = fit_baseline(feats[:15])
    changed = feats.copy()
    changed[15:] *= 100.0  # later (possibly degraded) data must not influence the baseline
    assert fit_baseline(changed[:15]) == early


def test_normalised_features_are_invariant_to_joint_channel_scaling() -> None:
    """A louder sensor (gain x5) gives the same z-scores as a quieter one."""
    feats = _feature_matrix(25)
    gain = np.ones(len(FEATURE_NAMES))
    for name in ("rms", "peak", "std"):
        gain[IDX[name]] = 5.0
    z1 = normalize(feats, fit_baseline(feats[:12]))
    z2 = normalize(feats * gain, fit_baseline(feats[:12] * gain))
    np.testing.assert_allclose(z1, z2, atol=1e-6)


def test_degradation_shows_up_as_large_z_scores() -> None:
    feats = _feature_matrix(20)
    baseline = fit_baseline(feats)
    z = normalize(vibration_features(_impacts(DEFECT_FREQUENCIES_HZ["bpfo"])), baseline)
    assert z[IDX["kurtosis"]] > 10
    assert z[IDX["env_bpfo"]] > 10


def test_baseline_validation_and_constant_feature_floor() -> None:
    with pytest.raises(ValueError, match="at least 10 baseline snapshots"):
        fit_baseline(_feature_matrix(BASELINE_MIN_SNAPSHOTS - 1))
    with pytest.raises(ValueError, match="expected an"):
        fit_baseline(np.zeros((20, 3)))
    nan_matrix = _feature_matrix(12)
    nan_matrix[3, 4] = np.nan
    with pytest.raises(ValueError, match="NaN or infinite"):
        fit_baseline(nan_matrix)
    constant = np.tile(_feature_matrix(1), (12, 1))  # zero variance in every feature
    z = normalize(constant[0], fit_baseline(constant))
    assert np.isfinite(z).all()
    with pytest.raises(ValueError, match="expected 17 features"):
        normalize(np.zeros(5), fit_baseline(constant))


def test_baseline_round_trip_and_window_length() -> None:
    baseline = fit_baseline(_feature_matrix(12))
    assert Baseline.from_dict(baseline.to_dict()) == baseline
    assert baseline_length(2156) == 324  # ceil(0.15 * 2156)
    assert baseline_length(20) == BASELINE_MIN_SNAPSHOTS


# ---- batch builder: training/serving parity ---------------------------------------------


def test_batch_builder_rows_equal_the_serving_function(tmp_path: Path) -> None:
    from app.ml.ims_profile import parse_snapshot_text

    rng = np.random.default_rng(7)
    test_dir = tmp_path / "set"
    test_dir.mkdir()
    names = ["2003.10.22.12.06.24", "2003.10.22.12.16.24", "2003.10.22.12.26.24"]
    texts = {}
    for name in names:
        signal = rng.normal(0, 0.4, (N_SAMPLES, 2))
        texts[name] = "\n".join("\t".join(f"{v:.6f}" for v in row) for row in signal)
        (test_dir / name).write_text(texts[name])
    (test_dir / names[1]).write_text("1\t2\n3\n")  # ragged -> reported, skipped

    out = tmp_path / "out" / "features.csv.gz"
    rows, errors = build_test_features(test_dir, out)
    assert rows == 4  # 2 readable files x 2 channels
    assert list(errors) == [names[1]]

    with gzip.open(out, "rt") as handle:
        written = list(csv.DictReader(handle))
    assert [r["file"] for r in written] == [names[0], names[0], names[2], names[2]]
    signal = parse_snapshot_text(texts[names[2]])
    for row in written[2:]:
        expected = vibration_features(signal[:, int(row["channel"]) - 1])
        got = np.array([float(row[name]) for name in FEATURE_NAMES])
        np.testing.assert_allclose(got, expected, rtol=1e-6, atol=1e-9)


# ---- real IMS features (committed gzip CSVs; raw data not needed) --------------------------

FEATURES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "processed" / "ims"
_TESTS = {"1st_test": (2156, 8), "2nd_test": (984, 4), "3rd_test": (6324, 4)}
_have_features = pytest.mark.skipif(
    not all((FEATURES_DIR / f"{t}_vibration_features.csv.gz").is_file() for t in _TESTS),
    reason="vibration features missing; run `python -m scripts.build_vibration_features`",
)


def _load_z(test: str) -> dict[int, np.ndarray]:
    """Per-channel z-scored features, baseline = first 15 % of that channel's snapshots."""
    with gzip.open(FEATURES_DIR / f"{test}_vibration_features.csv.gz", "rt") as handle:
        rows = list(csv.DictReader(handle))
    out: dict[int, np.ndarray] = {}
    for channel in sorted({int(r["channel"]) for r in rows}):
        raw = np.array(
            [[float(r[n]) for n in FEATURE_NAMES] for r in rows if int(r["channel"]) == channel]
        )
        out[channel] = normalize(raw, fit_baseline(raw[: baseline_length(len(raw))]))
    return out


def _late(z: np.ndarray, name: str) -> float:
    """Median of a z-scored feature over the last 2 % of the run."""
    return float(np.median(z[-max(20, len(z) // 50) :, IDX[name]]))


@_have_features
def test_real_features_have_one_row_per_file_and_channel_and_are_finite() -> None:
    for test, (files, channels) in _TESTS.items():
        z = _load_z(test)
        assert sorted(z) == list(range(1, channels + 1))
        assert all(len(v) == files for v in z.values())
        assert all(np.isfinite(v).all() for v in z.values())


@_have_features
def test_real_set1_kurtosis_separates_the_four_failing_channels() -> None:
    """Inner-race (ch5-6) and roller-element (ch7-8) defects vs the two healthy bearings."""
    z = _load_z("1st_test")
    kurt = {c: float(v[:, IDX["kurtosis"]].max()) for c, v in z.items()}
    assert all(kurt[c] > 50 for c in (5, 6, 7, 8))
    assert all(kurt[c] < 10 for c in (1, 2, 3, 4))


@_have_features
def test_real_outer_race_failures_peak_on_the_documented_bearing() -> None:
    """Sets 2 and 3: the failing bearing has the largest late RMS and BPFO-envelope rise."""
    for test, failing in (("2nd_test", 1), ("3rd_test", 3)):
        z = _load_z(test)
        for feature in ("rms", "env_bpfo"):
            late = {c: _late(v, feature) for c, v in z.items()}
            assert max(late, key=lambda c: late[c]) == failing, (test, feature, late)
        assert z[failing][:, IDX["rms"]].max() > 100
        assert z[failing][:, IDX["kurtosis"]].max() > 50


@_have_features
def test_real_early_life_stays_quiet_in_sets_1_and_2() -> None:
    """No channel exceeds |z| = 6 on RMS between the baseline window and 40 % of the run.

    Set 3 is deliberately excluded: its healthy channels 2 and 4 reach z of 10-16 in that
    period (baseline drift), a known limitation documented in docs/DATASETS.md.
    """
    for test in ("1st_test", "2nd_test"):
        for channel, z in _load_z(test).items():
            start = baseline_length(len(z))
            assert np.abs(z[start : int(0.4 * len(z)), IDX["rms"]]).max() < 6.0, (test, channel)
