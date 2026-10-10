"""Simulator calibration, degradation simulators, waveform store, wiring (Phase 3b, task 3b.8)."""

import ast
import asyncio
import io
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.agents import maintenance_inventory as mi
from app.core.config import settings
from app.ml import vibration_sim as vs
from app.ml.degradation_sim import DegradationSimulator
from app.ml.engine_sim import CHANNELS, channel_telemetry, cycle_values, draw_engine
from app.ml.sim_calibration import calibrate, load_calibration, save_calibration
from app.ml.vibration_features import (
    FEATURE_NAMES,
    N_SAMPLES,
    fit_baseline,
    normalize,
    vibration_features,
)
from app.schemas.ml.vibration_result import VibrationAssessment
from app.services import vibration_inference_service as vis
from app.services import waveform_store as ws
from app.services.ml.dataset_mapping import CMAPSS_TELEMETRY_TYPES
from scripts.iot_data_generator import degradation_readings, parse_args

BACKEND = Path(__file__).resolve().parents[2]
FEATURES_DIR = BACKEND / "data" / "processed" / "ims"
CMAPSS_DIR = BACKEND / "data" / "raw" / "cmapss"

needs_data = pytest.mark.skipif(
    not (FEATURES_DIR / "1st_test_vibration_features.csv.gz").exists()
    or not (CMAPSS_DIR / "train_FD001.txt").exists(),
    reason="real IMS / C-MAPSS data not present",
)


@pytest.fixture(scope="module")
def cal() -> dict[str, Any]:
    if not (FEATURES_DIR / "1st_test_vibration_features.csv.gz").exists():
        pytest.skip("real data not present")
    return calibrate(FEATURES_DIR, CMAPSS_DIR, "FD001")


# ---- calibration reads real data, deterministically -----------------------------------------


@needs_data
def test_calibration_is_deterministic_and_roundtrips(tmp_path: Path, cal: dict[str, Any]) -> None:
    again = calibrate(FEATURES_DIR, CMAPSS_DIR, "FD001")
    assert again == cal
    save_calibration(cal, tmp_path / "c.json")
    assert load_calibration(tmp_path / "c.json") == cal


@needs_data
def test_calibration_records_provenance_and_real_scale(cal: dict[str, Any]) -> None:
    assert "IMS" in cal["vibration"]["source"] and "C-MAPSS" in cal["engines"]["source"]
    assert cal["vibration"]["n_failing_channels"] == 6 and cal["vibration"]["n_channels"] == 16
    assert cal["engines"]["n_engines"] == 100
    assert 100 < cal["engines"]["life_cycles_quantiles"]["50"] < 300
    assert (
        cal["engines"]["progress_curve"][0] == 0.0 and cal["engines"]["progress_curve"][-1] == 1.0
    )
    assert set(cal["engines"]["channels"]) == set(CHANNELS)


def test_load_rejects_unknown_version(tmp_path: Path) -> None:
    (tmp_path / "c.json").write_text('{"calibration_version": 999}')
    with pytest.raises(ValueError):
        load_calibration(tmp_path / "c.json")


# ---- vibration simulator --------------------------------------------------------------------


@needs_data
def test_targets_start_at_one_and_end_at_calibrated_growth(cal: dict[str, Any]) -> None:
    assert vs.target_ratios(cal, 0.0) == (1.0, 1.0)
    rms_end, kurt_end = vs.target_ratios(cal, 1.0)
    assert rms_end == pytest.approx(cal["vibration"]["rms_growth_ratio"][-1])
    assert kurt_end == pytest.approx(cal["vibration"]["kurtosis_growth_ratio"][-1])
    with pytest.raises(ValueError):
        vs.target_ratios(cal, 1.2)


@needs_data
def test_modulation_depth_hits_the_kurtosis_target() -> None:
    sigma_f2, m = vs.modulation_depth(1.6, 1.2)
    total = 1 + sigma_f2
    kurt = (3 + 6 * sigma_f2 + 3 * sigma_f2**2 * vs._q(m)) / total**2
    assert kurt == pytest.approx(3.0 * 1.2, rel=1e-6)
    assert vs.modulation_depth(1.0, 1.0) == (0.0, 0.0)


