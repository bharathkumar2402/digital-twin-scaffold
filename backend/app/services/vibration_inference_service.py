"""Vibration anomaly inference (Phase 3b, task 3b.7b).

Scores an asset channel's 1-second accelerometer snapshots with the versioned Isolation
Forest from MinIO, using the same `vibration_features` / `fit_baseline` / `normalize` code
as training. The caller supplies the channel's early-life (healthy) snapshots as the
baseline plus the recent snapshots to judge - raw waveforms are not stored in
`sensor_readings`, so the waveform source is the caller's concern.

Design rules:
  * Fails closed: a missing model, too short a baseline, nothing to score or an invalid
    waveform yields *no* assessment plus a reason - never a guessed verdict.
  * A model/feature layout mismatch is a deployment error and raises.
  * Output is a validated `VibrationAssessment` carrying model and dataset identity.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
from minio.error import S3Error
from pydantic import ValidationError
from urllib3.exceptions import HTTPError as Urllib3HTTPError

from app.ml.vibration_features import (
    BASELINE_MIN_SNAPSHOTS,
    fit_baseline,
    normalize,
    vibration_features,
)
from app.ml.vibration_train import Detector, load_vibration_model
from app.schemas.ml.vibration_result import VibrationAssessment

logger = logging.getLogger("services.vibration_inference")

SKIP_MODEL_UNAVAILABLE = "vibration_model_unavailable"
SKIP_SHORT_BASELINE = "insufficient_baseline_snapshots"
SKIP_NO_RECENT = "no_recent_snapshots"
SKIP_INVALID = "invalid_waveform"


class VibrationModelUnavailable(RuntimeError):
    """No vibration model has been published (or it cannot be read)."""


@dataclass(frozen=True)
class VibrationOutcome:
    """Either a validated assessment or the reason there is none - never a guess."""

    assessment: VibrationAssessment | None
    skip_reason: str | None = None


@dataclass(frozen=True)
class LoadedVibrationModel:
    detector: Detector
    version: str
    dataset_name: str
    dataset_version: str


_MODEL_CACHE: dict[str, LoadedVibrationModel] = {}


def load_vibration_model_cached(version: str | None = None) -> LoadedVibrationModel:
    """Load (and cache by resolved version); raises `VibrationModelUnavailable`."""
    if version is not None and version in _MODEL_CACHE:
        return _MODEL_CACHE[version]
    try:
        detector, _metrics, metadata = load_vibration_model(version)
    except (S3Error, Urllib3HTTPError, OSError, KeyError) as exc:
        raise VibrationModelUnavailable(f"no readable vibration model: {exc}") from exc
    resolved = str(metadata.get("model_version") or version or "")
    if not resolved:
        raise VibrationModelUnavailable("stored vibration model has no model_version")
    loaded = LoadedVibrationModel(
        detector=detector,
        version=resolved,
        dataset_name=str(metadata.get("dataset_name") or "unknown"),
        dataset_version=str(metadata.get("dataset_version") or "unknown"),
    )
    _MODEL_CACHE[resolved] = loaded
    return loaded


def _features(snapshots: Sequence[np.ndarray]) -> np.ndarray:
    return np.vstack([vibration_features(s) for s in snapshots])


def assess_channel(
    loaded: LoadedVibrationModel,
    *,
    asset_id: uuid.UUID,
    channel: int,
    baseline_snapshots: Sequence[np.ndarray],
    recent_snapshots: Sequence[np.ndarray],
    now: datetime | None = None,
) -> VibrationOutcome:
    """Verdict for one channel: baseline from its healthy snapshots, then score the recent ones."""
    if len(baseline_snapshots) < BASELINE_MIN_SNAPSHOTS:
        return VibrationOutcome(None, SKIP_SHORT_BASELINE)
    if not recent_snapshots:
        return VibrationOutcome(None, SKIP_NO_RECENT)
    try:
        baseline = fit_baseline(_features(baseline_snapshots))
        z = normalize(_features(recent_snapshots), baseline)
        scores = np.clip(loaded.detector.scores(z), 0.0, 1.0)
        exceeds = scores > loaded.detector.threshold
        run = 0
        for flag in exceeds[::-1]:
            if not flag:
                break
            run += 1
        k = loaded.detector.k_consecutive
        assessment = VibrationAssessment(
            asset_id=asset_id,
            channel=channel,
            alarm=run >= k,
            latest_score=float(scores[-1]),
            max_recent_score=float(scores.max()),
            threshold=float(np.clip(loaded.detector.threshold, 0.0, 1.0)),
            consecutive_exceedances=run,
            k_consecutive=k,
            n_baseline_snapshots=len(baseline_snapshots),
            n_scored_snapshots=len(recent_snapshots),
            model_version=loaded.version,
            dataset_name=loaded.dataset_name,
            dataset_version=loaded.dataset_version,
            computed_at=now or datetime.now(UTC),
        )
    except (ValueError, ValidationError) as exc:
        # vibration_features raises ValueError on wrong length / NaN; a non-finite score or
        # inconsistent field fails the schema. Either way: no verdict.
        logger.warning("vibration skipped for asset %s ch %s: %s", asset_id, channel, exc)
        return VibrationOutcome(None, SKIP_INVALID)
    return VibrationOutcome(assessment, None)


def assess_asset_channels(
    *,
    asset_id: uuid.UUID,
    channels: dict[int, tuple[Sequence[np.ndarray], Sequence[np.ndarray]]],
    model_version: str | None = None,
    now: datetime | None = None,
) -> dict[int, VibrationOutcome]:
    """Outcomes per channel; `channels[c] = (baseline_snapshots, recent_snapshots)`."""
    try:
        loaded = load_vibration_model_cached(model_version)
    except VibrationModelUnavailable as exc:
        logger.warning("vibration unavailable: %s", exc)
        return {c: VibrationOutcome(None, SKIP_MODEL_UNAVAILABLE) for c in channels}
    return {
        c: assess_channel(
            loaded,
            asset_id=asset_id,
            channel=c,
            baseline_snapshots=base,
            recent_snapshots=recent,
            now=now,
        )
        for c, (base, recent) in channels.items()
    }


def assessments_to_context(
    outcomes_by_asset: dict[uuid.UUID, dict[int, VibrationOutcome]],
) -> list[dict[str, Any]]:
    """JSON-safe assessments (skips dropped) for the agent context."""
    return [
        o.assessment.model_dump(mode="json")
        for per_channel in outcomes_by_asset.values()
        for o in per_channel.values()
        if o.assessment is not None
    ]
