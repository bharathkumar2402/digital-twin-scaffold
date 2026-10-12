"""Live telemetry simulator for the demo (Phase L task L.2; docs/LIVE_DATA_PLAN.md).

Runs as a host process next to the API. Signs in as the seeded technician account, finds the
facility's machines through the API (no direct database access), and every tick posts the five
coherent AI4I sensors of every machine in ONE `POST /telemetry` batch (44 x 5 = 220 rows).
Timestamps are real; only the degradation storyline is time-compressed.

    python -m scripts.live_simulator                         # run, storyline once
    python -m scripts.live_simulator --storyline off --seed 7
    python -m scripts.live_simulator inject AC-2 overstrain   # while it runs (next tick)
    python -m scripts.live_simulator inject PB-1 sensor_dropout
    python -m scripts.live_simulator clear PB-1

The machines start from the same real held-out AI4I records as the demo (`app.ml.ai4i_replay`),
so scores continue from the current demo instead of jumping. If the API is down or the token
expires it backs off and signs in again; it never exits because of a transient failure and it
never reads or writes the databases. It does not import any training code (CLAUDE.md rule 8).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.ml.ai4i_replay import heldout_rows, pick_row, replay_readings
from app.ml.live_machine_sim import (
    DEFAULT_WEAR_MIN_PER_HOUR,
    FAULTS,
    SENSORS,
    STORYLINES,
    UNITS,
    Ai4iStats,
    FleetSim,
    Storyline,
    calibrate_from_csv,
)
from scripts.demo_factory import MACHINES

logger = logging.getLogger("live_simulator")
BACKEND = Path(__file__).resolve().parents[1]
AI4I_CSV = BACKEND / "data" / "raw" / "ai4i2020.csv"
CONTROL_DIR = BACKEND / "data" / "processed" / "sim_control"
# Copies of the constants in scripts/seed_demo.py (importing it would pull in the database models,
# and this process must never touch the databases). A unit test keeps them identical.
DEMO_TENANT_ID = uuid.UUID("d3500000-0000-4000-8000-000000000001")
DEMO_SIMULATOR_EMAIL = "simulator@twinlabs.com"
DEMO_EXTRA_PASSWORD = "demo-password-123"
MAX_BATCH_ROWS = 1000  # POST /telemetry rejects more
MAX_BACKOFF_S = 60.0
# 4xx answers that mean "try again later"; every other 4xx is a configuration error to stop on.
RETRYABLE_4XX = {401, 408, 425, 429}


class SimulatorError(Exception):
    """A failure retrying cannot fix (wrong credentials, forbidden role, rejected payload)."""


def start_states() -> dict[str, dict[str, float]]:
    """Each machine's starting sensors: the real held-out AI4I record the demo gives it."""
    held_out = heldout_rows(AI4I_CSV)
    stamp = datetime(2026, 1, 1, tzinfo=UTC)
    states: dict[str, dict[str, float]] = {}
    for m in MACHINES:
        failure, nth, mode = m.snapshot
        record = pick_row(held_out, failure=failure, nth=nth, mode=mode)
        readings = replay_readings(record, uuid.uuid4(), stamp)
        by_type = {r.sensor_type: r.value for r in readings}
        states[m.key] = {s: float(by_type[s]) for s in SENSORS}
    return states


def build_batch(
    values: dict[str, dict[str, float]], asset_ids: dict[str, uuid.UUID], when: datetime
) -> list[dict[str, Any]]:
    """One reading per sensor per machine, all machines and sensors sharing the timestamp."""
    stamp = when.isoformat()
    rows: list[dict[str, Any]] = []
    for key, sensors in values.items():
        if key not in asset_ids:
            continue
        for sensor in SENSORS:
            rows.append(
                {
                    "asset_id": str(asset_ids[key]),
                    "sensor_type": sensor,
                    "value": sensors[sensor],
                    "unit": UNITS[sensor],
                    "timestamp": stamp,
                }
            )
    if len(rows) > MAX_BATCH_ROWS:
        raise SimulatorError(f"batch of {len(rows)} rows exceeds the API limit {MAX_BATCH_ROWS}")
    return rows