def _stats(cal: dict[str, Any], health: float, n: int = 12) -> tuple[float, float]:
    rng = np.random.default_rng(3)
    rms, kurt = [], []
    for _ in range(n):
        f = vibration_features(vs.simulate_waveform(cal, health, rng))
        rms.append(f[FEATURE_NAMES.index("rms")])
        kurt.append(f[FEATURE_NAMES.index("kurtosis")])
    return float(np.mean(rms)), float(np.mean(kurt))


@needs_data
def test_simulated_waveform_matches_calibrated_levels(cal: dict[str, Any]) -> None:
    healthy_rms, healthy_kurt = _stats(cal, 0.0)
    assert healthy_rms == pytest.approx(cal["vibration"]["healthy_rms_median"], rel=0.03)
    assert healthy_kurt == pytest.approx(3.0, abs=0.2)  # documented: Gaussian healthy signal
    end_rms, end_kurt = _stats(cal, 1.0)
    rms_ratio, kurt_ratio = vs.target_ratios(cal, 1.0)
    assert end_rms / healthy_rms == pytest.approx(rms_ratio, rel=0.05)
    assert end_kurt / healthy_kurt == pytest.approx(kurt_ratio, rel=0.08)


@needs_data
def test_degradation_is_detectable_against_own_baseline_and_healthy_is_not(
    cal: dict[str, Any],
) -> None:
    rng = np.random.default_rng(5)
    scale = vs.draw_channel_scale(cal, rng)
    feats = lambda h: vibration_features(  # noqa: E731
        vs.simulate_waveform(cal, h, rng, channel_scale=scale)
    )
    baseline = fit_baseline(np.vstack([feats(0.0) for _ in range(20)]))
    healthy_z = normalize(np.vstack([feats(0.0) for _ in range(10)]), baseline)
    late_z = normalize(np.vstack([feats(1.0) for _ in range(10)]), baseline)
    rms = FEATURE_NAMES.index("rms")
    env = FEATURE_NAMES.index("env_bpfo")
    assert np.abs(healthy_z[:, rms]).max() < 4
    assert late_z[:, rms].min() > 4
    assert late_z[:, env].mean() > healthy_z[:, env].mean() + 3  # BPFO line appears


def test_channel_scale_is_positive_and_spread_matches_calibration() -> None:
    calibration = {"vibration": {"healthy_rms_log_spread_across_channels": 0.35}}
    rng = np.random.default_rng(0)
    logs = np.log([vs.draw_channel_scale(calibration, rng) for _ in range(4000)])
    assert logs.std() == pytest.approx(0.35, rel=0.1)


# ---- engine simulator -----------------------------------------------------------------------


@needs_data
def test_engine_channels_map_to_the_project_schema(cal: dict[str, Any]) -> None:
    assert {channel_telemetry(c)[0] for c in CHANNELS} == set(CMAPSS_TELEMETRY_TYPES)
    eng = draw_engine(cal, np.random.default_rng(1))
    values = cycle_values(cal, eng, 1, np.random.default_rng(2))
    assert set(values) == set(CHANNELS) and all(np.isfinite(v) for v in values.values())
    life = cal["engines"]["life_cycles_quantiles"]
    assert float(life["0"]) <= eng.life_cycles <= float(life["100"])
    with pytest.raises(ValueError):
        cycle_values(cal, eng, 0, np.random.default_rng(2))


@needs_data
def test_engine_drifts_toward_the_calibrated_end_state(cal: dict[str, Any]) -> None:
    eng = draw_engine(cal, np.random.default_rng(1))
    rng = np.random.default_rng(9)
    drifting = next(
        c
        for c in CHANNELS
        if abs(cal["engines"]["channels"][c]["end_shift"])
        > 6 * cal["engines"]["channels"][c]["within_engine_std"]
    )
    start = np.mean([cycle_values(cal, eng, 1, rng)[drifting] for _ in range(30)])
    end = np.mean([cycle_values(cal, eng, eng.life_cycles, rng)[drifting] for _ in range(30)])
    shift = cal["engines"]["channels"][drifting]["end_shift"]
    assert (end - start) * shift > 0 and abs(end - start) == pytest.approx(abs(shift), rel=0.2)


