"""One-command bootstrap of the LOCAL demo (see docs/DEMO.md).

Run after `docker compose -p twin-demo -f infra/docker/docker-compose.demo.yml up -d` and
with infra/docker/demo.env.example loaded into the shell:

    python -m scripts.demo_setup

Idempotent: migrations, model publishing and the demo tenant/user/facility are ensured on
every run; the asset/telemetry/waveform backfill only runs while the facility has no assets
(wipe everything with `docker compose ... down -v` for a clean slate). Steps:

  1. migrate the main and TimescaleDB databases
  2. publish the risk, RUL and vibration models to MinIO (trained from the real datasets)
  3. seed the demo tenant, admin user and facility
  4. create assets with a dependency chain, and a placeholder (TILED) floor-plan row
  5. backfill telemetry: AI4I-calibrated scalars for the plant assets, plus two engines from
     the calibrated degradation simulator (one run to failure, one new) with waveforms
  6. score the facility (risk + RUL + vibration verdict), then run the 5-agent pipeline once

Everything simulated here is calibrated from the real IMS / C-MAPSS / AI4I data
(docs/DATASETS.md); none of it is training data.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select

from app.core.config import settings
from app.core.db import async_session_factory, timescale_session_factory
from app.core.tenant_context import scope_session_to_tenant
from app.ml.degradation_sim import DegradationSimulator
from app.ml.engine_sim import channel_telemetry
from app.ml.sim_calibration import calibrate, load_calibration, save_calibration
from app.models.asset import Asset
from app.models.facility_map_upload import FacilityMapUpload, UploadStatus
from app.models.risk_score import RiskScore
from app.schemas.requests.telemetry import SensorReadingIn
from app.services import waveform_store
from app.services.agent_run_service import execute_and_record_pipeline
from app.services.asset_dependency_service import create_dependency
from app.services.asset_service import create_asset
from app.services.risk_inference_service import score_facility
from app.services.telemetry_service import ingest_readings
from scripts.calibrate_simulator import DEFAULT_OUT
from scripts.iot_data_generator import AI4I_SENSOR_PROFILES, generate_reading
from scripts.seed_demo import DEMO_EMAIL, DEMO_PASSWORD, seed

logger = logging.getLogger("demo_setup")
BACKEND = Path(__file__).resolve().parents[1]
SIM_SEED = 11  # the seed verified in tests/integration/test_degradation_e2e.py
HISTORY_TICKS_PLANT = 90
NEW_ENGINE_TICKS = 40
BATCH = 1000

# name, type, x, y  (pixel space of the placeholder raster; see docs/DEMO.md)
PLANT_ASSETS: list[tuple[str, str, float, float]] = [
    ("Primary Feed Pump 7", "pump", 900.0, 700.0),
    ("Heat Exchanger Alpha", "heat_exchanger", 2200.0, 700.0),
    ("Turbine Gen 1", "turbine", 3500.0, 700.0),
    ("Cooling Fan 2", "fan", 2200.0, 2000.0),
]
ENGINES: list[tuple[str, str, float, float]] = [
    ("Compressor Engine A (aged)", "engine", 900.0, 3000.0),
    ("Compressor Engine B (new)", "engine", 2800.0, 3000.0),
]
# parent -> child (a failure at the parent cascades to the child)
DEPENDENCIES: list[tuple[str, str]] = [
    ("Primary Feed Pump 7", "Heat Exchanger Alpha"),
    ("Heat Exchanger Alpha", "Turbine Gen 1"),
    ("Primary Feed Pump 7", "Cooling Fan 2"),
]


def _run(cmd: list[str], label: str) -> None:
    logger.info("%s ...", label)
    result = subprocess.run(cmd, cwd=BACKEND, capture_output=True, text=True, env=os.environ.copy())
    if result.returncode != 0:
        raise SystemExit(f"{label} failed:\n{result.stdout[-1500:]}\n{result.stderr[-1500:]}")


def migrate() -> None:
    for ini in ("alembic.ini", "alembic_timescale.ini"):
        _run([sys.executable, "-m", "alembic", "-c", ini, "upgrade", "head"], f"migrate {ini}")


def ensure_models() -> None:
    """Publish any model missing from MinIO, trained from the real datasets."""
    from app.ml.rul_train import load_rul_model
    from app.ml.train import load_model
    from app.ml.vibration_train import load_vibration_model
    from scripts import train_risk_model, train_rul_model, train_vibration_model

    def train_risk() -> None:
        train_risk_model.run(train_risk_model.parse_args(["--seed", "0"]))

    def train_rul() -> None:
        train_rul_model.run(train_rul_model.parse_args(["--subset", settings.rul_model_subset]))

    def train_vibration() -> None:
        train_vibration_model.run(train_vibration_model.parse_args([]))

    checks: list[tuple[str, Any, Any]] = [
        ("risk model", lambda: load_model(None), train_risk),
        ("RUL model", lambda: load_rul_model(settings.rul_model_subset, None), train_rul),
        ("vibration model", lambda: load_vibration_model(None), train_vibration),
    ]
    for label, probe, train in checks:
        try:
            probe()
            logger.info("%s already published", label)
        except Exception:  # noqa: BLE001 - any failure to read means: train and publish
            logger.info("training %s from the real dataset ...", label)
            train()


def ensure_calibration() -> dict[str, Any]:
    if not DEFAULT_OUT.is_file():
        save_calibration(
            calibrate(
                BACKEND / "data" / "processed" / "ims",
                BACKEND / "data" / "raw" / "cmapss",
                settings.rul_model_subset,
            ),
            DEFAULT_OUT,
        )
    return load_calibration(DEFAULT_OUT)


async def create_assets_and_map(
    tenant_id: uuid.UUID, facility_id: uuid.UUID
) -> dict[str, uuid.UUID]:
    ids: dict[str, uuid.UUID] = {}
    for name, kind, x, y in [*PLANT_ASSETS, *ENGINES]:
        async with async_session_factory() as session:
            await scope_session_to_tenant(session, tenant_id)  # re-scope: SET LOCAL per txn
            asset = await create_asset(
                session, tenant_id=tenant_id, facility_id=facility_id, name=name, type=kind,
                x=x, y=y, status_="operational", installed_date=None, manufacturer=None, model=None,
            )  # fmt: skip
            ids[name] = asset.id
    for parent, child in DEPENDENCIES:
        async with async_session_factory() as session:
            await scope_session_to_tenant(session, tenant_id)
            await create_dependency(
                session, tenant_id=tenant_id, facility_id=facility_id,
                parent_asset_id=ids[parent], child_asset_id=ids[child],
            )  # fmt: skip
    async with async_session_factory() as session:
        await scope_session_to_tenant(session, tenant_id)
        session.add(
            FacilityMapUpload(
                tenant_id=tenant_id,
                facility_id=facility_id,
                original_filename="demo-placeholder.pdf",
                storage_key=f"demo/{facility_id}/placeholder",
                format="pdf",
                status=UploadStatus.TILED,
                status_detail="placeholder map for the demo; upload a real floor plan instead",
                tile_prefix=f"demo/{facility_id}",
            )
        )
        await session.commit()
    return ids


async def _ingest(tenant_id: uuid.UUID, readings: list[SensorReadingIn]) -> None:
    for i in range(0, len(readings), BATCH):
        async with timescale_session_factory() as session:
            await scope_session_to_tenant(session, tenant_id)
            await ingest_readings(session, tenant_id=tenant_id, readings=readings[i : i + BATCH])


async def backfill_plant_telemetry(
    tenant_id: uuid.UUID, ids: dict[str, uuid.UUID], end: datetime
) -> None:
    rng = random.Random(SIM_SEED)
    readings: list[SensorReadingIn] = []
    for name, *_ in PLANT_ASSETS:
        # The feed pump is the troubled one: more anomalous readings than the AI4I base rate.
        rate = 0.25 if name == "Primary Feed Pump 7" else 0.0339
        for i in range(HISTORY_TICKS_PLANT):
            stamp = end - timedelta(hours=HISTORY_TICKS_PLANT - i)
            for sensor in AI4I_SENSOR_PROFILES:
                reading = generate_reading(
                    asset_id=ids[name], sensor_type=sensor, tick_s=i * 3600.0,
                    anomaly=rng.random() < rate, rng=rng, now=stamp,
                )  # fmt: skip
                readings.append(SensorReadingIn.model_validate(reading))
    await _ingest(tenant_id, readings)


def _simulate_engine(
    sim: DegradationSimulator, asset_id: uuid.UUID, until_failure: bool
) -> tuple[list[dict[str, float]], list[dict[int, np.ndarray]]]:
    """Tick the simulator: per-cycle engine channel values and per-cycle waveforms.

    Timestamps are assigned by the caller afterwards so the series ends at "now", and the
    engine telemetry and waveform streams share the same stamps.
    """
    values: list[dict[str, float]] = []
    waves: list[dict[int, np.ndarray]] = []
    for _ in range(10_000 if until_failure else NEW_ENGINE_TICKS):
        tick = sim.tick(asset_id)
        values.append(tick.engine_values)
        waves.append(tick.waveforms)
        if until_failure and tick.health >= 1.0:
            break
    return values, waves


async def backfill_engines(
    tenant_id: uuid.UUID, ids: dict[str, uuid.UUID], cal: dict[str, Any], end: datetime
) -> None:
    sim = DegradationSimulator(cal, seed=SIM_SEED, n_channels=2)
    for name, until_failure in (
        ("Compressor Engine A (aged)", True),
        ("Compressor Engine B (new)", False),
    ):
        asset_id = ids[name]
        cycles, waves = await asyncio.to_thread(_simulate_engine, sim, asset_id, until_failure)
        n = len(cycles)
        readings: list[SensorReadingIn] = []
        for i, values in enumerate(cycles):
            stamp = end - timedelta(hours=n - i)
            for column, value in values.items():
                kind, unit = channel_telemetry(column)
                readings.append(
                    SensorReadingIn(asset_id=asset_id, sensor_type=kind, value=value, unit=unit,
                                    timestamp=stamp)
                )  # fmt: skip
            for channel, wave in waves[i].items():
                await asyncio.to_thread(
                    waveform_store.put_snapshot, tenant_id, asset_id, channel, stamp, wave
                )
        await _ingest(tenant_id, readings)
        logger.info("%s: %d cycles of telemetry + waveforms", name, n)


async def score_and_run_pipeline(
    tenant_id: uuid.UUID, facility_id: uuid.UUID, run_pipeline: bool
) -> list[RiskScore]:
    settings.vibration_enabled = True
    async with async_session_factory() as main, timescale_session_factory() as ts:
        await scope_session_to_tenant(main, tenant_id)
        await scope_session_to_tenant(ts, tenant_id)
        scores = await score_facility(main, ts, tenant_id=tenant_id, facility_id=facility_id)
    if run_pipeline:
        async with async_session_factory() as session:
            await scope_session_to_tenant(session, tenant_id)
            _, run = await execute_and_record_pipeline(
                session, tenant_id=tenant_id, facility_id=facility_id, trigger="user_query",
                user_query="What if Primary Feed Pump 7 fails?",
            )  # fmt: skip
            logger.info("agent run %s: status=%s", run.id, run.status)
    return scores


async def get_upload_id(tenant_id: uuid.UUID, facility_id: uuid.UUID) -> uuid.UUID | None:
    async with async_session_factory() as session:
        await scope_session_to_tenant(session, tenant_id)
        result = await session.execute(
            select(FacilityMapUpload.id)
            .where(FacilityMapUpload.facility_id == facility_id)
            .order_by(FacilityMapUpload.created_at.desc())
        )
        return result.scalars().first()


async def facility_has_assets(tenant_id: uuid.UUID, facility_id: uuid.UUID) -> bool:
    async with async_session_factory() as session:
        await scope_session_to_tenant(session, tenant_id)
        result = await session.execute(select(Asset.id).where(Asset.facility_id == facility_id))
        return result.first() is not None


async def summarize(
    tenant_id: uuid.UUID, facility_id: uuid.UUID, names: dict[uuid.UUID, str]
) -> None:
    async with async_session_factory() as session:
        await scope_session_to_tenant(session, tenant_id)
        result = await session.execute(
            select(RiskScore)
            .where(RiskScore.facility_id == facility_id)
            .order_by(RiskScore.computed_at.desc())
        )
        latest: dict[uuid.UUID, RiskScore] = {}
        for row in result.scalars():
            latest.setdefault(row.asset_id, row)
    print("\nAsset health (latest scoring)")
    print(f"{'asset':30} {'risk':>6} {'RUL (cycles)':>13} {'vibration alarm':>16}")
    for asset_id, row in sorted(latest.items(), key=lambda kv: -kv[1].score):
        alarm = row.factors_json.get("vibration_alarm")
        rul = f"{row.rul_cycles:.1f}" if row.rul_cycles is not None else "-"
        alarm_s = "-" if alarm is None else ("YES" if alarm else "no")
        print(f"{names.get(asset_id, str(asset_id)):30} {row.score:6.1f} {rul:>13} {alarm_s:>16}")


async def main_async(args: argparse.Namespace) -> None:
    if not args.skip_migrate:
        migrate()
    if not args.skip_models:
        ensure_models()
    cal = ensure_calibration()
    tenant_id, _, facility_id = await seed()
    end = datetime.now(UTC)
    if await facility_has_assets(tenant_id, facility_id):
        logger.info("facility already has assets - skipping asset/telemetry backfill")
        async with async_session_factory() as session:
            await scope_session_to_tenant(session, tenant_id)
            rows = (
                await session.execute(select(Asset).where(Asset.facility_id == facility_id))
            ).scalars()
            ids = {a.name: a.id for a in rows}
    else:
        ids = await create_assets_and_map(tenant_id, facility_id)
        await backfill_plant_telemetry(tenant_id, ids, end)
        await backfill_engines(tenant_id, ids, cal, end)
    await score_and_run_pipeline(tenant_id, facility_id, not args.no_pipeline)
    await summarize(tenant_id, facility_id, {v: k for k, v in ids.items()})

    print("\n--- demo ready ---")
    print(f"login:       tenant_id={tenant_id}")
    print(f"             email={DEMO_EMAIL}  password={DEMO_PASSWORD}")
    print(f"facility_id: {facility_id}")
    upload_id = await get_upload_id(tenant_id, facility_id)
    print(
        f"map page:    http://localhost:5173/login?next=/facilities/{facility_id}/map/{upload_id}"
    )
    print("API docs:    http://localhost:8000/docs")
    print("Next: see docs/DEMO.md for the demo script.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--skip-migrate", action="store_true")
    parser.add_argument("--skip-models", action="store_true", help="assume models are published")
    parser.add_argument(
        "--no-pipeline", action="store_true", help="do not run the 5-agent pipeline"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    np.seterr(all="ignore")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