class Poster:
    """Authenticated API access that signs in again when the token is rejected."""

    def __init__(self, client: httpx.Client, tenant_id: uuid.UUID, email: str, password: str):
        self.client = client
        self._creds = {"tenant_id": str(tenant_id), "email": email, "password": password}
        self._token: str | None = None
        self.logins = 0

    def login(self) -> None:
        resp = self.client.post("/login", json=self._creds)
        if resp.status_code in (400, 401, 403, 422):
            raise SimulatorError(f"login rejected ({resp.status_code}): check the demo account")
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        self.logins += 1

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in (0, 1):
            if self._token is None:
                self.login()
            headers = {"Authorization": f"Bearer {self._token}"}
            resp = self.client.request(method, url, headers=headers, **kwargs)
            if resp.status_code == 401 and attempt == 0:
                self._token = None  # expired or revoked: sign in again, once
                continue
            return resp
        raise AssertionError("unreachable")


def discover(poster: Poster, facility_id: str | None) -> tuple[str, dict[str, uuid.UUID]]:
    resp = poster.request("GET", "/facilities")
    resp.raise_for_status()
    facilities = resp.json()
    if facility_id is not None:
        facilities = [f for f in facilities if f["id"] == facility_id]
    if not facilities:
        raise SimulatorError("no facility found for the simulator account")
    fid: str = facilities[0]["id"]
    resp = poster.request("GET", f"/facilities/{fid}/assets")
    resp.raise_for_status()
    by_name = {a["name"]: uuid.UUID(a["id"]) for a in resp.json()}
    ids = {m.key: by_name[m.name] for m in MACHINES if m.name in by_name}
    if not ids:
        raise SimulatorError("none of the demo machines exist in this facility (run demo_setup)")
    missing = [m.key for m in MACHINES if m.key not in ids]
    if missing:
        logger.warning("machines not found in the facility, skipped: %s", ", ".join(missing))
    return fid, ids


def apply_commands(control_dir: Path, sim: FleetSim) -> None:
    """Run and delete queued `inject` / `clear` command files (oldest first)."""
    if not control_dir.is_dir():
        return
    for path in sorted(control_dir.glob("*.json")):
        try:
            cmd = json.loads(path.read_text(encoding="utf-8"))
            if cmd["action"] == "inject":
                sim.inject(
                    cmd["machine"], cmd["fault"], duration_s=float(cmd.get("duration_s", 120))
                )
            elif cmd["action"] == "clear":
                sim.clear(cmd["machine"])
            else:
                raise ValueError(f"unknown action {cmd['action']!r}")
            logger.info("command applied: %s", cmd)
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("ignored bad command file %s: %s", path.name, exc)
        finally:
            path.unlink(missing_ok=True)


def run(
    poster: Poster,
    sim: FleetSim,
    asset_ids: dict[str, uuid.UUID],
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] = lambda: False,
    max_ticks: int | None = None,
    control_dir: Path | None = None,
) -> dict[str, int]:
    """The tick loop. The simulated machines advance every tick, even when a post fails (a real
    sensor does not wait for the network), and a failed batch is dropped, not replayed."""
    stats = {"ticks": 0, "sent": 0, "failed": 0, "rows": 0}
    failures = 0
    while not should_stop() and (max_ticks is None or stats["ticks"] < max_ticks):
        if control_dir is not None:
            apply_commands(control_dir, sim)
        values = sim.step()
        stats["ticks"] += 1
        delay = sim.tick_seconds
        batch = build_batch(values, asset_ids, now())
        if batch:
            try:
                resp = poster.request("POST", "/telemetry", json={"readings": batch})
                if resp.status_code >= 500 or resp.status_code in RETRYABLE_4XX:
                    raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
                if resp.status_code >= 400:
                    raise SimulatorError(
                        f"API rejected the batch: {resp.status_code} {resp.text[:200]}"
                    )
                stats["sent"] += 1
                stats["rows"] += len(batch)
                failures = 0
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                failures += 1
                stats["failed"] += 1
                delay = min(sim.tick_seconds * 2 ** (failures - 1), MAX_BACKOFF_S)
                logger.warning("post failed (%s); retry in %.0fs", exc.__class__.__name__, delay)
        if max_ticks is not None and stats["ticks"] >= max_ticks:
            break
        sleep(delay)
    return stats


