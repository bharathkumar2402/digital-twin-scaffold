"""Scores every asset in a facility against the trained XGBoost model (issue 3.3).

Wraps 3.1's `feature_engineering_service` (feature vectors) and 3.2's `app.ml.train`
(model load) end to end: pull each asset's `AssetFeatureSet`, vectorize it through
`app.ml.feature_vector.vectorize` (the single source of truth for column order, so
this can never drift from what `train.py` trained on), score it, validate the result
against `RiskScoreResult`, and persist it. This is a plain service function called by
`app/workers/risk_tasks.py`'s Celery task - not a LangGraph agent itself. Phase 4's
Risk Assessment agent will wrap this same function as one of its tools.
"""

import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from xgboost import XGBClassifier

from app.core.config import settings
from app.core.storage import get_minio_client
from app.ml.feature_vector import FEATURE_NAMES, vectorize
from app.ml.train import LATEST_POINTER_KEY, load_model
from app.models.facility import Facility
from app.models.risk_score import RiskScore
from app.schemas.ml.asset_features import AssetFeatureSet
from app.schemas.ml.risk_score import RiskScoreResult
from app.services.feature_engineering_service import (
    build_facility_features,
    build_snapshot_features,
)
from app.services.rul_inference_service import predict_facility_rul
from app.services.vibration_inference_service import collect_vibration_outcomes, vibration_factors

logger = logging.getLogger("services.risk_inference")

# Loaded lazily and cached per (version-string) - a Celery worker process scores many
# facilities over its lifetime, and re-fetching the ~small XGBoost artifact from MinIO
# on every asset (or even every task run) would be pure waste. Keyed by the resolved
# version string, not by None, so switching `latest.json` to a new version during a
# worker's lifetime is picked up on the next call rather than pinned forever.
_MODEL_CACHE: dict[str, tuple[XGBClassifier, list[str]]] = {}


def _resolve_and_load_model(
    model_version: str | None,
) -> tuple[XGBClassifier, list[str], str]:
    """Loads (model, feature_names, resolved_version). Raises if MinIO has no model
    published yet (`load_model` surfaces the underlying S3/MinIO error) - the caller
    lets that fail the Celery task rather than silently writing a garbage score."""
    if model_version is not None and model_version in _MODEL_CACHE:
        model, feature_names = _MODEL_CACHE[model_version]
        return model, feature_names, model_version

    model, feature_names, _metrics = load_model(model_version)
    resolved_version = model_version
    if resolved_version is None:
        client = get_minio_client()
        pointer_obj = client.get_object(settings.minio_models_bucket, LATEST_POINTER_KEY)
        try:
            resolved_version = json.loads(pointer_obj.read())["version"]
        finally:
            pointer_obj.close()
            pointer_obj.release_conn()

    assert resolved_version is not None
    _MODEL_CACHE[resolved_version] = (model, feature_names)
    return model, feature_names, resolved_version


def _build_factors(features: AssetFeatureSet) -> dict[str, float | int | str | None]:
    """Human-readable context behind a score - asset age and dependency-neighbor
    signals (this repo's stand-ins for "last maintenance date"/"failure rate for that
    class", per app/schemas/ml/asset_features.py's docstring), plus each sensor's
    30-day anomaly count, the most recent and most volatile window. Not the raw model
    internals (e.g. SHAP values) - that's a possible future enhancement, not scoped
    here."""
    factors: dict[str, float | int | str | None] = {
        "asset_status": features.asset_status,
        "asset_age_days": features.asset_age_days,
        "dependency_neighbor_count": features.dependency_neighbor_count,
        "dependency_neighbor_offline_count": features.dependency_neighbor_offline_count,
        "dependency_neighbor_maintenance_count": features.dependency_neighbor_maintenance_count,
    }
    window_30 = features.windows.get(30, {})
    for sensor_type, stats in window_30.items():
        factors[f"{sensor_type}_30d_anomaly_count"] = stats.anomaly_count
        factors[f"{sensor_type}_30d_latest_value"] = stats.latest_value
    return factors


