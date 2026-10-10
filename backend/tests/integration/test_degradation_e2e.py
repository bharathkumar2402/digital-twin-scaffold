# ruff: noqa: F811 - pytest fixtures imported from the sibling module and re-requested by name
"""End-to-end Phase 3b pipeline on throwaway containers (no managed/cloud services touched).

Real MinIO + real Postgres + real TimescaleDB, the *real* trained RUL and vibration models
(trained from the real C-MAPSS / IMS data into that MinIO), and the calibrated degradation
simulator as the live feed:

  simulator -> TimescaleDB (24 C-MAPSS channels) + MinIO (accelerometer waveforms)
            -> score_facility (RUL + vibration verdict, persisted in risk_scores)
            -> Agent 3 (vibration alarm raises maintenance priority)

One asset is simulated through its whole life (so its final snapshots are near failure), one
only through its first cycles (healthy). Requires Docker and the real data under
backend/data; skipped when the data is absent.
"""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from testcontainers.minio import MinioContainer
from testcontainers.postgres import PostgresContainer

from tests.integration.test_risk_inference_service import (  # noqa: F401 - fixtures
    _dsn,
    _make_asset,
    _make_facility,
    _make_tenant,
    _scoped,
    fake_model,
    main_pg_container,
    main_session_factory,
    migrated_main_db,
    migrated_timescale_db,
    timescale_pg_container,
    timescale_session_factory,
)

BACKEND = Path(__file__).resolve().parents[2]
FEATURES_DIR = BACKEND / "data" / "processed" / "ims"
CMAPSS_DIR = BACKEND / "data" / "raw" / "cmapss"

pytestmark = pytest.mark.skipif(
    not (FEATURES_DIR / "3rd_test_vibration_features.csv.gz").exists()
    or not (CMAPSS_DIR / "train_FD001.txt").exists(),
    reason="real IMS / C-MAPSS data not present",
)

START = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def minio_container() -> Any:
    with MinioContainer(image="minio/minio:latest") as minio:
        yield minio


