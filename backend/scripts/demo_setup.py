"""One-command bootstrap of the LOCAL demo: the Meridian Components Plant (see docs/DEMO.md).

Run after `docker compose -p twin-demo -f infra/docker/docker-compose.demo.yml up -d` and
with infra/docker/demo.env.example loaded into the shell:

    python -m scripts.demo_setup

The plant (3 floors, 44 machines, 60 dependency links) is defined once in `demo_factory`; this
loads it. Idempotent: migrations, model publishing, the tenant/user/facility and the floor plans
are ensured on every run; machines, links and telemetry are created only while the facility has
none (wipe everything with `docker compose ... down -v` for a clean slate). Steps:

  1. migrate the main and TimescaleDB databases
  2. publish the risk, RUL and vibration models to MinIO (trained from the real datasets)
  3. seed the demo tenant, admin user and facility
  4. draw, tile and register one floor plan per floor
  5. create the machines (on their floors) and the links between them
  6. backfill telemetry: AI4I-calibrated history for every machine, a real held-out AI4I record as
     each machine's latest snapshot, engine-style telemetry for machines with a remaining life,
     and vibration waveforms for the rotating ones
  7. score the plant (risk + RUL + vibration verdict), then run the 5-agent pipeline once

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
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import insert, select

from app.core.config import settings
from app.core.db import async_session_factory, timescale_session_factory
from app.core.tenant_context import scope_session_to_tenant
from app.ml.ai4i_replay import heldout_rows, pick_row, replay_readings
from app.ml.degradation_sim import DegradationSimulator
from app.ml.engine_sim import channel_telemetry
from app.ml.sim_calibration import calibrate, load_calibration, save_calibration
from app.ml.vibration_sim import draw_channel_scale, simulate_waveform
from app.models.asset import Asset
from app.models.facility_map_upload import FacilityMapUpload, UploadStatus
from app.models.risk_score import RiskScore
from app.models.sensor_reading import SensorReading
from app.services import waveform_store
from app.services.agent_run_service import run_pipeline_for_facility
from app.services.asset_dependency_service import create_dependency
from app.services.asset_service import create_asset
from app.services.risk_inference_service import score_facility
from scripts.calibrate_simulator import DEFAULT_OUT
from scripts.demo_factory import FLOORS, LINKS, MACHINES, Machine, machines_on, tile_prefix
from scripts.demo_floorplan import build_and_upload
from scripts.iot_data_generator import AI4I_SENSOR_PROFILES, generate_reading
from scripts.seed_demo import DEMO_EMAIL, DEMO_FLOOR_UPLOAD_IDS, DEMO_PASSWORD, seed

logger = logging.getLogger("demo_setup")
BACKEND = Path(__file__).resolve().parents[1]
SIM_SEED = 11
HISTORY_TICKS = 60
MIN_ENGINE_TICKS = 40  # the RUL model needs a full feature window
VIBRATION_SNAPSHOTS = 35  # 20 healthy baseline + the recent window the detector scores
VIBRATION_BASELINE = 20
AI4I_CSV = BACKEND / "data" / "raw" / "ai4i2020.csv"
INSERT_CHUNK = 5000


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


# ---- floor plans, machines, links ------------------------------------------------------------


async def ensure_floor_plans(tenant_id: uuid.UUID, facility_id: uuid.UUID) -> None:
    """Draw and tile every floor into MinIO, and make sure each has a finished map upload row.

    Idempotent with fixed upload ids so map URLs are stable. These are drawings generated for the
    demo, tiled directly (no sandbox worker); a real upload still goes through the sandbox.
    """
    counts = await asyncio.to_thread(build_and_upload)
    logger.info("floor plans: uploaded tiles %s", counts)
    async with async_session_factory() as session:
        await scope_session_to_tenant(session, tenant_id)
        for floor in FLOORS:
            upload_id = DEMO_FLOOR_UPLOAD_IDS[floor.level]
            upload = (
                await session.execute(
                    select(FacilityMapUpload).where(FacilityMapUpload.id == upload_id)
                )
            ).scalar_one_or_none()
            if upload is None:
                session.add(
                    FacilityMapUpload(
                        id=upload_id,
                        tenant_id=tenant_id,
                        facility_id=facility_id,
                        original_filename=f"meridian-level-{floor.level}.png",
                        storage_key=f"demo/{facility_id}/level-{floor.level}",
                        format="png",
                        status=UploadStatus.TILED,
                        status_detail="generated demo floor plan (scripts/demo_floorplan.py)",
                        tile_prefix=tile_prefix(floor.level),
                        floor_level=floor.level,
                        floor_name=floor.name,
                    )
                )
            else:
                upload.status = UploadStatus.TILED
                upload.tile_prefix = tile_prefix(floor.level)
                upload.floor_level = floor.level
                upload.floor_name = floor.name
        await session.commit()


async def create_machines_and_links(
    tenant_id: uuid.UUID, facility_id: uuid.UUID
) -> dict[str, uuid.UUID]:
    ids: dict[str, uuid.UUID] = {}
    for m in MACHINES:
        async with async_session_factory() as session:
            await scope_session_to_tenant(session, tenant_id)  # re-scope: SET LOCAL per txn
            asset = await create_asset(
                session,
                tenant_id=tenant_id,
                facility_id=facility_id,
                name=m.name,
                type=m.type,
                floor_level=m.floor,
                x=float(m.x),
                y=float(m.y),
                status_="operational",
                installed_date=None,
                manufacturer=None,
                model=None,
            )
            ids[m.key] = asset.id
    for dependent, upstream, _kind in LINKS:
        async with async_session_factory() as session:
            await scope_session_to_tenant(session, tenant_id)
            await create_dependency(
                session,
                tenant_id=tenant_id,
                facility_id=facility_id,
                parent_asset_id=ids[dependent],
                child_asset_id=ids[upstream],
            )
    logger.info("created %d machines and %d links", len(ids), len(LINKS))
    return ids


async def place_machines_on_plan(
    tenant_id: uuid.UUID, facility_id: uuid.UUID
) -> dict[str, uuid.UUID]:
    """A re-run: put already-created machines back on their floor and position."""
    by_name = {m.name: m for m in MACHINES}
    ids: dict[str, uuid.UUID] = {}
    async with async_session_factory() as session:
        await scope_session_to_tenant(session, tenant_id)
        assets = (
            await session.execute(select(Asset).where(Asset.facility_id == facility_id))
        ).scalars()
        for asset in assets:
            spec = by_name.get(asset.name)
            if spec is not None:
                asset.floor_level, asset.x, asset.y = spec.floor, float(spec.x), float(spec.y)
                ids[spec.key] = asset.id
        await session.commit()
    return ids


# ---- telemetry -------------------------------------------------------------------------------


async def _bulk_insert(tenant_id: uuid.UUID, rows: list[dict[str, Any]]) -> None:
    """Straight to the hypertable (no live anomaly check: this is history, not a live feed)."""
    for i in range(0, len(rows), INSERT_CHUNK):
        async with timescale_session_factory() as session:
            await scope_session_to_tenant(session, tenant_id)
            await session.execute(insert(SensorReading), rows[i : i + INSERT_CHUNK])
            await session.commit()


def _row(
    tenant_id: uuid.UUID, asset_id: uuid.UUID, kind: str, value: float, unit: str, stamp: datetime
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "asset_id": asset_id,
        "sensor_type": kind,
        "value": float(value),
        "unit": unit,
        "timestamp": stamp,
    }


async def backfill_history(tenant_id: uuid.UUID, ids: dict[str, uuid.UUID], end: datetime) -> None:
    """AI4I-calibrated sensor history for every machine (what the charts show)."""
    rng = random.Random(SIM_SEED)
    rows: list[dict[str, Any]] = []
    for m in MACHINES:
        troubled = m.snapshot[0] or m.vibration == "failing"
        rate = 0.25 if troubled else 0.0339
        for i in range(HISTORY_TICKS):
            stamp = end - timedelta(hours=HISTORY_TICKS - i)
            for sensor in AI4I_SENSOR_PROFILES:
                r = generate_reading(
                    asset_id=ids[m.key],
                    sensor_type=sensor,
                    tick_s=i * 3600.0,
                    anomaly=rng.random() < rate,
                    rng=rng,
                    now=stamp,
                )
                rows.append(_row(tenant_id, ids[m.key], sensor, r["value"], r["unit"], stamp))
    await _bulk_insert(tenant_id, rows)
    logger.info("history: %d sensor readings", len(rows))


async def backfill_snapshots(
    tenant_id: uuid.UUID, ids: dict[str, uuid.UUID], end: datetime
) -> None:
    """Each machine's latest snapshot is a REAL held-out AI4I record (what risk is scored on)."""
    held_out = heldout_rows(AI4I_CSV)
    stamp = end - timedelta(minutes=1)
    rows: list[dict[str, Any]] = []
    for m in MACHINES:
        failure, nth, mode = m.snapshot
        record = pick_row(held_out, failure=failure, nth=nth, mode=mode)
        rows.extend(
            _row(tenant_id, ids[m.key], r.sensor_type, r.value, r.unit, stamp)
            for r in replay_readings(record, ids[m.key], stamp)
        )
    await _bulk_insert(tenant_id, rows)