async def _get_facility_or_404(
    session: AsyncSession, *, tenant_id: uuid.UUID, facility_id: uuid.UUID
) -> Facility:
    result = await session.execute(
        select(Facility).where(Facility.id == facility_id, Facility.tenant_id == tenant_id)
    )
    facility = result.scalar_one_or_none()
    if facility is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Facility not found")
    return facility


async def score_facility(
    main_session: AsyncSession,
    timescale_session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    model_version: str | None = None,
    now: datetime | None = None,
) -> list[RiskScore]:
    """Scores every asset in a facility and persists one `RiskScore` row per asset.

    Both sessions must already be RLS-scoped to `tenant_id`, same convention as
    `feature_engineering_service.build_facility_features`. Loads the model once for
    the whole facility, not once per asset.
    """
    await _get_facility_or_404(main_session, tenant_id=tenant_id, facility_id=facility_id)
    now = now or datetime.now(UTC)

    model, feature_names, resolved_version = _resolve_and_load_model(model_version)
    if feature_names != list(FEATURE_NAMES):
        raise ValueError(
            "loaded model's feature_names do not match app.ml.feature_vector.FEATURE_NAMES "
            "- refusing to score with a mismatched column layout"
        )

    feature_sets = await build_facility_features(
        main_session, timescale_session, tenant_id=tenant_id, facility_id=facility_id, now=now
    )

    # The model is trained on single-snapshot AI4I rows, so it is scored on each asset's latest
    # simultaneous snapshot - not on the windowed stats in `feature_sets`, which stay the
    # explainability context. An asset without a coherent snapshot gets no score at all
    # rather than a number the model cannot meaningfully produce.
    snapshots = await build_snapshot_features(
        timescale_session, tenant_id=tenant_id, base_features=feature_sets
    )
    for features in feature_sets:
        if snapshots[features.asset_id].features is None:
            logger.warning(
                "asset %s not scored: %s",
                features.asset_id,
                snapshots[features.asset_id].skip_reason,
            )
    feature_sets = [f for f in feature_sets if snapshots[f.asset_id].features is not None]

    # Remaining Useful Life (Phase 3b). Never allowed to break risk scoring: an asset with
    # no C-MAPSS-style telemetry, or a facility with no published RUL model, gets no RUL.
    rul_outcomes = await predict_facility_rul(
        timescale_session,
        tenant_id=tenant_id,
        asset_ids=[f.asset_id for f in feature_sets],
        now=now,
    )

    # Vibration anomaly verdicts (Phase 3b), recorded with their model and dataset identity.
    # Opt-in, and never allowed to break risk scoring (the collector swallows store errors).
    vibration_outcomes = (
        await collect_vibration_outcomes(tenant_id, [f.asset_id for f in feature_sets])
        if settings.vibration_enabled
        else {}
    )

    saved: list[RiskScore] = []
    for features in feature_sets:
        snapshot = snapshots[features.asset_id]
        assert snapshot.features is not None  # filtered above
        vector = vectorize(snapshot.features).reshape(1, -1)
        probability = float(model.predict_proba(vector)[0, 1])
        score = max(0.0, min(100.0, probability * 100.0))

        factors = _build_factors(features)
        factors["scored_on_snapshot_at"] = (
            snapshot.snapshot_at.isoformat() if snapshot.snapshot_at else None
        )
        rul = rul_outcomes[features.asset_id]
        if rul.estimate is not None:
            factors["rul_model_version"] = rul.estimate.model_version
            factors["rul_model_subset"] = rul.estimate.model_subset
            factors["rul_dataset_name"] = rul.estimate.dataset_name
            factors["rul_dataset_version"] = rul.estimate.dataset_version
            factors["rul_cycle"] = rul.estimate.cycle
        elif rul.skip_reason not in (None, "no_cmapss_telemetry"):
            factors["rul_unavailable_reason"] = rul.skip_reason

        if features.asset_id in vibration_outcomes:
            factors.update(vibration_factors(vibration_outcomes[features.asset_id]))

        result = RiskScoreResult(
            asset_id=features.asset_id,
            tenant_id=features.tenant_id,
            facility_id=features.facility_id,
            score=score,
            rul_cycles=rul.estimate.rul_cycles if rul.estimate is not None else None,
            model_version=resolved_version,
            factors=factors,
            computed_at=now,
        )

        row = RiskScore(
            tenant_id=result.tenant_id,
            facility_id=result.facility_id,
            asset_id=result.asset_id,
            score=result.score,
            rul_cycles=result.rul_cycles,
            model_version=result.model_version,
            factors_json=result.factors,
            computed_at=result.computed_at,
        )
        main_session.add(row)
        saved.append(row)

    await main_session.commit()
    return saved


