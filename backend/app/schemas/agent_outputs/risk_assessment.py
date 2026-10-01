"""Pydantic output schema for Agent 2 — Risk Assessment Agent (PROJECT_PLAN.md §4.3)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ScoredAsset(BaseModel):
    """An individual asset scored and ranked by predictive failure urgency."""

    model_config = ConfigDict(extra="forbid")

    asset_id: uuid.UUID = Field(..., description="Target asset identifier")
    risk_score: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Predictive failure risk score from 0.0 (safe) to 100.0 (critical)",
    )
    urgency_rank: int = Field(
        ...,
        ge=1,
        description="1-indexed urgency rank within the scored facility asset set",
    )
    risk_tier: Literal["low", "medium", "high", "critical"] = Field(
        ...,
        description="Categorical risk band corresponding to numeric score",
    )
    predicted_failure_mode: str | None = Field(
        default=None,
        description="Specific failure mode if identified (e.g. HDF, TWF, PWF, OSF, RNF)",
    )
    primary_risk_factors: list[str] = Field(
        default_factory=list,
        description="Explanatory factor strings explaining why this score was assigned",
    )
    telemetry_anomaly_count_30d: int = Field(
        0,
        ge=0,
        description="Count of sensor anomalies recorded in the preceding 30-day window",
    )
    recommended_action: str = Field(
        ...,
        min_length=3,
        description="Recommended operational intervention (e.g. urgent inspection)",
    )


class RiskAssessmentOutput(BaseModel):
    """Pydantic validation schema for the Risk Assessment Agent's return value."""

    model_config = ConfigDict(extra="forbid")

    assessment_id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        description="Unique assessment run identifier",
    )
    facility_id: uuid.UUID = Field(..., description="Facility being assessed")
    model_version: str = Field(
        ...,
        min_length=1,
        description="Version string of the XGBoost model used for inference",
    )
    assessed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when assessment was performed",
    )
    ranked_assets: list[ScoredAsset] = Field(
        ...,
        description="List of scored assets ranked in descending order of urgency",
    )
    high_risk_count: int = Field(..., ge=0, description="Count of assets with risk_score >= 67")
    medium_risk_count: int = Field(..., ge=0, description="Count of assets with 34 <= score < 67")
    low_risk_count: int = Field(..., ge=0, description="Count of assets with risk_score < 34")
    executive_summary: str = Field(
        ...,
        min_length=5,
        description="Concise natural-language summary of facility-wide risk state",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Overall agent confidence score between 0.0 and 1.0",
    )
