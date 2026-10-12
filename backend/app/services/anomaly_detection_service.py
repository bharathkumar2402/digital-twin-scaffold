import uuid
from collections import defaultdict

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY, TIMESTAMP, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import String

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

# Upper bound on how many prior readings feed one baseline (the newest ones inside the lookback
# window). Without it the cost of every ingest grows with the history: measured in Phase L task
# L.0, one 220-row batch took 342 ms with 30k rows stored and 2080 ms with 1M. With it, each
# (asset, sensor) lookup reads at most this many index entries, however long the history is.
# Histories shorter than the cap give exactly the statistics of the full window, as before.
# 200 = about 50 minutes at the 15 s demo tick; measured on 1M stored rows, the whole baseline
# statement takes ~79 ms at 500, ~37 ms at 200 and ~24 ms at 100, and 200 samples still give a
# stable standard deviation (about +-5 %).
BASELINE_MAX_SAMPLES = 200

# One statement for the whole batch: `unnest` turns the per-group (asset, sensor, cutoff) triples
# into rows and the LATERAL subquery takes each group's newest `cap` readings strictly before its
# cutoff (so a batch can never be part of its own baseline) and not older than the lookback window.
# Served by ix_sensor_readings_asset_sensor_time (migrations_timescale 0003). The explicit
# tenant_id filter sits on top of the table's RLS policy (defense in depth).
_BASELINE_SQL = text(
    """
    SELECT g.asset_id, g.sensor_type, s.cnt, s.mean, s.stddev
    FROM unnest(:asset_ids, :sensor_types, :cutoffs) AS g(asset_id, sensor_type, cutoff)
    CROSS JOIN LATERAL (
        SELECT count(*) AS cnt, avg(w.value) AS mean, stddev_samp(w.value) AS stddev
        FROM (
            SELECT r.value
            FROM sensor_readings r
            WHERE r.tenant_id = :tenant_id
              AND r.asset_id = g.asset_id
              AND r.sensor_type = g.sensor_type
              AND r.timestamp < g.cutoff
              AND r.timestamp >= g.cutoff - make_interval(days => :lookback_days)
            ORDER BY r.timestamp DESC
            LIMIT :cap
        ) AS w
    ) AS s
    """
).bindparams(
    bindparam("asset_ids", type_=ARRAY(UUID(as_uuid=True))),
    bindparam("sensor_types", type_=ARRAY(String)),
    bindparam("cutoffs", type_=ARRAY(TIMESTAMP(timezone=True))),
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

    All groups' baselines come from ONE statement (`_BASELINE_SQL`), and each reads at most
    `BASELINE_MAX_SAMPLES` rows, so the cost of an ingest does not grow with stored history.
    """
    groups: dict[tuple[uuid.UUID, str], list[SensorReadingIn]] = defaultdict(list)
    for reading in readings:
        groups[(reading.asset_id, reading.sensor_type)].append(reading)

    if not groups:
        return []
    keys = list(groups)
    cutoffs = [min(r.timestamp for r in groups[key]) for key in keys]
    rows = (
        await session.execute(
            _BASELINE_SQL,
            {
                "asset_ids": [asset_id for asset_id, _ in keys],
                "sensor_types": [sensor_type for _, sensor_type in keys],
                "cutoffs": cutoffs,
                "tenant_id": tenant_id,
                "lookback_days": LOOKBACK_DAYS,
                "cap": BASELINE_MAX_SAMPLES,
            },
        )
    ).all()
    baselines = {(row.asset_id, row.sensor_type): row for row in rows}

    results: list[AnomalyCheckResult] = []
    for (asset_id, sensor_type), group_readings in groups.items():
        row = baselines[(asset_id, sensor_type)]
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
