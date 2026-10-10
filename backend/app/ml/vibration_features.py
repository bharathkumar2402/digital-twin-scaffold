"""Feature extraction for the vibration anomaly detector (Phase 3b).

`vibration_features` is the single function used for both training (batch over the IMS
files) and live serving, so a model trained on these features sees the same math at
inference time. One call turns one channel's 1-second snapshot into a small fixed-order
feature vector:

  * time domain      - RMS, peak, crest factor, std, skewness, kurtosis
  * spectral shape   - fraction of spectral power in 8 log-spaced bands (20 Hz - 10 kHz)
  * envelope spectra - fraction of the envelope-spectrum energy sitting at each bearing
                       defect frequency (BPFO / BPFI / BSF) and its 2nd harmonic

Envelope analysis (band-pass -> analytic signal -> envelope -> FFT) is the standard way to
expose the repetitive impacts of a rolling-element bearing defect; the defect shows up as
a line at the defect frequency in the envelope spectrum even when it is invisible in the
raw spectrum.

Features are then scaled per channel against that channel's own early-life statistics
(`fit_baseline` / `normalize`), mirroring deployment where each new sensor has a
calibration period. Do not mix baselines across channels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

SAMPLE_RATE_HZ = 20_000.0
N_SAMPLES = 20_480
SPECTRAL_RANGE_HZ = (20.0, 10_000.0)
N_BANDS = 8
# Resonance band demodulated for the envelope analysis. A modelling assumption (typical
# for accelerometer-mounted bearing housings), not taken from NASA's documentation.
ENVELOPE_BAND_HZ = (2_000.0, 8_000.0)
ENVELOPE_ANALYSIS_RANGE_HZ = (5.0, 1_000.0)
DEFECT_HALF_WIDTH = 0.03  # +/- 3 % around each defect frequency (and its 2nd harmonic)

# Rexnord ZA-2115 geometry as cited in the IMS bearing literature (Qiu et al., 2006): 16
# rollers, pitch diameter 2.815 in, roller diameter 0.331 in, contact angle 15.17 deg.
# NOT stated in NASA's readme and not re-verified against the paper in this repo; the
# resulting lines are checked empirically against the data in the unit tests.
SHAFT_HZ = 2000.0 / 60.0
_N_ROLLERS = 16
_PITCH_DIAMETER = 2.815
_ROLLER_DIAMETER = 0.331
_CONTACT_ANGLE_RAD = math.radians(15.17)
_X = _ROLLER_DIAMETER / _PITCH_DIAMETER * math.cos(_CONTACT_ANGLE_RAD)
DEFECT_FREQUENCIES_HZ: dict[str, float] = {
    "bpfo": _N_ROLLERS / 2 * (1 - _X) * SHAFT_HZ,  # outer race
    "bpfi": _N_ROLLERS / 2 * (1 + _X) * SHAFT_HZ,  # inner race
    "bsf": _PITCH_DIAMETER / (2 * _ROLLER_DIAMETER) * (1 - _X**2) * SHAFT_HZ,  # roller
}

_BAND_EDGES = np.geomspace(SPECTRAL_RANGE_HZ[0], SPECTRAL_RANGE_HZ[1], N_BANDS + 1)

FEATURE_NAMES: list[str] = [
    "rms",
    "peak",
    "crest",
    "std",
    "skew",
    "kurtosis",
    *[f"band_{i}" for i in range(N_BANDS)],
    *[f"env_{name}" for name in DEFECT_FREQUENCIES_HZ],
]

BASELINE_STD_FLOOR_REL = 1e-3  # baseline std is floored at 0.1 % of |mean|
BASELINE_STD_FLOOR_ABS = 1e-12
BASELINE_MIN_SNAPSHOTS = 10
BASELINE_FRACTION = 0.15  # share of a test's earliest snapshots treated as the healthy baseline


def _validate(signal: np.ndarray) -> np.ndarray:
    arr = np.asarray(signal, dtype=np.float64)
    if arr.ndim != 1:
        raise ValueError(f"expected a 1-D signal, got shape {arr.shape}")
    if arr.size != N_SAMPLES:
        raise ValueError(f"expected {N_SAMPLES} samples (1 s at 20 kHz), got {arr.size}")
    if not np.isfinite(arr).all():
        raise ValueError("signal contains NaN or infinite values")
    return arr


def _band_mask(freqs: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return (freqs >= lo) & (freqs < hi)


def _envelope(signal: np.ndarray) -> np.ndarray:
    """Amplitude envelope of the signal band-passed to ENVELOPE_BAND_HZ (FFT analytic signal)."""
    n = signal.size
    spectrum = np.fft.fft(signal)
    freqs = np.fft.fftfreq(n, d=1.0 / SAMPLE_RATE_HZ)
    analytic_gain = np.zeros(n)
    analytic_gain[0] = 1.0
    analytic_gain[1 : (n + 1) // 2] = 2.0
    if n % 2 == 0:
        analytic_gain[n // 2] = 1.0
    in_band = (np.abs(freqs) >= ENVELOPE_BAND_HZ[0]) & (np.abs(freqs) <= ENVELOPE_BAND_HZ[1])
    return np.abs(np.fft.ifft(spectrum * analytic_gain * in_band))


def vibration_features(signal: np.ndarray) -> np.ndarray:
    """Feature vector (ordered as `FEATURE_NAMES`) for one channel's 1-second snapshot.

    A perfectly constant (dead-sensor) signal returns an all-zero vector rather than NaNs.
    """
    x = _validate(signal)
    mean = x.mean()
    centered = x - mean
    var = float((centered**2).mean())
    rms = float(np.sqrt((x**2).mean()))
    peak = float(np.abs(x).max())
    if var <= 0.0 or rms <= 0.0:
        return np.zeros(len(FEATURE_NAMES))
    std = math.sqrt(var)
    skew = float((centered**3).mean() / std**3)
    kurt = float((centered**4).mean() / var**2)

    freqs = np.fft.rfftfreq(x.size, d=1.0 / SAMPLE_RATE_HZ)
    power = np.abs(np.fft.rfft(centered)) ** 2
    total = float(power[_band_mask(freqs, *SPECTRAL_RANGE_HZ)].sum())
    bands = [
        float(power[_band_mask(freqs, lo, hi)].sum()) / total if total > 0 else 0.0
        for lo, hi in zip(_BAND_EDGES[:-1], _BAND_EDGES[1:], strict=True)
    ]

    env = _envelope(centered)
    env_power = np.abs(np.fft.rfft(env - env.mean())) ** 2
    env_total = float(env_power[_band_mask(freqs, *ENVELOPE_ANALYSIS_RANGE_HZ)].sum())
    defects = []
    for f0 in DEFECT_FREQUENCIES_HZ.values():
        energy = sum(
            float(
                env_power[
                    _band_mask(
                        freqs, h * f0 * (1 - DEFECT_HALF_WIDTH), h * f0 * (1 + DEFECT_HALF_WIDTH)
                    )
                ].sum()
            )
            for h in (1, 2)
        )
        defects.append(energy / env_total if env_total > 0 else 0.0)

    return np.array([rms, peak, peak / rms, std, skew, kurt, *bands, *defects], dtype=np.float64)


def baseline_length(n_snapshots: int) -> int:
    """How many of a channel's earliest snapshots form its baseline window."""
    return max(BASELINE_MIN_SNAPSHOTS, math.ceil(BASELINE_FRACTION * n_snapshots))


