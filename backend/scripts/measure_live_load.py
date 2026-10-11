"""Phase L task L.0: baseline measurements for the live data loop (docs/LIVE_DATA_PLAN.md).

Measures, against a SCRATCH tenant (never the demo tenant) on the local demo stack:

  1. ingest   - POST /telemetry, 220-row batches (44 machines x 5 AI4I sensors): rows/s, p50/p95
  2. scoring  - score_facility() for 44 assets with the scratch tenant holding 30k / 300k / 1M (opt-in 3M) rows
  3. api      - read-route latency idle vs. while ingest runs
  4. compress - can TimescaleDB compress a hypertable with (forced) RLS, and is RLS still enforced
                afterwards? Runs in a scratch DATABASE; the demo's sensor_readings is never altered.

Load rows are timing fillers only (existing stateless generator, AI4I-range values). They are never
read by training code (CLAUDE.md rule 8). Everything the script creates is deleted at the end.

Usage (demo env loaded as in docs/DEMO.md, API running on :8000):

    python -m scripts.measure_live_load                 # everything
    python -m scripts.measure_live_load --max-rows 300000 --skip compress
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import httpx

from app.core.config import settings
from app.core.db import async_session_factory, timescale_session_factory
from app.core.tenant_context import scope_session_to_tenant
from app.models.facility import Facility
from app.models.tenant import Tenant
from app.models.user import Role
from app.services.asset_service import create_asset
from app.services.auth_service import create_user_as_admin
from app.services.risk_inference_service import score_facility
from scripts.demo_factory import MACHINES
from scripts.iot_data_generator import generate_reading
from scripts.seed_demo import DEMO_TENANT_ID

logger = logging.getLogger("measure_live_load")

SCRATCH_TENANT_ID = uuid.UUID("0a5ca1e0-0000-4000-8000-000000000001")
SCRATCH_TENANT_NAME = "L0 measurement scratch"
SCRATCH_EMAIL = "l0-measure@scratch.example.com"
SCRATCH_PASSWORD = "l0-measure-password-123"
SCRATCH_DB = "l0_scratch_compression"
# The five coherent sensors the risk model's snapshot needs (feature_engineering_service).
SENSORS = ("air_temperature", "process_temperature", "rotational_speed", "torque", "tool_wear")
ROWS_PER_TICK = 44 * len(SENSORS)  # 220
TICK_SECONDS = 15
# 3M rows took Docker Desktop down on the dev machine (2026-10-11): opt in with --max-rows 3000000.
ALL_TIERS = (30_000, 300_000, 1_000_000, 3_000_000)
DEFAULT_MAX_ROWS = 1_000_000
OUT_JSON = Path(__file__).resolve().parents[1] / "data" / "processed" / "measure_live_load.json"


def pct(values: list[float], p: float) -> float:
    """Nearest-rank percentile (values need not be sorted)."""
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(p / 100 * len(ordered) + 0.5) - 1))]


def summarize(ms: list[float]) -> dict[str, float]:
    return {
        "n": len(ms),
        "p50_ms": round(statistics.median(ms), 1),
        "p95_ms": round(pct(ms, 95), 1),
        "p99_ms": round(pct(ms, 99), 1),
        "max_ms": round(max(ms), 1),
    }


def admin_dsn(db: str, *, timescale: bool) -> str:
    s = settings
    if timescale:
        return (
            f"postgresql://{s.timescale_user}:{s.timescale_password}"
            f"@{s.timescale_host}:{s.timescale_port}/{db}"
        )
    return f"postgresql://{s.postgres_user}:{s.postgres_password}@{s.postgres_host}:{s.postgres_port}/{db}"


# --------------------------------------------------------------------------- scratch tenant


async def create_scratch_tenant() -> tuple[uuid.UUID, list[uuid.UUID]]:
    """Scratch tenant, facility, the demo's 44 machines and an admin user -> (facility, assets)."""
    if SCRATCH_TENANT_ID == DEMO_TENANT_ID:
        raise SystemExit("refusing to run: scratch tenant id equals the demo tenant id")
    async with async_session_factory() as session:
        session.add(Tenant(id=SCRATCH_TENANT_ID, name=SCRATCH_TENANT_NAME, plan_tier="pro"))
        await session.commit()
    async with async_session_factory() as session:
        await create_user_as_admin(
            session,
            tenant_id=SCRATCH_TENANT_ID,
            email=SCRATCH_EMAIL,
            password=SCRATCH_PASSWORD,
            role=Role.TENANT_ADMIN,
        )
    facility_id = uuid.uuid4()
    async with async_session_factory() as session:
        await scope_session_to_tenant(session, SCRATCH_TENANT_ID)
        session.add(Facility(id=facility_id, tenant_id=SCRATCH_TENANT_ID, name="L0 scratch plant"))
        await session.commit()
    asset_ids: list[uuid.UUID] = []
    for m in MACHINES:
        async with async_session_factory() as session:
            await scope_session_to_tenant(session, SCRATCH_TENANT_ID)
            asset = await create_asset(
                session,
                tenant_id=SCRATCH_TENANT_ID,
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
            asset_ids.append(asset.id)
    return facility_id, asset_ids


async def delete_scratch_tenant() -> None:
    """Remove everything carrying the scratch tenant id (admin connections bypass RLS)."""
    ts = await asyncpg.connect(admin_dsn(settings.timescale_db, timescale=True))
    try:
        await ts.execute("DELETE FROM sensor_readings WHERE tenant_id = $1", SCRATCH_TENANT_ID)
    finally:
        await ts.close()
    conn = await asyncpg.connect(admin_dsn(settings.postgres_db, timescale=False))
    try:
        tables = [
            r["table_name"]
            for r in await conn.fetch(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema='public' AND column_name='tenant_id'"
            )
        ]
        pending = list(tables)
        for _ in range(len(pending) + 1):  # FK order is unknown: retry until nothing is left
            still: list[str] = []
            for t in pending:
                try:
                    await conn.execute(f'DELETE FROM "{t}" WHERE tenant_id = $1', SCRATCH_TENANT_ID)
                except asyncpg.ForeignKeyViolationError:
                    still.append(t)
            if not still:
                break
            pending = still
        await conn.execute("DELETE FROM tenants WHERE id = $1", SCRATCH_TENANT_ID)
    finally:
        await conn.close()


# --------------------------------------------------------------------------- load data


def tick_rows(
    asset_ids: list[uuid.UUID], when: datetime, rng: random.Random, n: int
) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for aid in asset_ids:
        for sensor in SENSORS:
            r = generate_reading(
                asset_id=aid,
                sensor_type=sensor,
                tick_s=n * TICK_SECONDS,
                anomaly=False,
                rng=rng,
                now=when,
            )
            rows.append((SCRATCH_TENANT_ID, aid, sensor, float(r["value"]), r["unit"], when))
    return rows


async def fill_to(
    conn: asyncpg.Connection, asset_ids: list[uuid.UUID], current: int, target: int, end: datetime
) -> int:
    """Bulk-COPY filler ticks (oldest first, ending just before `end`) until `target` rows exist."""
    rng = random.Random(7)
    total_ticks = target // ROWS_PER_TICK
    have_ticks = current // ROWS_PER_TICK
    batch: list[tuple[Any, ...]] = []
    for n in range(have_ticks, total_ticks):
        when = end - timedelta(seconds=TICK_SECONDS * (total_ticks - n + 4))
        batch.extend(tick_rows(asset_ids, when, rng, n))
        if len(batch) >= 100_000:
            await _copy(conn, batch)
            batch = []
    if batch:
        await _copy(conn, batch)
    return total_ticks * ROWS_PER_TICK


async def _copy(conn: asyncpg.Connection, rows: list[tuple[Any, ...]]) -> None:
    await conn.copy_records_to_table(
        "sensor_readings",
        records=rows,
        columns=["tenant_id", "asset_id", "sensor_type", "value", "unit", "timestamp"],
    )


# --------------------------------------------------------------------------- measurements


async def login(client: httpx.AsyncClient) -> dict[str, str]:
    r = await client.post(
        "/login",
        json={
            "tenant_id": str(SCRATCH_TENANT_ID),
            "email": SCRATCH_EMAIL,
            "password": SCRATCH_PASSWORD,
        },
    )
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def batch_payload(asset_ids: list[uuid.UUID], rng: random.Random, n: int) -> dict[str, Any]:
    now = datetime.now(UTC)
    readings = []
    for aid in asset_ids:
        for sensor in SENSORS:
            r = generate_reading(
                asset_id=aid,
                sensor_type=sensor,
                tick_s=n * TICK_SECONDS,
                anomaly=False,
                rng=rng,
                now=now,
            )
            readings.append(
                {
                    "asset_id": str(aid),
                    "sensor_type": sensor,
                    "value": r["value"],
                    "unit": r["unit"],
                    "timestamp": now.isoformat(),
                }
            )
    return {"readings": readings}


async def measure_ingest(
    client: httpx.AsyncClient, headers: dict[str, str], asset_ids: list[uuid.UUID], batches: int
) -> dict[str, Any]:
    rng = random.Random(3)
    paced: list[float] = []
    for n in range(batches):  # one batch at a time, like the simulator would send
        body = batch_payload(asset_ids, rng, n)
        t0 = time.perf_counter()
        r = await client.post("/telemetry", json=body, headers=headers)
        paced.append((time.perf_counter() - t0) * 1000)
        r.raise_for_status()
    flood: list[float] = []
    t_start = time.perf_counter()
    for n in range(batches):  # back to back = the ceiling
        body = batch_payload(asset_ids, rng, batches + n)
        t0 = time.perf_counter()
        (await client.post("/telemetry", json=body, headers=headers)).raise_for_status()
        flood.append((time.perf_counter() - t0) * 1000)
    elapsed = time.perf_counter() - t_start
    return {
        "batch_rows": ROWS_PER_TICK,
        "sequential": summarize(paced),
        "back_to_back": summarize(flood),
        "back_to_back_rows_per_s": round(batches * ROWS_PER_TICK / elapsed, 0),
        "required_rows_per_s_at_15s_tick": round(ROWS_PER_TICK / TICK_SECONDS, 1),
    }


async def time_routes(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    facility_id: uuid.UUID,
    asset_id: uuid.UUID,
    n: int,
) -> dict[str, Any]:
    routes = {
        "assets": f"/facilities/{facility_id}/assets",
        "risk_scores": f"/facilities/{facility_id}/risk-scores",
        "risk_history": f"/facilities/{facility_id}/risk-history?limit_per_asset=20",
        "telemetry": f"/facilities/{facility_id}/assets/{asset_id}/telemetry?limit=200",
    }
    out: dict[str, Any] = {}
    for name, url in routes.items():
        ms = []
        for _ in range(n):
            t0 = time.perf_counter()
            (await client.get(url, headers=headers)).raise_for_status()
            ms.append((time.perf_counter() - t0) * 1000)
        out[name] = summarize(ms)
    return out


async def time_scoring(facility_id: uuid.UUID, runs: int) -> dict[str, Any]:
    secs = []
    saved = 0
    for _ in range(runs):
        async with async_session_factory() as main, timescale_session_factory() as ts:
            await scope_session_to_tenant(main, SCRATCH_TENANT_ID)
            await scope_session_to_tenant(ts, SCRATCH_TENANT_ID)
            t0 = time.perf_counter()
            saved = len(
                await score_facility(main, ts, tenant_id=SCRATCH_TENANT_ID, facility_id=facility_id)
            )
            secs.append(time.perf_counter() - t0)
    return {
        "first_run_s": round(secs[0], 2),  # includes model load from MinIO
        "later_runs_s": [round(s, 2) for s in secs[1:]],
        "assets_scored": saved,
    }


def save(results: dict[str, Any]) -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")


async def measure_live_path(
    results: dict[str, Any], api: str, tiers: tuple[int, ...], ingest_batches: int, score_runs: int
) -> None:
    """Fills results["live_path"] in place and saves after every tier (a crash keeps the rest)."""
    facility_id, asset_ids = await create_scratch_tenant()
    live: dict[str, Any] = {"tiers": []}
    results["live_path"] = live
    conn = await asyncpg.connect(admin_dsn(settings.timescale_db, timescale=True))
    try:
        async with httpx.AsyncClient(base_url=api, timeout=60) as client:
            headers = await login(client)
            end = datetime.now(UTC)
            have = 0
            for i, tier in enumerate(tiers):
                t0 = time.perf_counter()
                have = await fill_to(conn, asset_ids, have, tier, end)
                await conn.execute("ANALYZE sensor_readings")
                logger.info("loaded %d rows in %.0fs", have, time.perf_counter() - t0)
                entry: dict[str, Any] = {"rows_stored": have}
                entry["scoring"] = await time_scoring(facility_id, score_runs)
                entry["routes_idle"] = await time_routes(
                    client, headers, facility_id, asset_ids[0], 30
                )
                if (
                    i == 0
                ):  # ingest at the smallest and the largest tier is enough to show the trend
                    entry["ingest"] = await measure_ingest(
                        client, headers, asset_ids, ingest_batches
                    )
                live["tiers"].append(entry)
                save(results)
            # API latency while ingest runs (concurrent loader + readers) at the largest tier
            stop = asyncio.Event()

            async def background_ingest() -> None:
                rng = random.Random(5)
                n = 0
                while not stop.is_set():
                    await client.post(
                        "/telemetry", json=batch_payload(asset_ids, rng, n), headers=headers
                    )
                    n += 1
                    await asyncio.sleep(0.0)

            task = asyncio.create_task(background_ingest())
            try:
                live["routes_under_ingest"] = await time_routes(
                    client, headers, facility_id, asset_ids[0], 30
                )
            finally:
                stop.set()
                await task
            live["ingest_at_largest_tier"] = await measure_ingest(
                client, headers, asset_ids, ingest_batches
            )
    finally:
        await conn.close()


# --------------------------------------------------------------------------- compression vs RLS


def _table_ddl(name: str) -> str:
    return f"""
CREATE TABLE {name} (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL, asset_id uuid NOT NULL, sensor_type varchar(100) NOT NULL,
    value double precision NOT NULL, unit varchar(50) NOT NULL,
    timestamp timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_{name} PRIMARY KEY (id, timestamp)
);
CREATE INDEX ix_{name}_tenant ON {name} (tenant_id);
CREATE INDEX ix_{name}_asset_time ON {name} (asset_id, timestamp);
SELECT create_hypertable('{name}', 'timestamp', chunk_time_interval => INTERVAL '1 day');
"""


def _rls_ddl(name: str) -> str:
    guc = "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
    return f"""
ALTER TABLE {name} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {name} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON {name}
  USING (tenant_id = {guc}) WITH CHECK (tenant_id = {guc});
GRANT SELECT, INSERT ON {name} TO {SCRATCH_ROLE};
"""


def _compress_ddl(name: str) -> str:
    return (
        f"ALTER TABLE {name} SET "
        "(timescaledb.compress, timescaledb.compress_segmentby = 'asset_id')"
    )


_COMPRESS_OLD = (
    "SELECT count(compress_chunk(c, if_not_compressed => true)) "
    "FROM show_chunks('{t}', older_than => now() - interval '2 days') c"
)
APP_PASS = "scratch-pass"
SCRATCH_ROLE = "l0_scratch_app"  # roles are cluster-wide: never touch the real app_role


async def _try(conn: asyncpg.Connection, sql: str) -> str:
    try:
        await conn.execute(sql)
        return "ok"
    except Exception as exc:  # noqa: BLE001 - we want the server's exact message
        return f"{type(exc).__name__}: {exc}"


async def _visible_rows(table: str, tenant: uuid.UUID | None) -> int:
    """Rows of `table` seen by the scratch role with the tenant GUC set (None = GUC unset)."""
    s = settings
    conn = await asyncpg.connect(
        user=SCRATCH_ROLE,
        password=APP_PASS,
        host=s.timescale_host,
        port=s.timescale_port,
        database=SCRATCH_DB,
    )
    try:
        async with conn.transaction():
            if tenant is not None:
                await conn.execute(f"SET LOCAL app.current_tenant_id = '{tenant}'")
            return int(await conn.fetchval(f"SELECT count(*) FROM {table}"))
    finally:
        await conn.close()


async def _seed(conn: asyncpg.Connection, table: str, tenants: list[uuid.UUID]) -> None:
    base = datetime.now(UTC) - timedelta(days=10)
    rows = [
        (t, uuid.uuid4(), "torque", 40.0 + i % 7, "Nm", base + timedelta(minutes=i))
        for t in tenants
        for i in range(3000)
    ]
    await conn.copy_records_to_table(
        table,
        records=rows,
        columns=["tenant_id", "asset_id", "sensor_type", "value", "unit", "timestamp"],
    )


async def _compressed_chunks(conn: asyncpg.Connection, table: str) -> int:
    return int(
        await conn.fetchval(
            "SELECT count(*) FROM timescaledb_information.chunks "
            "WHERE hypertable_name=$1 AND is_compressed",
            table,
        )
    )


async def measure_compression() -> dict[str, Any]:
    """Three tables in a scratch database: no RLS (control), RLS first, compression first."""
    admin = await asyncpg.connect(admin_dsn("postgres", timescale=True))
    await admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH_DB} WITH (FORCE)")
    await admin.execute(f"CREATE DATABASE {SCRATCH_DB}")
    await admin.close()
    conn = await asyncpg.connect(admin_dsn(SCRATCH_DB, timescale=True))
    out: dict[str, Any] = {}
    t_a, t_b = uuid.uuid4(), uuid.uuid4()
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")
        out["timescaledb_version"] = await conn.fetchval(
            "SELECT extversion FROM pg_extension WHERE extname='timescaledb'"
        )
        await conn.execute(f"DROP ROLE IF EXISTS {SCRATCH_ROLE}")
        await conn.execute(f"CREATE ROLE {SCRATCH_ROLE} LOGIN NOBYPASSRLS PASSWORD '{APP_PASS}'")
        for name in ("sr_control", "sr_rls_first", "sr_compress_first"):
            await conn.execute(_table_ddl(name))
            await _seed(conn, name, [t_a, t_b])

        # Control: compression with no RLS must work on this server, or the rest means nothing.
        control: dict[str, Any] = {"enable": await _try(conn, _compress_ddl("sr_control"))}
        control["compress_chunks"] = await _try(conn, _COMPRESS_OLD.format(t="sr_control"))
        control["compressed_chunks"] = await _compressed_chunks(conn, "sr_control")
        out["control_no_rls"] = control

        # Order 1 = what production has: RLS already on, then try to turn compression on.
        await conn.execute(_rls_ddl("sr_rls_first"))
        first: dict[str, Any] = {
            "enable_compression": await _try(conn, _compress_ddl("sr_rls_first"))
        }
        if first["enable_compression"] == "ok":
            first["compress_chunks"] = await _try(conn, _COMPRESS_OLD.format(t="sr_rls_first"))
            first["compressed_chunks"] = await _compressed_chunks(conn, "sr_rls_first")
        first["isolation"] = await _isolation("sr_rls_first", t_a, t_b)
        out["order_rls_then_compression"] = first

        # Order 2 = compress first, then try to turn RLS on.
        second: dict[str, Any] = {
            "enable_compression": await _try(conn, _compress_ddl("sr_compress_first"))
        }
        second["compress_chunks"] = await _try(conn, _COMPRESS_OLD.format(t="sr_compress_first"))
        second["compressed_chunks"] = await _compressed_chunks(conn, "sr_compress_first")
        second["enable_rls"] = await _try(conn, _rls_ddl("sr_compress_first"))
        second["isolation"] = (
            await _isolation("sr_compress_first", t_a, t_b)
            if second["enable_rls"] == "ok"
            else None
        )
        out["order_compression_then_rls"] = second
    finally:
        await conn.close()
        admin = await asyncpg.connect(admin_dsn("postgres", timescale=True))
        await admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH_DB} WITH (FORCE)")
        await admin.execute(f"DROP ROLE IF EXISTS {SCRATCH_ROLE}")
        await admin.close()
    return out