@needs_data
def test_degradation_simulator_ages_restarts_and_is_seed_deterministic(
    cal: dict[str, Any],
) -> None:
    asset = uuid.uuid4()
    a, b = DegradationSimulator(cal, seed=4, n_channels=2), DegradationSimulator(cal, seed=4)
    ticks_a = [a.tick(asset, with_waveforms=False) for _ in range(5)]
    ticks_b = [b.tick(asset, with_waveforms=False) for _ in range(5)]
    assert [t.age for t in ticks_a] == [1, 2, 3, 4, 5]
    assert [t.engine_values for t in ticks_a] == [t.engine_values for t in ticks_b]
    assert ticks_a[0].health == 0.0 and ticks_a[0].waveforms == {}
    first = a.tick(asset)
    assert set(first.waveforms) == {0, 1} and first.waveforms[0].shape == (N_SAMPLES,)

    sim = DegradationSimulator(cal, seed=1)
    ages, healths = [], []
    for _ in range(700):  # longer than any C-MAPSS FD001 life -> must restart at least once
        t = sim.tick(asset, with_waveforms=False)
        ages.append(t.age)
        healths.append(t.health)
    assert max(healths) == 1.0 and ages.count(1) >= 2
    with pytest.raises(ValueError):
        DegradationSimulator(cal, n_channels=0)


def test_generator_degradation_is_opt_in_and_emits_complete_snapshots() -> None:
    args = parse_args(["--base-url", "x", "--tenant-id", str(uuid.uuid4()), "--email", "e",
                       "--password", "p"])  # fmt: skip
    assert args.degradation is False
    now = datetime(2026, 1, 1, tzinfo=UTC)
    readings = degradation_readings(uuid.uuid4(), {c: 1.0 for c in CHANNELS}, now)
    assert len(readings) == 24
    assert {r["sensor_type"] for r in readings} == set(CMAPSS_TELEMETRY_TYPES)
    assert {r["timestamp"] for r in readings} == {now.isoformat()}  # one complete snapshot


# ---- non-circularity (CLAUDE.md rule 8) -----------------------------------------------------

SIM_MODULES = {
    "sim_calibration", "vibration_sim", "engine_sim", "degradation_sim", "iot_data_generator",
    "ai4i_replay",
}  # fmt: skip


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return {part for name in names for part in name.split(".")}


def test_no_training_code_imports_the_simulator() -> None:
    trainers = [
        p
        for p in (BACKEND / "app" / "ml").glob("*.py")
        if p.stem
        not in {"sim_calibration", "vibration_sim", "engine_sim", "degradation_sim", "ai4i_replay"}
    ] + list((BACKEND / "scripts").glob("train_*.py"))
    assert trainers, "expected to find training modules to scan"
    offenders = {p.name: _imports(p) & SIM_MODULES for p in trainers if _imports(p) & SIM_MODULES}
    assert offenders == {}, f"training code must never import simulator code: {offenders}"


def test_simulator_modules_do_not_import_training_or_inference_models() -> None:
    forbidden = {"train", "rul_train", "vibration_train", "xgboost", "sklearn"}
    for stem in ("vibration_sim", "engine_sim", "degradation_sim"):
        assert _imports(BACKEND / "app" / "ml" / f"{stem}.py") & forbidden == set(), stem


# ---- waveform store -------------------------------------------------------------------------


class FakeMinio:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def bucket_exists(self, bucket: str) -> bool:
        return True

    def put_object(self, bucket: str, key: str, data: io.BytesIO, length: int) -> None:
        self.objects[key] = data.read()

    def list_objects(self, bucket: str, prefix: str = "") -> list[Any]:
        return [
            type("O", (), {"object_name": k})()
            for k in sorted(self.objects)
            if k.startswith(prefix)
        ]

    def get_object(self, bucket: str, key: str) -> Any:
        data = self.objects[key]
        return type("R", (), {"read": lambda s: data, "close": lambda s: None,
                              "release_conn": lambda s: None})()  # fmt: skip


@pytest.fixture
def fake_minio(monkeypatch: pytest.MonkeyPatch) -> FakeMinio:
    client = FakeMinio()
    monkeypatch.setattr(ws, "get_minio_client", lambda: client)
    return client


