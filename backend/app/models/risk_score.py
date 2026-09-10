import uuid
from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, Float, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class RiskScore(UUIDPKMixin, Base):
    """One risk-inference result for one asset at one point in time (issue 3.3).

    Deliberately insert-only, never updated in place: PROJECT_PLAN.md §5 and this
    repo's Phase 3 DoD ("model version recorded alongside each stored risk score, for
    auditability") both point at keeping history rather than overwriting the latest
    score - callers read the latest row per asset via `ORDER BY computed_at DESC`.
    `factors_json` holds the feature-level context behind the score (asset age,
    dependency-neighbor counts, per-sensor anomaly counts) for later
    explainability/UI use, not the raw model internals.
    """

    __tablename__ = "risk_scores"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 100", name="ck_risk_scores_score_range"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=False, index=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id"), nullable=False, index=True
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    model_version: Mapped[str] = mapped_column(String(255), nullable=False)
    factors_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
