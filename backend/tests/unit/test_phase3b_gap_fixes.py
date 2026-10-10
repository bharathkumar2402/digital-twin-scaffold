"""Phase 3b gap fixes: no synthetic training path, model+dataset identity recorded."""

import ast
import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.schemas.ml.vibration_result import VibrationAssessment
from app.services import vibration_inference_service as vis
from app.services.vibration_inference_service import VibrationOutcome, vibration_factors
from scripts.train_risk_model import parse_args

BACKEND = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_risk_training_cli_has_no_synthetic_option() -> None:
    for flag in ("--synthetic", "--num-samples"):
        with pytest.raises(SystemExit):
            parse_args([flag])
    assert not hasattr(parse_args([]), "synthetic")


def _imported_modules(path: Path) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            mods.add(base)
            mods.update(f"{base}.{a.name}" for a in node.names)
    return {part for m in mods for part in m.split(".")}


def test_only_tests_may_import_the_invented_synthetic_dataset() -> None:
    scanned = [*(BACKEND / "app").rglob("*.py"), *(BACKEND / "scripts").rglob("*.py")]
    scanned = [p for p in scanned if p.name != "synthetic_data.py"]
    assert len(scanned) > 50
    offenders = [p.name for p in scanned if "synthetic_data" in _imported_modules(p)]
    assert offenders == [], f"non-test code must not use invented training data: {offenders}"


def _assessment(alarm: bool, channel: int = 0, score: float = 0.9) -> VibrationAssessment:
    return VibrationAssessment(
        asset_id=uuid.uuid4(),
        channel=channel,
        alarm=alarm,
        latest_score=score,
        max_recent_score=score,
        threshold=0.5,
        consecutive_exceedances=5 if alarm else 0,
        k_consecutive=5,
        n_baseline_snapshots=20,
        n_scored_snapshots=12,
        model_version="v1",
        dataset_name="NASA IMS Bearing",
        dataset_version="ims_test",
        computed_at=NOW,
    )


def test_vibration_factors_record_verdict_with_model_and_dataset() -> None:
    f = vibration_factors(
        {
            0: VibrationOutcome(_assessment(False, 0, 0.3)),
            1: VibrationOutcome(_assessment(True, 1, 0.95)),
            2: VibrationOutcome(None, vis.SKIP_INVALID),
        }
    )
    assert f["vibration_alarm"] == 1 and f["vibration_alarm_channels"] == 1
    assert f["vibration_channels_scored"] == 2 and f["vibration_max_score"] == 0.95
    assert f["vibration_model_version"] == "v1"
    assert f["vibration_dataset_name"] == "NASA IMS Bearing"
    assert all(isinstance(v, (int, float, str)) for v in f.values())  # fits RiskScoreResult


def test_vibration_factors_all_skipped_records_reason_and_empty_records_nothing() -> None:
    skipped = {
        0: VibrationOutcome(None, vis.SKIP_SHORT_BASELINE),
        1: VibrationOutcome(None, vis.SKIP_INVALID),
    }
    assert vibration_factors(skipped) == {
        "vibration_unavailable_reason": "insufficient_baseline_snapshots,invalid_waveform"
    }
    assert vibration_factors({}) == {}


def test_collect_outcomes_survives_store_failure_and_drops_channelless_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good, bad, empty = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    def fake(tenant_id: uuid.UUID, asset_id: uuid.UUID, version: str | None) -> Any:
        if asset_id == bad:
            raise ConnectionError("minio down")
        return {} if asset_id == empty else {0: VibrationOutcome(_assessment(True))}

    monkeypatch.setattr(vis, "_assess_asset_from_store", fake)
    out = asyncio.run(vis.collect_vibration_outcomes(uuid.uuid4(), [good, bad, empty]))
    assert list(out) == [good]