@pytest.fixture
def real_minio(minio_container: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import storage
    from app.core.config import settings
    from app.ml import rul_train
    from app.services import rul_inference_service, vibration_inference_service

    cfg = minio_container.get_config()
    monkeypatch.setattr(settings, "minio_endpoint", cfg["endpoint"])
    monkeypatch.setattr(settings, "minio_access_key", cfg["access_key"])
    monkeypatch.setattr(settings, "minio_secret_key", cfg["secret_key"])
    monkeypatch.setattr(settings, "minio_secure", False)
    monkeypatch.setattr(storage, "_client", None)
    # The imported autouse `fake_model` stubs the RUL loader out; this test needs the real one.
    monkeypatch.setattr(rul_inference_service, "load_rul_model", rul_train.load_rul_model)
    monkeypatch.setattr(rul_inference_service, "_MODEL_CACHE", {})
    monkeypatch.setattr(vibration_inference_service, "_MODEL_CACHE", {})


@pytest.fixture
def trained_models(real_minio: None) -> dict[str, str]:
    """Train and publish both models from the real datasets, as an operator would."""
    from scripts import train_rul_model, train_vibration_model

    rul = train_rul_model.run(train_rul_model.parse_args(["--subset", "FD001"]))
    vib = train_vibration_model.run(train_vibration_model.parse_args([]))
    assert rul is not None and vib is not None
    return {
        "rul_dataset": str(rul.metadata["dataset_name"]),
        "vib": str(vib.metadata["dataset_name"]),
    }


async def _insert_rows(
    timescale: PostgresContainer, tenant_id: uuid.UUID, asset_id: uuid.UUID, rows: list[tuple]
) -> None:
    conn = await asyncpg.connect(
        _dsn(timescale, user=timescale.username, password=timescale.password, driver="postgresql")
    )
    try:
        await conn.executemany(
            "INSERT INTO sensor_readings "
            "(tenant_id, asset_id, sensor_type, value, unit, timestamp) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            [(tenant_id, asset_id, k, v, u, ts) for ts, k, v, u in rows],
        )
    finally:
        await conn.close()


def _feed(
    sim: Any, asset_id: uuid.UUID, tenant_id: uuid.UUID, *, until_failure: bool, n_ticks: int
) -> list[tuple]:
    """Tick the simulator, writing waveforms to MinIO; return the telemetry rows."""
    from app.ml.engine_sim import channel_telemetry
    from app.services import waveform_store

    rows: list[tuple] = []
    for i in range(10_000 if until_failure else n_ticks):
        tick = sim.tick(asset_id)
        stamp = START + timedelta(hours=i)
        for column, value in tick.engine_values.items():
            kind, unit = channel_telemetry(column)
            rows.append((stamp, kind, value, unit))
        for channel, wave in tick.waveforms.items():
            waveform_store.put_snapshot(tenant_id, asset_id, channel, stamp, wave)
        if until_failure and tick.health >= 1.0:
            break
    return rows


async def test_simulated_degradation_flows_through_rul_vibration_and_agent3(
    monkeypatch: pytest.MonkeyPatch,
    trained_models: dict[str, str],
    migrated_main_db: PostgresContainer,
    migrated_timescale_db: PostgresContainer,
    main_session_factory: async_sessionmaker,
    timescale_session_factory: async_sessionmaker,
) -> None:
    import asyncio

    from app.agents import maintenance_inventory as mi
    from app.core.config import settings
    from app.ml.degradation_sim import DegradationSimulator
    from app.ml.sim_calibration import calibrate
    from app.services.risk_inference_service import score_facility

    monkeypatch.setattr(settings, "vibration_enabled", True)
    cal = calibrate(FEATURES_DIR, CMAPSS_DIR, "FD001")
    sim = DegradationSimulator(cal, seed=11, n_channels=2)

    tenant_id = await _make_tenant(migrated_main_db, "Tenant E2E")
    facility_id = await _make_facility(migrated_main_db, tenant_id=tenant_id, name="Plant E2E")
    failing = await _make_asset(
        migrated_main_db, tenant_id=tenant_id, facility_id=facility_id, name="Engine old"
    )
    healthy = await _make_asset(
        migrated_main_db, tenant_id=tenant_id, facility_id=facility_id, name="Engine new"
    )
    rows_failing = await asyncio.to_thread(
        _feed, sim, failing, tenant_id, until_failure=True, n_ticks=0
    )
    rows_healthy = await asyncio.to_thread(
        _feed, sim, healthy, tenant_id, until_failure=False, n_ticks=40
    )
    await _insert_rows(migrated_timescale_db, tenant_id, failing, rows_failing)
    await _insert_rows(migrated_timescale_db, tenant_id, healthy, rows_healthy)

    main_session = await _scoped(main_session_factory, tenant_id)
    timescale_session = await _scoped(timescale_session_factory, tenant_id)
    try:
        saved = await score_facility(
            main_session, timescale_session, tenant_id=tenant_id, facility_id=facility_id
        )
    finally:
        await main_session.close()
        await timescale_session.close()
    by_asset = {row.asset_id: row for row in saved}
    old, new = by_asset[failing], by_asset[healthy]

    # RUL: the worn engine is predicted much closer to failure than the new one, and every
    # stored result names its model and dataset.
    assert old.rul_cycles is not None and new.rul_cycles is not None
    assert old.rul_cycles < new.rul_cycles
    assert old.factors_json["rul_dataset_name"] == trained_models["rul_dataset"]
    assert old.factors_json["rul_model_version"]

    # Vibration: verdict persisted with model + dataset identity, no alarm while healthy.
    for row in (old, new):
        assert row.factors_json["vibration_model_version"]
        assert row.factors_json["vibration_dataset_name"] == trained_models["vib"]
    assert old.factors_json["vibration_alarm"] == 1  # sustained alarm near end of life
    assert new.factors_json["vibration_alarm"] == 0  # no alarm for the healthy engine
    assert old.factors_json["vibration_max_score"] > new.factors_json["vibration_max_score"]

    # Agent 3: pulls the same evidence from the store; an alarm lifts schedule priority.
    scored = [
        {"asset_id": str(a), "risk_score": 5.0, "rul_cycles": r.rul_cycles}
        for a, r in by_asset.items()
    ]
    out = await mi.real_maintenance_inventory_callable(
        {"tenant_id": str(tenant_id), "facility_id": str(facility_id), "risk_scores": scored}
    )
    priority = {i["asset_id"]: i["priority"] for i in out["scheduled_items"]}
    assert priority[str(healthy)] == "low"
    assert priority[str(failing)] in ("high", "critical")  # lifted from a 5.0 risk score