async def backfill_engine_telemetry(
    tenant_id: uuid.UUID, ids: dict[str, uuid.UUID], cal: dict[str, Any], end: datetime
) -> None:
    """24 engine-style channels for machines with a remaining life, aged to their target stage."""
    sim = DegradationSimulator(cal, seed=SIM_SEED, n_channels=1)
    rows: list[dict[str, Any]] = []
    for m in (m for m in MACHINES if m.rul_health is not None):
        assert m.rul_health is not None
        ticks = []
        while True:
            tick = sim.tick(ids[m.key], with_waveforms=False)
            ticks.append(tick)
            if tick.health >= m.rul_health and tick.age >= MIN_ENGINE_TICKS:
                break
        n = len(ticks)
        for i, tick in enumerate(ticks):
            stamp = end - timedelta(hours=n - i)
            for column, value in tick.engine_values.items():
                kind, unit = channel_telemetry(column)
                rows.append(_row(tenant_id, ids[m.key], kind, value, unit, stamp))
        logger.info("%s: %d cycles of engine telemetry (stage %.2f)", m.key, n, m.rul_health)
    await _bulk_insert(tenant_id, rows)


def _vibration_health(i: int, failing: bool) -> float:
    """Healthy throughout, or healthy for the 20-snapshot baseline and then a late ramp."""
    if not failing or i < VIBRATION_BASELINE:
        return 0.0
    span = max(1, VIBRATION_SNAPSHOTS - VIBRATION_BASELINE - 1)
    return 0.55 + 0.45 * (i - VIBRATION_BASELINE) / span


