"""Degrading-bearing vibration simulator calibrated from IMS (Phase 3b, task 3b.8).

A snapshot is Gaussian noise at the real healthy RMS plus a fault component: noise confined
to the envelope-analysis resonance band, amplitude-modulated at the outer-race defect
frequency (BPFO). At run fraction `health` in [0, 1]:

  * total RMS follows the calibrated IMS RMS growth curve exactly (in expectation), and
  * the modulation depth is solved so kurtosis follows the calibrated IMS kurtosis growth
    curve, relative to the Gaussian baseline of 3.

Nothing here is a hand-picked number: levels, growth curves and the per-asset spread all come
from `sim_calibration`. Limitation: the healthy simulated signal is Gaussian (kurtosis 3.0)
while the real healthy median is 3.43, and one defect type (outer race) is simulated.

This module is for the live demo only. It must never feed model training (CLAUDE.md rule 8).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from app.ml.vibration_features import (
    DEFECT_FREQUENCIES_HZ,
    ENVELOPE_BAND_HZ,
    N_SAMPLES,
    SAMPLE_RATE_HZ,
)

_M_MAX = 1.0


def _monotone_curve(fractions: list[float], ratios: list[float]) -> tuple[np.ndarray, np.ndarray]:
    """Growth curve forced to start at 1 and never decrease (real medians are noisy)."""
    r = np.maximum.accumulate(np.maximum(np.asarray(ratios, dtype=float), 1.0))
    r[0] = 1.0
    return np.asarray(fractions, dtype=float), r


def target_ratios(calibration: dict[str, Any], health: float) -> tuple[float, float]:
    """(RMS ratio, kurtosis ratio) relative to healthy at run fraction `health`."""
    if not 0.0 <= health <= 1.0:
        raise ValueError(f"health must be in [0, 1], got {health}")
    vib = calibration["vibration"]
    fr, rms = _monotone_curve(vib["growth_fractions"], vib["rms_growth_ratio"])
    _, kurt = _monotone_curve(vib["growth_fractions"], vib["kurtosis_growth_ratio"])
    # Below the first measured fraction the bearing is healthy.
    return float(np.interp(health, fr, rms)), float(np.interp(health, fr, kurt))


def _q(m: float) -> float:
    """E[e^4] for the unit-power modulation e = (1 + m cos) / sqrt(1 + m^2 / 2)."""
    return (1 + 3 * m**2 + 3 * m**4 / 8) / (1 + m**2 / 2) ** 2


def modulation_depth(rms_ratio: float, kurt_ratio: float) -> tuple[float, float]:
    """(fault power fraction sigma_f^2 / sigma_0^2, modulation depth m) hitting both targets."""
    sigma_f2 = max(rms_ratio**2 - 1.0, 0.0)
    if sigma_f2 == 0.0:
        return 0.0, 0.0
    total = 1.0 + sigma_f2
    needed = (3.0 * kurt_ratio * total**2 - 3.0 - 6.0 * sigma_f2) / (3.0 * sigma_f2**2)
    if needed <= 1.0:
        return sigma_f2, 0.0
    if needed >= _q(_M_MAX):
        return sigma_f2, _M_MAX
    lo, hi = 0.0, _M_MAX
    for _ in range(50):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _q(mid) < needed else (lo, mid)
    return sigma_f2, (lo + hi) / 2


def _band_noise(rng: np.random.Generator) -> np.ndarray:
    spectrum = np.fft.rfft(rng.standard_normal(N_SAMPLES))
    freqs = np.fft.rfftfreq(N_SAMPLES, d=1.0 / SAMPLE_RATE_HZ)
    spectrum[(freqs < ENVELOPE_BAND_HZ[0]) | (freqs > ENVELOPE_BAND_HZ[1])] = 0.0
    noise = np.fft.irfft(spectrum, n=N_SAMPLES)
    return noise / float(np.sqrt(np.mean(noise**2)))


def simulate_waveform(
    calibration: dict[str, Any],
    health: float,
    rng: np.random.Generator,
    *,
    channel_scale: float = 1.0,
) -> np.ndarray:
    """One 1-second, 20 kHz snapshot (`N_SAMPLES` floats) at run fraction `health`.

    `channel_scale` is the channel's own healthy RMS multiplier (see `draw_channel_scale`).
    """
    sigma0 = float(calibration["vibration"]["healthy_rms_median"]) * channel_scale
    rms_ratio, kurt_ratio = target_ratios(calibration, health)
    sigma_f2, m = modulation_depth(rms_ratio, kurt_ratio)
    signal = sigma0 * rng.standard_normal(N_SAMPLES)
    if sigma_f2 > 0.0:
        t = np.arange(N_SAMPLES) / SAMPLE_RATE_HZ
        phase = rng.uniform(0, 2 * np.pi)
        envelope = (
            1.0 + m * np.cos(2 * np.pi * DEFECT_FREQUENCIES_HZ["bpfo"] * t + phase)
        ) / np.sqrt(1.0 + m**2 / 2)
        signal = signal + sigma0 * np.sqrt(sigma_f2) * envelope * _band_noise(rng)
    return signal


def draw_channel_scale(calibration: dict[str, Any], rng: np.random.Generator) -> float:
    """Per-channel healthy RMS multiplier, log-normal with the IMS cross-channel spread."""
    spread = float(calibration["vibration"]["healthy_rms_log_spread_across_channels"])
    return float(np.exp(rng.normal(0.0, spread)))
