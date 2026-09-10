import uuid
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.ml.anomaly import AnomalyCheckResult
from app.schemas.requests.telemetry import SensorReadingIn

# Deliberately much shorter than 3.1's 30/90/365-day feature windows: this baseline is
# "what does normal look like recently for this asset/sensor", not a long-run class
# stat, so a short lookback reacts to genuinely new operating conditions instead of
# diluting a live spike against a year of history.
LOOKBACK_DAYS = 7

# Live single-reading threshold, deliberately stricter than 3.1's window-local
# ANOMALY_Z_SCORE_THRESHOLD (2.0): that one counts occurrences across a wide window as
# a training feature, this one fires per reading, per batch, as data is ingested - a
# looser threshold here would flag routine noise on every ingest call before task 3.5's
# debounce/cooldown logic even exists to absorb it.
Z_SCORE_THRESHOLD = 3.0

# Below this many prior readings, a stddev is too noisy to trust - report `is_anomaly
# = False` with the sample count visible, never a false positive from thin history.
MIN_SAMPLE_COUNT = 10

_BASELINE_SQL = text(
    """
    SELECT count(*) AS cnt, avg(value) AS mean, stddev_samp(value) AS stddev
    FROM sensor_readings
    WHERE tenant_id = :tenant_id
      AND asset_id = :asset_id
      AND sensor_type = :sensor_type
      AND timestamp >= :window_start
      AND timestamp < :cutoff
    """
)


def _z_score_and_flag(
    *, value: float, mean: float | None, stddev: float | None, sample_count: int
) -> tuple[float | None, bool]:
    """Pure z-score/flag logic, split out from the DB-querying function below so the
    math (thresholds, the minimum-sample-count guard, zero-stddev handling) is unit
    testable without a database."""
    if sample_count < MIN_SAMPLE_COUNT or mean is None or not stddev:
        return None, False
    z_score = (value - mean) / stddev
    return z_score, abs(z_score) > Z_SCORE_THRESHOLD


async def check_readings_for_anomalies(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    readings: list[SensorReadingIn],
) -> list[AnomalyCheckResult]:
    """Rolling Z-score check on a just-ingested batch of readings (issue 3.4).

    Must run against a session already RLS-scoped to `tenant_id`
    (`get_timescale_scoped_session`) - the explicit `tenant_id` filter here is
    defense-in-depth, same pattern as every other tenant-scoped query in this repo.

    Readings are grouped by `(asset_id, sensor_type)`, and the rolling baseline for a
    group is computed once, using the group's *earliest* timestamp as the cutoff - this
    guarantees the baseline can never include any reading from the batch being checked
    (regardless of insertion or in-batch ordering), at the cost of one deliberate
    approximation: readings later in the same batch don't see earlier same-batch
    readings as part of their own baseline. That's the right trade-off for this task's
    typical caller (a live ingest tick posting near-simultaneous readings) - a fully
    per-reading baseline would mean an N+1 query per reading for no practical benefit
    here, and can be revisited if a later task needs finer granularity.
    """
    groups: dict[tuple[uuid.UUID, str], list[SensorReadingIn]] = defaultdict(list)
    for reading in readings:
        groups[(reading.asset_id, reading.sensor_type)].append(reading)

    results: list[AnomalyCheckResult] = []
    for (asset_id, sensor_type), group_readings in groups.items():
        cutoff = min(r.timestamp for r in group_readings)
        window_start = cutoff - timedelta(days=LOOKBACK_DAYS)

        row = (
            await session.execute(
                _BASELINE_SQL,
                {
                    "tenant_id": tenant_id,
                    "asset_id": asset_id,
                    "sensor_type": sensor_type,
                    "window_start": window_start,
                    "cutoff": cutoff,
                },
            )
        ).one()

        sample_count: int = row.cnt
        mean: float | None = row.mean
        stddev: float | None = row.stddev

        for reading in group_readings:
            z_score, is_anomaly = _z_score_and_flag(
                value=reading.value, mean=mean, stddev=stddev, sample_count=sample_count
            )

            results.append(
                AnomalyCheckResult(
                    asset_id=asset_id,
                    sensor_type=sensor_type,
                    value=reading.value,
                    timestamp=reading.timestamp,
                    rolling_mean=mean,
                    rolling_stddev=stddev,
                    sample_count=sample_count,
                    z_score=z_score,
                    is_anomaly=is_anomaly,
                )
            )

    return results
