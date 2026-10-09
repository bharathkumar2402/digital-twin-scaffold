"""SQLAlchemy model for agent_runs (PROJECT_PLAN.md §5, Issue #30).

Persists every execution of the 5-agent LangGraph pipeline, including state snapshots,
validation failures, execution duration, and synthesized decision reports for the
transparency panel and audit logging.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class AgentRun(UUIDPKMixin, TimestampMixin, Base):
    """An execution record of the 5-agent LangGraph pipeline.

    Insert-only ledger of all agent runs per tenant and facility, capturing the full
    state machine snapshot, execution duration, loop guard telemetry, and validation
    errors for transparency and observability.
    """

    __tablename__ = "agent_runs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    facility_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=True, index=True
    )
    trigger: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # "completed", "halted_for_escalation", "failed"
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    state_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    validation_errors_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    decision_report: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