def _interruptible_sleep(stop: threading.Event) -> Callable[[float], None]:
    def _sleep(seconds: float) -> None:
        stop.wait(seconds)

    return _sleep


def queue_command(control_dir: Path, command: dict[str, Any]) -> Path:
    control_dir.mkdir(parents=True, exist_ok=True)
    path = control_dir / f"{time.time_ns()}.json"
    path.write_text(json.dumps(command), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--api", default=os.environ.get("SIM_API", "http://127.0.0.1:8000"))
    parser.add_argument("--tenant-id", default=str(DEMO_TENANT_ID))
    parser.add_argument("--email", default=DEMO_SIMULATOR_EMAIL)
    parser.add_argument("--password", default=DEMO_EXTRA_PASSWORD)
    parser.add_argument("--facility-id", default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--tick-seconds", type=float, default=float(os.environ.get("SIM_TICK_SECONDS", 15))
    )
    parser.add_argument("--storyline", choices=STORYLINES, default="once")
    parser.add_argument("--story-machine", default="AC-2")
    parser.add_argument(
        "--story-delay-min", type=float, default=float(os.environ.get("STORY_DELAY", 3))
    )
    parser.add_argument(
        "--story-minutes", type=float, default=float(os.environ.get("STORY_MINUTES", 15))
    )
    parser.add_argument("--wear-min-per-hour", type=float, default=DEFAULT_WEAR_MIN_PER_HOUR)
    parser.add_argument("--max-ticks", type=int, default=None)
    parser.add_argument("--control-dir", type=Path, default=CONTROL_DIR)
    sub = parser.add_subparsers(dest="cmd")
    inj = sub.add_parser("inject", help="queue a fault for a running simulator")
    inj.add_argument("machine")
    inj.add_argument("fault", choices=FAULTS)
    inj.add_argument("--duration-s", type=float, default=120.0)
    clr = sub.add_parser("clear", help="clear a machine's injected fault / dropout")
    clr.add_argument("machine")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    keys = {m.key for m in MACHINES}
    if args.cmd in ("inject", "clear"):
        if args.machine not in keys:
            parser.error(f"unknown machine {args.machine!r}; known: {', '.join(sorted(keys))}")
        command: dict[str, Any] = {"action": args.cmd, "machine": args.machine}
        if args.cmd == "inject":
            command.update(fault=args.fault, duration_s=args.duration_s)
        print(f"queued: {queue_command(args.control_dir, command)}")
        return 0

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    stats: Ai4iStats = calibrate_from_csv(AI4I_CSV)
    sim = FleetSim(
        start_states(),
        stats,
        seed=args.seed,
        tick_seconds=args.tick_seconds,
        wear_min_per_hour=args.wear_min_per_hour,
        storyline=Storyline(
            mode=args.storyline,
            machine=args.story_machine,
            delay_s=args.story_delay_min * 60,
            duration_s=args.story_minutes * 60,
        ),
    )
    with httpx.Client(base_url=args.api, timeout=30) as client:
        poster = Poster(client, uuid.UUID(args.tenant_id), args.email, args.password)
        try:
            while not stop.is_set():  # wait for the API instead of dying on start-up
                try:
                    facility_id, asset_ids = discover(poster, args.facility_id)
                    break
                except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                    logger.warning("API not ready (%s); retrying in 5s", exc.__class__.__name__)
                    stop.wait(5)
            else:
                return 0
            logger.info(
                "simulating %d machines in facility %s, tick %.0fs, storyline=%s, seed=%d",
                len(asset_ids), facility_id, args.tick_seconds, args.storyline, args.seed,
            )  # fmt: skip
            result = run(
                poster, sim, asset_ids,
                sleep=_interruptible_sleep(stop), should_stop=stop.is_set,
                max_ticks=args.max_ticks, control_dir=args.control_dir,
            )  # fmt: skip
        except SimulatorError as exc:
            logger.error("stopping: %s", exc)
            return 2
    logger.info("stopped: %s", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
