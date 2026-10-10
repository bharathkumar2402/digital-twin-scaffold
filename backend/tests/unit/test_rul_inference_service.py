"""Tests for RUL inference over telemetry: pivoting, windows, fail-closed paths, parity."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest
from pydantic import ValidationError
from xgboost import XGBRegressor

from app.ml.cmapss_profile import COLUMNS, parse_cmapss_table, parse_rul_vector
from app.ml.rul_dataset import build_test_dataset, build_train_dataset
from app.ml.rul_features import RulFeatureConfig, fit_config
from app.schemas.ml.rul_result import RulEstimate
from app.services import rul_inference_service as svc
from app.services.ml.dataset_mapping import CMAPSS_TELEMETRY_TYPES, map_cmapss_rows_to_readings
from app.services.rul_inference_service import (
    SKIP_INCOMPLETE,
    SKIP_INVALID,
    SKIP_MODEL_UNAVAILABLE,
    SKIP_NO_TELEMETRY,
    SKIP_UNSEEN_REGIME,
    LoadedRulModel,
    RulModelUnavailable,
    assemble_snapshots,
    build_window,
    load_rul_model_cached,
    predict_asset_rul,
    predict_facility_rul,
    predict_from_window,
)
from scripts.acquire_cmapss import DEFAULT_OUTPUT_DIR as CMAPSS_DIR

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
START = datetime(2026, 1, 1, tzinfo=UTC)
TENANT = uuid.uuid4()
OTHER_TENANT = uuid.uuid4()


class StubModel:
    """Stands in for XGBRegressor: returns a fixed value so we test the plumbing."""

    def __init__(self, value: float, n_features: int) -> None:
        self.value = value
        self.n_features_in_ = n_features

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.full(X.shape[0], self.value)


def _engine_table(n: int, seed: int = 0, s1: float = 0.0, s3: float = 100.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.zeros((n, len(COLUMNS)))
    t[:, 0] = 1
    t[:, 1] = np.arange(1, n + 1)
    t[:, 2], t[:, 4] = s1, s3
    t[:, 5] = 518.67
    t[:, 6] = 640 + np.arange(n) * 0.4 + rng.normal(0, 0.1, n)
    for c in range(7, 26):
        t[:, c] = rng.normal(10 * c, 1, n)
    return t


@pytest.fixture(scope="module")
def config() -> RulFeatureConfig:
    return fit_config(np.vstack([_engine_table(80, 1), _engine_table(100, 2)]))


def _loaded(config: RulFeatureConfig, value: float = 50.0) -> LoadedRulModel:
    stub = StubModel(value, len(config.feature_names))
    return LoadedRulModel(
        model=cast(Any, stub), config=config, version="20261010T000000Z-abc", subset="FD001"
    )


def _telemetry(
    table: np.ndarray, asset_id: uuid.UUID = uuid.uuid4()
) -> list[tuple[datetime, str, float]]:
    readings = map_cmapss_rows_to_readings("FD001", table, START, timedelta(hours=1))
    return [(r.timestamp, r.sensor_type, r.value) for r in readings]


def _db() -> Any:
    return FakeTimescale()


class FakeTimescale:
    """Emulates the two RUL queries in Python, including tenant/asset filtering."""

    def __init__(self) -> None:
        self.rows: dict[tuple[uuid.UUID, uuid.UUID], list[tuple[datetime, str, float]]] = {}
        self.calls: list[dict[str, Any]] = []

    def add(
        self, tenant: uuid.UUID, asset: uuid.UUID, rows: list[tuple[datetime, str, float]]
    ) -> None:
        self.rows.setdefault((tenant, asset), []).extend(rows)

    async def execute(self, stmt: Any, params: dict[str, Any]) -> Any:
        self.calls.append(params)
        rows = [
            r
            for r in self.rows.get((params["tenant_id"], params["asset_id"]), [])
            if r[1] in params["types"]
        ]
        if "count(*)" in str(stmt):
            by_time: dict[datetime, set[str]] = {}
            for stamp, kind, _ in rows:
                by_time.setdefault(stamp, set()).add(kind)
            n = sum(1 for kinds in by_time.values() if len(kinds) == params["n_channels"])
            return SimpleNamespace(scalar_one=lambda: n)
        rows.sort(key=lambda r: r[0], reverse=True)
        limited = rows[: params["row_limit"]]
        return SimpleNamespace(
            all=lambda: [
                SimpleNamespace(timestamp=a, sensor_type=b, value=c) for a, b, c in limited
            ]
        )


# ---- pivoting and windows -------------------------------------------------------------------


def test_assemble_keeps_only_complete_snapshots_oldest_first() -> None:
    rows = _telemetry(_engine_table(6))
    stamps = sorted({r[0] for r in rows})
    rows = [r for r in rows if not (r[0] == stamps[2] and r[1] == "cmapss_s9")]  # break cycle 3
    snaps = assemble_snapshots(rows, max_cycles=10)
    assert [s["timestamp"] for s in snaps] == [s for i, s in enumerate(stamps) if i != 2]
    assert len(assemble_snapshots(rows, max_cycles=2)) == 2
    assert assemble_snapshots(rows, max_cycles=2)[-1]["timestamp"] == stamps[-1]
    assert assemble_snapshots([], 5) == []


def test_assemble_duplicate_readings_keep_last() -> None:
    rows = _telemetry(_engine_table(2))
    first = rows[0]
    rows.append((first[0], first[1], 12345.0))
    snaps = assemble_snapshots(rows, 5)
    assert snaps[0][first[1]] == 12345.0


def test_window_cycle_numbers_are_the_assets_age_not_the_window_index() -> None:
    snaps = assemble_snapshots(_telemetry(_engine_table(40)), 30)
    window = build_window(snaps, total_cycles=500)
    assert [s.cycle for s in window.snapshots][0] == 471
    assert [s.cycle for s in window.snapshots][-1] == 500
    with pytest.raises(ValueError, match="smaller than the window"):
        build_window(snaps, total_cycles=10)
    with pytest.raises(ValueError, match="no snapshots"):
        build_window([], 5)


# ---- prediction -------------------------------------------------------------------------------


def test_prediction_is_clipped_validated_and_versioned(config: RulFeatureConfig) -> None:
    window = build_window(assemble_snapshots(_telemetry(_engine_table(40)), 30), 40)
    asset = uuid.uuid4()
    est = predict_from_window(window, _loaded(config, 73.456), asset_id=asset, now=NOW)
    assert est.rul_cycles == 73.46
    assert (est.model_version, est.model_subset, est.cycle, est.window_len) == (
        "20261010T000000Z-abc",
        "FD001",
        40,
        30,
    )
    assert (
        predict_from_window(window, _loaded(config, -20.0), asset_id=asset, now=NOW).rul_cycles
        == 0.0
    )
    assert (
        predict_from_window(window, _loaded(config, 9999.0), asset_id=asset, now=NOW).rul_cycles
        == config.rul_cap
    )
    with pytest.raises(ValueError, match="non-finite"):
        predict_from_window(window, _loaded(config, float("nan")), asset_id=asset, now=NOW)


def test_estimate_schema_rejects_bad_values() -> None:
    base: dict[str, Any] = dict(
        asset_id=uuid.uuid4(),
        model_version="v",
        model_subset="FD001",
        cycle=1,
        window_len=1,
        computed_at=NOW,
    )
    RulEstimate(rul_cycles=10.0, **base)
    for bad in (-1.0, 10_000.0, float("inf")):
        with pytest.raises(ValidationError):
            RulEstimate(rul_cycles=bad, **base)
    with pytest.raises(ValidationError):
        RulEstimate(rul_cycles=5.0, surprise="x", **base)  # type: ignore[call-arg]


# ---- per-asset fail-closed behaviour ------------------------------------------------------------


@pytest.mark.asyncio
async def test_asset_without_telemetry_gets_no_rul(config: RulFeatureConfig) -> None:
    out = await predict_asset_rul(
        _db(),
        _loaded(config),
        tenant_id=TENANT,
        asset_id=uuid.uuid4(),
        now=NOW,
    )
    assert out.estimate is None and out.skip_reason == SKIP_NO_TELEMETRY


@pytest.mark.asyncio
async def test_incomplete_snapshots_give_no_rul(config: RulFeatureConfig) -> None:
    asset, db = uuid.uuid4(), _db()
    db.add(TENANT, asset, [r for r in _telemetry(_engine_table(5)) if r[1] != "cmapss_s4"])
    out = await predict_asset_rul(db, _loaded(config), tenant_id=TENANT, asset_id=asset, now=NOW)
    assert out.estimate is None and out.skip_reason == SKIP_INCOMPLETE


@pytest.mark.asyncio
async def test_unseen_operating_regime_gives_no_rul(config: RulFeatureConfig) -> None:
    asset, db = uuid.uuid4(), _db()
    db.add(TENANT, asset, _telemetry(_engine_table(35, s1=42.0, s3=60.0)))
    out = await predict_asset_rul(db, _loaded(config), tenant_id=TENANT, asset_id=asset, now=NOW)
    assert out.estimate is None and out.skip_reason == SKIP_UNSEEN_REGIME


@pytest.mark.asyncio
async def test_non_finite_telemetry_gives_no_rul(config: RulFeatureConfig) -> None:
    asset, db = uuid.uuid4(), _db()
    rows = _telemetry(_engine_table(35))
    rows[-5] = (rows[-5][0], rows[-5][1], float("nan"))  # newest cycle, inside the window
    db.add(TENANT, asset, rows)
    out = await predict_asset_rul(db, _loaded(config), tenant_id=TENANT, asset_id=asset, now=NOW)
    assert out.estimate is None and out.skip_reason == SKIP_INVALID


@pytest.mark.asyncio
async def test_success_uses_total_cycle_count_and_scopes_by_tenant(
    config: RulFeatureConfig,
) -> None:
    asset, db = uuid.uuid4(), _db()
    db.add(TENANT, asset, _telemetry(_engine_table(120)))
    db.add(OTHER_TENANT, asset, _telemetry(_engine_table(7)))  # another tenant, same asset id
    out = await predict_asset_rul(
        db, _loaded(config, 60.0), tenant_id=TENANT, asset_id=asset, now=NOW
    )
    assert out.estimate is not None and out.skip_reason is None
    assert (
        out.estimate.cycle == 120
    )  # all 120 complete snapshots counted, not just the 30 in the window
    assert out.estimate.window_len == 30
    assert all(call["tenant_id"] == TENANT for call in db.calls)  # every query is tenant-scoped
    assert all(set(call["types"]) == set(CMAPSS_TELEMETRY_TYPES) for call in db.calls)


@pytest.mark.asyncio
async def test_facility_degrades_when_model_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*args: Any, **kwargs: Any) -> Any:
        raise KeyError("no such key")

    monkeypatch.setattr(svc, "load_rul_model", missing)
    monkeypatch.setattr(svc, "_MODEL_CACHE", {})
    assets = [uuid.uuid4(), uuid.uuid4()]
    out = await predict_facility_rul(_db(), tenant_id=TENANT, asset_ids=assets, now=NOW)
    assert {a: o.skip_reason for a, o in out.items()} == dict.fromkeys(
        assets, SKIP_MODEL_UNAVAILABLE
    )
    with pytest.raises(RulModelUnavailable):
        load_rul_model_cached("FD001")


def test_model_cache_and_layout_mismatch(
    monkeypatch: pytest.MonkeyPatch, config: RulFeatureConfig
) -> None:
    calls = []

    def fake_load(subset: str, version: str | None) -> Any:
        calls.append((subset, version))
        n = len(config.feature_names)
        return StubModel(1.0, n), config, {}, {"model_version": "v-123"}

    monkeypatch.setattr(svc, "load_rul_model", fake_load)
    monkeypatch.setattr(svc, "_MODEL_CACHE", {})
    first = load_rul_model_cached("FD001")
    assert first.version == "v-123"
    assert load_rul_model_cached("FD001", "v-123") is first  # served from cache, no second load
    assert len(calls) == 1

    monkeypatch.setattr(svc, "_MODEL_CACHE", {})
    monkeypatch.setattr(
        svc,
        "load_rul_model",
        lambda s, v: (StubModel(1.0, 3), config, {}, {"model_version": "bad"}),
    )
    with pytest.raises(ValueError, match="disagree on the feature count"):
        load_rul_model_cached("FD001")


# ---- train/serve parity on real FD001 through the full service path ------------------------------

_have_data = pytest.mark.skipif(
    not (CMAPSS_DIR / "train_FD001.txt").is_file(),
    reason="C-MAPSS not downloaded; run `python -m scripts.acquire_cmapss`",
)


@_have_data
@pytest.mark.asyncio
async def test_service_prediction_equals_offline_test_set_prediction() -> None:
    """Live path (telemetry rows -> service) must equal the offline evaluation path."""
    train = parse_cmapss_table((CMAPSS_DIR / "train_FD001.txt").read_text())
    test = parse_cmapss_table((CMAPSS_DIR / "test_FD001.txt").read_text())
    rul = parse_rul_vector((CMAPSS_DIR / "RUL_FD001.txt").read_text())
    cfg = fit_config(train)
    tr = build_train_dataset(train, cfg)
    model = XGBRegressor(n_estimators=60, max_depth=4, random_state=0, n_jobs=1).fit(tr.X, tr.y)
    loaded = LoadedRulModel(model=model, config=cfg, version="parity", subset="FD001")
    offline = build_test_dataset(test, rul, cfg)
    expected = np.clip(model.predict(offline.X), 0.0, cfg.rul_cap)

    db = _db()
    assets = {}
    for engine in (1, 7, 42, 100):
        asset = uuid.uuid4()
        assets[engine] = asset
        db.add(TENANT, asset, _telemetry(test[test[:, 0] == engine]))
    for engine, asset in assets.items():
        out = await predict_asset_rul(db, loaded, tenant_id=TENANT, asset_id=asset, now=NOW)
        assert out.estimate is not None
        assert out.estimate.rul_cycles == pytest.approx(expected[engine - 1], abs=0.01)
        assert out.estimate.cycle == int(offline.cycles[engine - 1])