async def _isolation(table: str, t_a: uuid.UUID, t_b: uuid.UUID) -> dict[str, Any]:
    seen = (
        await _visible_rows(table, t_a),
        await _visible_rows(table, t_b),
        await _visible_rows(table, None),
    )
    return {
        "tenant_a_sees": seen[0],
        "tenant_b_sees": seen[1],
        "no_tenant_sees": seen[2],
        "expected_each": 3000,
        "isolated": seen == (3000, 3000, 0),
    }


# --------------------------------------------------------------------------- main


async def main_async(args: argparse.Namespace) -> None:
    results: dict[str, Any] = {"measured_at": datetime.now(UTC).isoformat()}
    skip = set(args.skip)
    if "compress" not in skip:
        logger.info("compression vs RLS (scratch database) ...")
        results["compression_vs_rls"] = await measure_compression()
    if "live" not in skip:
        tiers = tuple(t for t in ALL_TIERS if t <= args.max_rows) or (args.max_rows,)
        logger.info("live path, tiers=%s (scratch tenant %s)", tiers, SCRATCH_TENANT_ID)
        try:
            await measure_live_path(results, args.api, tiers, args.ingest_batches, args.score_runs)
        finally:
            logger.info("cleaning up scratch tenant")
            await delete_scratch_tenant()
    save(results)
    print(json.dumps(results, indent=2, default=str))
    print(f"\nwritten to {OUT_JSON}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument(
        "--max-rows", type=int, default=DEFAULT_MAX_ROWS, help="largest row tier to load"
    )
    p.add_argument("--ingest-batches", type=int, default=40)
    p.add_argument("--score-runs", type=int, default=3)
    p.add_argument("--skip", nargs="*", default=[], choices=["compress", "live"])
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(main_async(p.parse_args(argv)))


if __name__ == "__main__":
    main()