@dataclass(frozen=True)
class Baseline:
    """Per-channel early-life statistics used to scale features."""

    mean: tuple[float, ...]
    std: tuple[float, ...]
    n_snapshots: int

    def to_dict(self) -> dict[str, Any]:
        return {"mean": list(self.mean), "std": list(self.std), "n_snapshots": self.n_snapshots}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Baseline:
        return cls(
            mean=tuple(float(x) for x in data["mean"]),
            std=tuple(float(x) for x in data["std"]),
            n_snapshots=int(data["n_snapshots"]),
        )


def fit_baseline(features: np.ndarray) -> Baseline:
    """Fit a baseline from the feature rows of a channel's early-life (healthy) snapshots."""
    if features.ndim != 2 or features.shape[1] != len(FEATURE_NAMES):
        raise ValueError(
            f"expected an (n, {len(FEATURE_NAMES)}) feature matrix, got {features.shape}"
        )
    if features.shape[0] < BASELINE_MIN_SNAPSHOTS:
        raise ValueError(
            f"need at least {BASELINE_MIN_SNAPSHOTS} baseline snapshots, got {features.shape[0]}"
        )
    if not np.isfinite(features).all():
        raise ValueError("baseline features contain NaN or infinite values")
    mean = features.mean(axis=0)
    std = np.maximum(
        features.std(axis=0),
        np.maximum(BASELINE_STD_FLOOR_REL * np.abs(mean), BASELINE_STD_FLOOR_ABS),
    )
    return Baseline(
        mean=tuple(float(m) for m in mean),
        std=tuple(float(s) for s in std),
        n_snapshots=int(features.shape[0]),
    )


def normalize(features: np.ndarray, baseline: Baseline) -> np.ndarray:
    """Z-score `features` (a vector or an (n, n_features) matrix) against `baseline`."""
    if features.shape[-1] != len(FEATURE_NAMES):
        raise ValueError(f"expected {len(FEATURE_NAMES)} features, got {features.shape[-1]}")
    return (features - np.asarray(baseline.mean)) / np.asarray(baseline.std)
