"""Simulated IoT data generator.

Standalone script (not part of the FastAPI app) that posts realistic normal and
anomalous sensor readings to the `/telemetry` ingest endpoint, for local dev and
demo use. Run with:

    python -m scripts.iot_data_generator --base-url http://localhost:8000 \\
        --tenant-id <uuid> --email demo@tenant.example --password secret \\
        --num-assets 5 --rate 10 --anomaly-rate 0.02
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import random
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypedDict

import httpx

logger = logging.getLogger("iot_data_generator")

MAX_BATCH_SIZE = 1000  # must match TelemetryIngestRequest.readings max_length


class ReadingPayload(TypedDict):
    """Mirrors `SensorReadingIn` field-for-field, as JSON-ready values."""

    asset_id: str
    sensor_type: str
    value: float
    unit: str
    timestamp: str


@dataclass(frozen=True)
class SensorProfile:
    """Normal-operating-range parameters for one sensor type."""

    unit: str
    baseline: float
    amplitude: float
    noise_stddev: float
    period_s: float
    anomaly_multiplier: float


SENSOR_PROFILES: dict[str, SensorProfile] = {
    "temperature_c": SensorProfile(
        unit="C", baseline=65.0, amplitude=5.0, noise_stddev=0.5, period_s=600.0,
        anomaly_multiplier=2.5,
    ),
    "pressure_kpa": SensorProfile(
        unit="kPa", baseline=101.3, amplitude=2.0, noise_stddev=0.2, period_s=300.0,
        anomaly_multiplier=3.0,
    ),
    "vibration_mm_s": SensorProfile(
        unit="mm/s", baseline=2.0, amplitude=0.5, noise_stddev=0.1, period_s=60.0,
        anomaly_multiplier=6.0,
    ),
    "humidity_pct": SensorProfile(
        unit="%", baseline=45.0, amplitude=8.0, noise_stddev=1.0, period_s=1800.0,
        anomaly_multiplier=1.8,
    ),
}


def generate_reading(
    *,
    asset_id: uuid.UUID,
    sensor_type: str,
    tick_s: float,
    anomaly: bool,
    rng: random.Random,
    now: datetime | None = None,
) -> ReadingPayload:
    """Builds one `SensorReadingIn`-shaped payload dict.

    `tick_s` is a monotonically increasing seconds counter (not wall-clock) driving
    the sine-wave baseline, so callers can generate deterministic sequences for tests.
    """
    profile = SENSOR_PROFILES[sensor_type]
    phase = 2 * math.pi * tick_s / profile.period_s
    value = profile.baseline + profile.amplitude * math.sin(phase)
    value += rng.gauss(0.0, profile.noise_stddev)

    if anomaly:
        direction = rng.choice((-1.0, 1.0))
        value += direction * profile.amplitude * profile.anomaly_multiplier

    timestamp = now or datetime.now(UTC)
    return {
        "asset_id": str(asset_id),
        "sensor_type": sensor_type,
        "value": value,
        "unit": profile.unit,
        "timestamp": timestamp.isoformat(),
    }


def generate_batch(
    *,
    asset_ids: list[uuid.UUID],
    sensor_types: list[str],
    tick_s: float,
    anomaly_rate: float,
    rng: random.Random,
) -> list[ReadingPayload]:
    """One reading per (asset, sensor_type) pair, each independently rolled for anomaly."""
    readings = []
    for asset_id in asset_ids:
        for sensor_type in sensor_types:
            anomaly = rng.random() < anomaly_rate
            readings.append(
                generate_reading(
                    asset_id=asset_id,
                    sensor_type=sensor_type,
                    tick_s=tick_s,
                    anomaly=anomaly,
                    rng=rng,
                )
            )
    return readings


def chunk_readings(
    readings: list[ReadingPayload], batch_size: int
) -> list[list[ReadingPayload]]:
    batch_size = min(batch_size, MAX_BATCH_SIZE)
    return [readings[i : i + batch_size] for i in range(0, len(readings), batch_size)]


class TelemetryClient:
    """Thin wrapper: logs in once, posts batches, re-logs-in on 401."""

    def __init__(
        self, *, base_url: str, tenant_id: uuid.UUID, email: str, password: str
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)
        self._tenant_id = tenant_id
        self._email = email
        self._password = password
        self._access_token: str | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _login(self) -> None:
        response = await self._client.post(
            "/login",
            json={
                "tenant_id": str(self._tenant_id),
                "email": self._email,
                "password": self._password,
            },
        )
        response.raise_for_status()
        self._access_token = response.json()["access_token"]

    async def post_batch(self, readings: list[ReadingPayload]) -> int:
        if self._access_token is None:
            await self._login()

        for attempt in range(2):
            response = await self._client.post(
                "/telemetry",
                json={"readings": readings},
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            if response.status_code == httpx.codes.UNAUTHORIZED and attempt == 0:
                await self._login()
                continue
            response.raise_for_status()
            return int(response.json()["accepted"])

        raise RuntimeError("unreachable")  # pragma: no cover


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="API base URL, e.g. http://localhost:8000")
    parser.add_argument("--tenant-id", required=True, type=uuid.UUID)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument(
        "--num-assets", type=int, default=5, help="Number of simulated asset UUIDs to generate"
    )
    parser.add_argument(
        "--sensor-types",
        nargs="+",
        default=list(SENSOR_PROFILES),
        choices=list(SENSOR_PROFILES),
    )
    parser.add_argument(
        "--rate", type=float, default=1.0, help="Batches posted per second (fractional allowed)"
    )
    parser.add_argument(
        "--anomaly-rate",
        type=float,
        default=0.02,
        help="Independent probability per (asset, sensor_type, batch) of an anomalous reading",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=MAX_BATCH_SIZE,
        help="Max readings per POST /telemetry call",
    )
    parser.add_argument(
        "--duration", type=float, default=None, help="Seconds to run for; omit to run until Ctrl+C"
    )
    parser.add_argument("--seed", type=int, default=None, help="Random seed, for reproducible runs")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> None:
    rng = random.Random(args.seed)
    asset_ids = [uuid.uuid4() for _ in range(args.num_assets)]
    logger.info("Simulating %d assets: %s", len(asset_ids), asset_ids)

    client = TelemetryClient(
        base_url=args.base_url,
        tenant_id=args.tenant_id,
        email=args.email,
        password=args.password,
    )
    interval_s = 1.0 / args.rate if args.rate > 0 else 0.0
    tick_s = 0.0
    start = time.monotonic()

    try:
        while args.duration is None or (time.monotonic() - start) < args.duration:
            loop_start = time.monotonic()
            readings = generate_batch(
                asset_ids=asset_ids,
                sensor_types=args.sensor_types,
                tick_s=tick_s,
                anomaly_rate=args.anomaly_rate,
                rng=rng,
            )
            for chunk in chunk_readings(readings, args.batch_size):
                try:
                    accepted = await client.post_batch(chunk)
                    logger.info("Posted %d readings (accepted=%d)", len(chunk), accepted)
                except httpx.HTTPError as exc:
                    logger.warning("Telemetry post failed, will retry next tick: %s", exc)

            tick_s += interval_s if interval_s else 1.0
            elapsed = time.monotonic() - loop_start
            await asyncio.sleep(max(0.0, interval_s - elapsed))
    finally:
        await client.aclose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        logger.info("Stopped by user")


if __name__ == "__main__":
    main()
