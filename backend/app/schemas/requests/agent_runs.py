"""Pydantic schemas for agent_runs API endpoints (PROJECT_PLAN.md §4.4, §5)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentRunTriggerRequest(BaseModel):
    """Payload to initiate a 5-agent LangGraph pipeline run."""

    trigger: Literal["scheduled", "user_query", "alert"] = Field(
        default="scheduled",
        description="Trigger mechanism initiating the pipeline",
    )
    user_query: str | None = Field(
        default=None,
        description="Optional operator query or 'what-if' failure prompt",
    )


class AgentRunResponse(BaseModel):
    """Summary of an executed agent run for list views."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    facility_id: uuid.UUID | None
    trigger: str
    status: str
    duration_ms: int
    validation_errors_json: list[str]
    decision_report: str | None
    confidence: float | None
    created_at: datetime


class AgentRunDetailResponse(AgentRunResponse):
    """Full execution snapshot for the Agent Reasoning Transparency Panel."""

    state_snapshot_json: dict[str, Any]