async def get_latest_risk_scores(
    session: AsyncSession, *, tenant_id: uuid.UUID, facility_id: uuid.UUID
) -> list[RiskScore]:
    """The most recent `RiskScore` row per asset in a facility, read-only."""
    await _get_facility_or_404(session, tenant_id=tenant_id, facility_id=facility_id)

    # DISTINCT ON in the database (index ix_risk_scores_facility_asset_computed, migration 0013):
    # scoring every minute makes history grow, so the newest row per asset must not be picked in
    # Python from every row. Ties on computed_at are broken by id, so the answer is repeatable.
    # (The SkipScan crash that forbids DISTINCT on sensor_readings is a TimescaleDB-only problem;
    # risk_scores lives in plain Postgres.)
    result = await session.execute(
        select(RiskScore)
        .where(RiskScore.tenant_id == tenant_id, RiskScore.facility_id == facility_id)
        .distinct(RiskScore.asset_id)
        .order_by(RiskScore.asset_id, RiskScore.computed_at.desc(), RiskScore.id.desc())
    )
    return list(result.scalars().all())


async def get_risk_history(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    limit_per_asset: int,
) -> dict[uuid.UUID, list[tuple[datetime, float, float | None]]]:
    """The last `limit_per_asset` scores of every asset in a facility, oldest first.

    One bounded query: a window function ranks each asset's rows newest-first and keeps the top N,
    so history that grows over time is never loaded in full (unlike `get_latest_risk_scores`).
    Ties on `computed_at` are broken by `id`, so the same rows come back every time. Read-only.
    Tenant isolation is double-layered: the caller's tenant is filtered explicitly here, and the
    session is RLS-scoped as well.
    """
    await _get_facility_or_404(session, tenant_id=tenant_id, facility_id=facility_id)

    rank = (
        func.row_number()
        .over(
            partition_by=RiskScore.asset_id,
            order_by=(RiskScore.computed_at.desc(), RiskScore.id.desc()),
        )
        .label("rank")
    )
    ranked = (
        select(
            RiskScore.asset_id.label("asset_id"),
            RiskScore.computed_at.label("computed_at"),
            RiskScore.score.label("score"),
            RiskScore.rul_cycles.label("rul_cycles"),
            rank,
        )
        .where(RiskScore.tenant_id == tenant_id, RiskScore.facility_id == facility_id)
        .subquery()
    )
    result = await session.execute(
        select(ranked.c.asset_id, ranked.c.computed_at, ranked.c.score, ranked.c.rul_cycles)
        .where(ranked.c.rank <= limit_per_asset)
        # rank descending = oldest first within each asset
        .order_by(ranked.c.asset_id, ranked.c.rank.desc())
    )
    history: dict[uuid.UUID, list[tuple[datetime, float, float | None]]] = {}
    for asset_id, computed_at, score, rul_cycles in result.all():
        history.setdefault(asset_id, []).append((computed_at, float(score), rul_cycles))
    return history
