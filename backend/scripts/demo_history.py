"""Demo-only risk HISTORY, so the trend views have something to show (UI task U7).

The demo scores every machine once, so each asset has hours of identical scores and a trend is a
flat line. This adds a few past days per machine. It is a scripted STORYLINE, and is labelled as
such (`factors_json.demo_history`), but every number in it is real:

  * every point is the real risk model's output (`predict_proba`) on a real AI4I 2020 held-out
    record (one the model never trained on), built through the same feature path scoring uses;
  * only the CHOICE of which record stands for which day is scripted: a machine that needs
    attention gets an ordered path of records whose scores rise toward its real latest score, a
    healthy machine gets records that score near its latest score;
  * the latest score of each machine is the existing REAL one and is never touched.

Nothing here trains a model (CLAUDE.md rule 8) and nothing changes the scoring pipeline. It only
inserts `risk_scores` rows, and only when none exist yet (the table is insert-only for the app
role). Run it through `python -m scripts.demo_setup --history-days 6`.
"""

from __future__ import annotations

import logging
import random
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, text

from app.core.db import async_session_factory, timescale_session_factory
from app.core.tenant_context import scope_session_to_tenant
from app.ml.ai4i_replay import heldout_rows, replay_readings
from app.ml.feature_vector import vectorize
from app.models.risk_score import RiskScore
from app.schemas.ml.asset_features import AssetFeatureSet
from app.services.feature_engineering_service import (
    SNAPSHOT_RAW_SENSORS,
    build_asset_features_from_readings,
    build_facility_features,
    snapshot_readings,
)
from app.services.ml.dataset_mapping import map_ai4i2020_row
from app.services.risk_inference_service import _resolve_and_load_model

logger = logging.getLogger(__name__)

# A machine at or above this latest score "needs attention" (the app's medium band starts at 34), so
# its story is a deterioration; below it, the machine is simply steady.
ATTENTION_SCORE = 34.0
# Candidate pool: every Nth held-out record, plus every failure record.
POOL_STRIDE = 4


def storyline_targets(final: float, steps: int, rng: random.Random) -> list[float]:
    """Target scores for `steps` past days, oldest first.

    A machine needing attention accelerates from a low start up to (but below) its latest score; a
    healthy one wobbles a little around its latest score. Pure and seeded, so reruns agree.
    """
    if steps <= 0:
        return []
    if final >= ATTENTION_SCORE:
        start = min(8.0, final * 0.15)
        return [start + (final - start) * ((i / steps) ** 1.5) * 0.97 for i in range(steps)]
    return [min(100.0, max(0.0, final + rng.uniform(-1.5, 1.5))) for _ in range(steps)]


def pick_nearest(pool_scores: list[float], targets: list[float]) -> list[int]:
    """For each target in order, the unused pool index whose score is closest to it.

    Each record is used at most once per machine, so a path never repeats a record.
    """
    if len(targets) > len(pool_scores):
        raise ValueError(
            f"need {len(targets)} distinct records but the pool has {len(pool_scores)}"
        )
    used: set[int] = set()
    picks: list[int] = []
    for target in targets:
        best = min(
            (i for i in range(len(pool_scores)) if i not in used),
            key=lambda i: abs(pool_scores[i] - target),
        )
        used.add(best)
        picks.append(best)
    return picks


def history_timestamps(end: datetime, steps: int) -> list[datetime]:
    """One timestamp per past day, oldest first (`steps` days ago ... 1 day ago)."""
    return [end - timedelta(days=steps - i) for i in range(steps)]