def _write_waveforms(
    tenant_id: uuid.UUID, ids: dict[str, uuid.UUID], cal: dict[str, Any], end: datetime
) -> int:
    rng = np.random.default_rng(SIM_SEED)
    written = 0
    for m in (m for m in MACHINES if m.vibration is not None):
        for channel in range(m.vib_channels):
            scale = draw_channel_scale(cal, rng)
            for i in range(VIBRATION_SNAPSHOTS):
                stamp = end - timedelta(hours=VIBRATION_SNAPSHOTS - i)
                health = _vibration_health(i, m.vibration == "failing")
                wave = simulate_waveform(cal, health, rng, channel_scale=scale)
                waveform_store.put_snapshot(tenant_id, ids[m.key], channel, stamp, wave)
                written += 1
    return written


async def backfill_waveforms(
    tenant_id: uuid.UUID, ids: dict[str, uuid.UUID], cal: dict[str, Any], end: datetime
) -> None:
    written = await asyncio.to_thread(_write_waveforms, tenant_id, ids, cal, end)
    logger.info("vibration: %d waveform snapshots", written)


# ---- scoring, pipeline, summary --------------------------------------------------------------


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
            _, run = await run_pipeline_for_facility(
                session,
                tenant_id=tenant_id,
                facility_id=facility_id,
                trigger="user_query",
                user_query="What if Air Compressor AC-1 fails?",
            )
            logger.info("agent run %s: status=%s", run.id, run.status)
    return scores


async def facility_has_assets(tenant_id: uuid.UUID, facility_id: uuid.UUID) -> bool:
    async with async_session_factory() as session:
        await scope_session_to_tenant(session, tenant_id)
        result = await session.execute(select(Asset.id).where(Asset.facility_id == facility_id))
        return result.first() is not None


def _fmt(machines: Iterable[Machine]) -> str:
    return ", ".join(m.key for m in machines)


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
    ordered = sorted(latest.items(), key=lambda kv: -kv[1].score)
    print(f"\nScored {len(latest)} of {len(MACHINES)} machines. Highest risk and notable:")
    print(f"{'machine':34} {'risk':>6} {'RUL (cycles)':>13} {'vibration':>10}")
    shown = 0
    for asset_id, row in ordered:
        alarm = row.factors_json.get("vibration_alarm")
        if row.score < 50 and not alarm and row.rul_cycles is None:
            continue
        rul = f"{row.rul_cycles:.1f}" if row.rul_cycles is not None else "-"
        vib = "-" if alarm is None else ("ALARM" if alarm else "ok")
        print(f"{names.get(asset_id, str(asset_id)):34} {row.score:6.1f} {rul:>13} {vib:>10}")
        shown += 1
    print(f"({len(latest) - shown} other machines healthy: low risk, no alarm)")


async def main_async(args: argparse.Namespace) -> None:
    if not args.skip_migrate:
        migrate()
    if not args.skip_models:
        ensure_models()
    cal = ensure_calibration()
    tenant_id, _, facility_id = await seed()
    end = datetime.now(UTC)
    await ensure_floor_plans(tenant_id, facility_id)
    if await facility_has_assets(tenant_id, facility_id):
        logger.info("facility already has machines - skipping machine/telemetry backfill")
        ids = await place_machines_on_plan(tenant_id, facility_id)
    else:
        ids = await create_machines_and_links(tenant_id, facility_id)
        await backfill_history(tenant_id, ids, end)
        await backfill_snapshots(tenant_id, ids, end)
        await backfill_engine_telemetry(tenant_id, ids, cal, end)
        await backfill_waveforms(tenant_id, ids, cal, end)
    await score_and_run_pipeline(tenant_id, facility_id, not args.no_pipeline)
    names = {
        asset_id: next(m.name for m in MACHINES if m.key == key) for key, asset_id in ids.items()
    }
    await summarize(tenant_id, facility_id, names)

    print("\n--- demo ready ---")
    print('open:        http://localhost:5173   (click "Use demo account")')
    print(f"login:       tenant_id={tenant_id}")
    print(f"             email={DEMO_EMAIL}  password={DEMO_PASSWORD}")
    print(f"facility_id: {facility_id}")
    for floor in FLOORS:
        print(f"  L{floor.level} {floor.name:18} {_fmt(machines_on(floor.level))}")
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
