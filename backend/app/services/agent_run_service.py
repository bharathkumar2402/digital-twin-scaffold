"""Service for logging and querying agent runs (PROJECT_PLAN.md §4.4, §5, Issue #30).

Handles persistence of every 5-agent LangGraph run to the `agent_runs` table,
including full state snapshots, duration, loop guard telemetry, and validation failures.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.graph import run_facility_twin_pipeline
from app.agents.state import FacilityTwinState, create_initial_state
from app.models.agent_run import AgentRun

logger = logging.getLogger("services.agent_runs")


async def record_agent_run(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID | None,
    trigger: str,
    status: str,
    duration_ms: int,
    state_snapshot: dict[str, Any],
    validation_errors: list[str],
    decision_report: str | None = None,
    confidence: float | None = None,
) -> AgentRun:
    """Inserts a completed or halted agent run record into agent_runs."""
    run = AgentRun(
        tenant_id=tenant_id,
        facility_id=facility_id,
        trigger=trigger,
        status=status,
        duration_ms=duration_ms,
        state_snapshot_json=state_snapshot,
        validation_errors_json=validation_errors,
        decision_report=decision_report,
        confidence=confidence,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def list_agent_runs(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AgentRun]:
    """Retrieves paginated agent runs for a tenant, optionally filtered by facility."""
    query = (
        select(AgentRun)
        .where(AgentRun.tenant_id == tenant_id)
        .order_by(AgentRun.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if facility_id is not None:
        query = query.where(AgentRun.facility_id == facility_id)

    result = await session.execute(query)
    return list(result.scalars().all())


async def get_agent_run(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
) -> AgentRun | None:
    """Fetches a specific agent run by ID within the tenant context."""
    query = select(AgentRun).where(
        AgentRun.id == run_id,
        AgentRun.tenant_id == tenant_id,
    )
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def execute_and_record_pipeline(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    facility_id: uuid.UUID,
    trigger: str = "scheduled",
    user_query: str | None = None,
    config: RunnableConfig | None = None,
) -> tuple[FacilityTwinState, AgentRun]:
    """Executes the full 5-agent pipeline and persists the outcome to agent_runs."""
    initial_state = create_initial_state(
        tenant_id=str(tenant_id),
        facility_id=str(facility_id),
        trigger=trigger,  # type: ignore[arg-type]
        user_query=user_query,
    )

    start_time = time.monotonic()
    try:
        final_state = await run_facility_twin_pipeline(initial_state, config=config)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Catastrophic error running facility twin pipeline: %s", exc)
        duration_ms = int((time.monotonic() - start_time) * 1000)
        run = await record_agent_run(
            session,
            tenant_id=tenant_id,
            facility_id=facility_id,
            trigger=trigger,
            status="failed",
            duration_ms=duration_ms,
            state_snapshot=initial_state,
            validation_errors=[f"Pipeline unhandled exception: {exc}"],
            decision_report=None,
            confidence=0.0,
        )
        raise

    duration_ms = int((time.monotonic() - start_time) * 1000)

    # Classify run status
    if final_state.get("halted_for_escalation"):
        status = "halted_for_escalation"
    elif final_state.get("errors"):
        status = "failed"
    else:
        status = "completed"

    val_errors = list(final_state.get("validation_errors", []))

    run = await record_agent_run(
        session,
        tenant_id=tenant_id,
        facility_id=facility_id,
        trigger=trigger,
        status=status,
        duration_ms=duration_ms,
        state_snapshot=dict(final_state),
        validation_errors=val_errors,
        decision_report=final_state.get("decision_report"),
        confidence=final_state.get("confidence"),
    )

    return final_state, run
