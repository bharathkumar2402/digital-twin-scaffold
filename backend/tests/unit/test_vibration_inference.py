"""Vibration inference service + Agent 3 priority tool (Phase 3b, task 3b.7b).

A stub scorer (kurtosis-z based) stands in for the Isolation Forest so these tests need no
MinIO; the real artifact path is covered by the training tests.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from app.agents.maintenance_inventory import plan_maintenance_and_inventory
from app.agents.tools.vibration_priority_tool import (
    CRITICAL_FLOOR,
    HIGH_FLOOR,
    apply_vibration_signals,
)
from app.ml.vibration_features import FEATURE_NAMES, N_SAMPLES
from app.ml.vibration_train import Detector
from app.schemas.ml.vibration_result import VibrationAssessment
from app.services import vibration_inference_service as svc

KURT = FEATURE_NAMES.index("kurtosis")
NOW = datetime(2026, 1, 1, tzinfo=UTC)
ASSET = uuid.uuid4()


class StubScorer:
    """Score rises with the normalised kurtosis feature, squashed into (0, 1)."""

    def anomaly_score(self, z: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-np.abs(z[:, KURT]) + 4.0))


def _loaded(k: int = 3) -> svc.LoadedVibrationModel:
    return svc.LoadedVibrationModel(
        detector=Detector(scorer=StubScorer(), threshold=0.5, k_consecutive=k),  # type: ignore[arg-type]
        version="v-test",
        dataset_name="NASA IMS Bearing",
        dataset_version="ims_test",
    )


def _healthy(rng: np.random.Generator) -> np.ndarray:
    return rng.normal(0, 1, N_SAMPLES)


def _faulty(rng: np.random.Generator) -> np.ndarray:
    x = rng.normal(0, 1, N_SAMPLES)
    x[rng.integers(0, N_SAMPLES, 40)] += 40.0  # impulsive impacts -> huge kurtosis
    return x


def _assess(base: list[np.ndarray], recent: list[np.ndarray], k: int = 3) -> svc.VibrationOutcome:
    return svc.assess_channel(
        _loaded(k), asset_id=ASSET, channel=0, baseline_snapshots=base,
        recent_snapshots=recent, now=NOW,
    )  # fmt: skip


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(7)


def test_healthy_recent_snapshots_do_not_alarm(rng: np.random.Generator) -> None:
    base = [_healthy(rng) for _ in range(12)]
    out = _assess(base, [_healthy(rng) for _ in range(6)])
    assert out.assessment is not None and not out.assessment.alarm
    assert out.assessment.model_version == "v-test"
    assert out.assessment.dataset_name == "NASA IMS Bearing"


def test_sustained_fault_alarms(rng: np.random.Generator) -> None:
    base = [_healthy(rng) for _ in range(12)]
    out = _assess(base, [_healthy(rng), *[_faulty(rng) for _ in range(4)]])
    a = out.assessment
    assert a is not None and a.alarm and a.consecutive_exceedances >= 3


def test_single_spike_below_k_does_not_alarm(rng: np.random.Generator) -> None:
    base = [_healthy(rng) for _ in range(12)]
    out = _assess(base, [_healthy(rng), _faulty(rng), _healthy(rng)])
    assert out.assessment is not None and not out.assessment.alarm


def test_old_fault_followed_by_recovery_does_not_alarm(rng: np.random.Generator) -> None:
    # only the *trailing* run counts
    base = [_healthy(rng) for _ in range(12)]
    recent = [_faulty(rng) for _ in range(4)] + [_healthy(rng)]
    out = _assess(base, recent)
    assert out.assessment is not None and out.assessment.consecutive_exceedances == 0


def test_short_baseline_fails_closed(rng: np.random.Generator) -> None:
    out = _assess([_healthy(rng) for _ in range(3)], [_faulty(rng)])
    assert out.assessment is None and out.skip_reason == svc.SKIP_SHORT_BASELINE


def test_no_recent_snapshots_fails_closed(rng: np.random.Generator) -> None:
    out = _assess([_healthy(rng) for _ in range(12)], [])
    assert out.assessment is None and out.skip_reason == svc.SKIP_NO_RECENT


@pytest.mark.parametrize("bad", ["nan", "short"])
def test_invalid_waveform_fails_closed(rng: np.random.Generator, bad: str) -> None:
    base = [_healthy(rng) for _ in range(12)]
    wave = _healthy(rng)
    wave = np.where(np.arange(N_SAMPLES) == 5, np.nan, wave) if bad == "nan" else wave[:100]
    out = _assess(base, [wave])
    assert out.assessment is None and out.skip_reason == svc.SKIP_INVALID


def test_missing_model_degrades_to_reasoned_skip(
    monkeypatch: pytest.MonkeyPatch, rng: np.random.Generator
) -> None:
    def boom(version: str | None = None) -> Any:
        raise svc.VibrationModelUnavailable("no model")

    monkeypatch.setattr(svc, "load_vibration_model_cached", boom)
    res = svc.assess_asset_channels(
        asset_id=ASSET, channels={0: ([_healthy(rng)] * 12, [_healthy(rng)])}
    )
    assert res[0].assessment is None and res[0].skip_reason == svc.SKIP_MODEL_UNAVAILABLE
    assert svc.assessments_to_context({ASSET: res}) == []


def test_schema_rejects_inconsistent_alarm() -> None:
    fields: dict[str, Any] = dict(
        asset_id=ASSET, channel=0, alarm=True, latest_score=0.9, max_recent_score=0.9,
        threshold=0.5, consecutive_exceedances=1, k_consecutive=5, n_baseline_snapshots=10,
        n_scored_snapshots=3, model_version="v", dataset_name="d", dataset_version="1",
        computed_at=NOW,
    )  # fmt: skip
    with pytest.raises(ValidationError):
        VibrationAssessment(**fields)
    with pytest.raises(ValidationError):
        VibrationAssessment(**{**fields, "alarm": False, "latest_score": 1.5})


# ---- Agent 3 tool ----------------------------------------------------------------------


def _assessment(asset_id: uuid.UUID, alarm: bool) -> dict[str, Any]:
    k = 3
    return VibrationAssessment(
        asset_id=asset_id, channel=0, alarm=alarm, latest_score=0.9, max_recent_score=0.9,
        threshold=0.5, consecutive_exceedances=k if alarm else 0, k_consecutive=k,
        n_baseline_snapshots=10, n_scored_snapshots=5, model_version="v",
        dataset_name="d", dataset_version="1", computed_at=NOW,
    ).model_dump(mode="json")  # fmt: skip


def test_alarm_lifts_to_high_and_low_rul_to_critical() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assets = [
        {"asset_id": a, "risk_score": 10.0},
        {"asset_id": b, "risk_score": 10.0, "rul_cycles": 12.0},
        {"asset_id": c, "risk_score": 10.0},
    ]
    out = apply_vibration_signals(assets, [_assessment(a, True), _assessment(b, True)])
    by = {x["asset_id"]: x for x in out}
    assert by[a]["risk_score"] == HIGH_FLOOR and by[a]["vibration_alarm"]
    assert by[b]["risk_score"] == CRITICAL_FLOOR
    assert by[c]["risk_score"] == 10.0 and "vibration_alarm" not in by[c]
    assert assets[0]["risk_score"] == 10.0  # input not mutated


def test_never_lowers_score_and_ignores_no_alarm() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    out = apply_vibration_signals(
        [{"asset_id": a, "risk_score": 90.0}, {"asset_id": b, "risk_score": 20.0}],
        [_assessment(a, True), _assessment(b, False)],
    )
    assert out[0]["risk_score"] == 90.0 and out[1]["risk_score"] == 20.0


def test_invalid_assessment_is_dropped_not_trusted() -> None:
    a = uuid.uuid4()
    bogus = {**_assessment(a, True), "alarm": False, "consecutive_exceedances": 3}
    garbage = {"asset_id": str(a), "alarm": True}
    out = apply_vibration_signals([{"asset_id": a, "risk_score": 5.0}], [bogus, garbage])
    assert out[0]["risk_score"] == 5.0


def test_alarm_changes_the_schedule_priority() -> None:
    a = uuid.uuid4()
    facility = uuid.uuid4()
    quiet = plan_maintenance_and_inventory(
        facility_id=facility, scored_assets=[{"asset_id": a, "risk_score": 10.0}]
    )
    alarmed = plan_maintenance_and_inventory(
        facility_id=facility,
        scored_assets=[{"asset_id": a, "risk_score": 10.0}],
        vibration_assessments=[_assessment(a, True)],
    )
    assert quiet.scheduled_items[0].priority == "low"
    assert alarmed.scheduled_items[0].priority == "high"