def test_waveform_roundtrip_and_validation() -> None:
    wave = np.random.default_rng(0).normal(size=N_SAMPLES)
    np.testing.assert_allclose(ws.decode_waveform(ws.encode_waveform(wave)), wave, rtol=1e-5)
    with pytest.raises(ValueError):
        ws.encode_waveform(wave[:10])
    with pytest.raises(ValueError):
        ws.encode_waveform(np.where(np.arange(N_SAMPLES) == 3, np.nan, wave))


def test_waveform_decode_refuses_pickled_objects_and_wrong_shapes() -> None:
    evil = io.BytesIO()
    np.save(evil, np.array([object()], dtype=object), allow_pickle=True)
    with pytest.raises(ValueError):
        ws.decode_waveform(evil.getvalue())
    short = io.BytesIO()
    np.save(short, np.zeros(5), allow_pickle=False)
    with pytest.raises(ValueError):
        ws.decode_waveform(short.getvalue())


def test_waveform_store_is_tenant_scoped_and_splits_baseline_from_recent(
    fake_minio: FakeMinio,
) -> None:
    tenant_a, tenant_b, asset = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for i in range(8):
        stamp = datetime(2026, 1, 1, 0, i, tzinfo=UTC)
        ws.put_snapshot(tenant_a, asset, 0, stamp, np.full(N_SAMPLES, float(i)))
    ws.put_snapshot(tenant_a, asset, 1, datetime(2026, 1, 1, tzinfo=UTC), np.zeros(N_SAMPLES))

    assert ws.list_channels(tenant_a, asset) == [0, 1]
    assert ws.list_channels(tenant_b, asset) == []  # same asset id, other tenant: nothing
    base, recent = ws.load_channel_window(tenant_a, asset, 0, n_baseline=3, n_recent=2)
    assert [float(b[0]) for b in base] == [0.0, 1.0, 2.0]
    assert [float(r[0]) for r in recent] == [6.0, 7.0]  # latest two after the baseline
    other_base, other_recent = ws.load_channel_window(tenant_b, asset, 0, n_baseline=3, n_recent=2)
    assert other_base == [] and other_recent == []


# ---- service + agent wiring -----------------------------------------------------------------


def test_collect_vibration_assessments_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: Any) -> Any:
        raise ConnectionError("object store down")

    monkeypatch.setattr(vis, "_assess_asset_from_store", boom)
    out = asyncio.run(vis.collect_vibration_assessments(uuid.uuid4(), [uuid.uuid4()]))
    assert out == []


def _alarm(asset_id: uuid.UUID) -> dict[str, Any]:
    return VibrationAssessment(
        asset_id=asset_id, channel=0, alarm=True, latest_score=0.9, max_recent_score=0.9,
        threshold=0.5, consecutive_exceedances=5, k_consecutive=5, n_baseline_snapshots=20,
        n_scored_snapshots=12, model_version="v", dataset_name="d", dataset_version="1",
        computed_at=datetime(2026, 1, 1, tzinfo=UTC),
    ).model_dump(mode="json")  # fmt: skip


def _run_agent(context: dict[str, Any]) -> list[dict[str, Any]]:
    out = asyncio.run(mi.real_maintenance_inventory_callable(context))
    return list(out["scheduled_items"])


def test_agent_pulls_vibration_from_store_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = uuid.uuid4()
    calls: list[int] = []

    async def fake_collect(
        tenant_id: uuid.UUID, asset_ids: list[uuid.UUID]
    ) -> list[dict[str, Any]]:
        calls.append(1)
        return [_alarm(asset)]

    monkeypatch.setattr(mi, "collect_vibration_assessments", fake_collect)
    ctx = {
        "tenant_id": str(uuid.uuid4()),
        "facility_id": str(uuid.uuid4()),
        "risk_scores": [{"asset_id": str(asset), "risk_score": 10.0}],
    }
    monkeypatch.setattr(settings, "vibration_enabled", False)
    assert _run_agent(ctx)[0]["priority"] == "low" and calls == []
    monkeypatch.setattr(settings, "vibration_enabled", True)
    assert _run_agent(ctx)[0]["priority"] == "high" and calls == [1]
    # an explicitly supplied (even empty) list wins over the store
    assert _run_agent({**ctx, "vibration_assessments": []})[0]["priority"] == "low"