def _score_record(
    model: Any,
    base: AssetFeatureSet,
    row: dict[str, str],
    asset_id: uuid.UUID,
    stamp: datetime,
) -> float:
    """The model's 0-100 risk for one real record, through the same path as live scoring."""
    latest = {r.sensor_type: (r.value, stamp) for r in replay_readings(row, asset_id, stamp)}
    built = snapshot_readings({t: latest[t] for t in SNAPSHOT_RAW_SENSORS if t in latest})
    if isinstance(built, str):
        raise ValueError(f"record could not be turned into a snapshot: {built}")
    readings, at = built
    features = build_asset_features_from_readings(
        asset_id,
        readings,
        tenant_id=base.tenant_id,
        facility_id=base.facility_id,
        computed_at=at,
        asset_status=base.asset_status,
        asset_age_days=base.asset_age_days,
        dependency_neighbor_count=base.dependency_neighbor_count,
        dependency_neighbor_offline_count=base.dependency_neighbor_offline_count,
        dependency_neighbor_maintenance_count=base.dependency_neighbor_maintenance_count,
    )
    vector = vectorize(features).reshape(1, -1)
    return max(0.0, min(100.0, float(model.predict_proba(vector)[0, 1]) * 100.0))


async def backfill_risk_history(
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    asset_ids: dict[str, uuid.UUID],
    end: datetime,
    days: int,
    csv_path: Any,
    seed: int = 11,
) -> int:
    """Insert `days` past scores per machine. Returns how many rows were added (0 if skipped)."""
    if days <= 0:
        return 0
    model, _names, version = _resolve_and_load_model(None)
    rng = random.Random(seed)
    held_out = heldout_rows(csv_path)
    pool_rows = [
        r for i, r in enumerate(held_out) if i % POOL_STRIDE == 0 or map_ai4i2020_row(r).is_failure
    ]

    async with async_session_factory() as session:
        await scope_session_to_tenant(session, tenant_id)
        existing = await session.execute(
            text(
                "SELECT 1 FROM risk_scores WHERE facility_id = :f "
                "AND factors_json->>'demo_history' IS NOT NULL LIMIT 1"
            ),
            {"f": facility_id},
        )
        if existing.first() is not None:
            logger.info("risk history already present - skipping")
            return 0

        # Newest row per asset (rows arrive newest first, so keep the first one seen).
        newest: dict[uuid.UUID, RiskScore] = {}
        rows = await session.execute(
            select(RiskScore)
            .where(RiskScore.facility_id == facility_id)
            .order_by(RiskScore.asset_id, RiskScore.computed_at.desc())
        )
        for row in rows.scalars():
            newest.setdefault(row.asset_id, row)

        async with timescale_session_factory() as ts:
            await scope_session_to_tenant(ts, tenant_id)
            bases = {
                f.asset_id: f
                for f in await build_facility_features(
                    session, ts, tenant_id=tenant_id, facility_id=facility_id, now=end
                )
            }

        # Pool scores once, against a generic context; chosen records are re-scored per machine.
        generic_asset = next(iter(bases))
        generic = bases[generic_asset]
        stamp = end - timedelta(days=days + 1)
        pool_scores = [_score_record(model, generic, r, generic_asset, stamp) for r in pool_rows]

        stamps = history_timestamps(end, days)
        added: list[RiskScore] = []
        for asset_id in sorted(asset_ids.values()):
            current = newest.get(asset_id)
            base = bases.get(asset_id)
            if current is None or base is None:
                continue  # never scored (no coherent snapshot): nothing honest to extend
            targets = storyline_targets(current.score, days, rng)
            for step, pick in enumerate(pick_nearest(pool_scores, targets)):
                record = pool_rows[pick]
                score = _score_record(model, base, record, asset_id, stamps[step])
                added.append(
                    RiskScore(
                        tenant_id=tenant_id,
                        facility_id=facility_id,
                        asset_id=asset_id,
                        score=score,
                        rul_cycles=None,
                        model_version=current.model_version or version,
                        factors_json={
                            "demo_history": 1,
                            "ai4i_udi": map_ai4i2020_row(record).udi,
                        },
                        computed_at=stamps[step],
                    )
                )
        session.add_all(added)
        await session.commit()
        logger.info("risk history: %d scores over %d days", len(added), days)
        return len(added)


__all__ = [
    "ATTENTION_SCORE",
    "backfill_risk_history",
    "history_timestamps",
    "pick_nearest",
    "storyline_targets",
]
