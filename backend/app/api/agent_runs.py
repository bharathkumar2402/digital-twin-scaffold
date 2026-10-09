"""API endpoints for agent_runs (PROJECT_PLAN.md §4.4, §5, Issue #30).

Provides access to agent run execution history, failure audits, and full state snapshots
for the frontend Agent Reasoning Transparency Panel (Phase 6).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import require_roles
from app.core.tenant_context import TenantContext, get_tenant_context, get_tenant_scoped_session
from app.models.user import Role
from app.schemas.requests.agent_runs import (
    AgentRunDetailResponse,
    AgentRunResponse,
    AgentRunTriggerRequest,
)
from app.services.agent_run_service import (
    execute_and_record_pipeline,
    get_agent_run,
    list_agent_runs,
)

router = APIRouter(tags=["agent_runs"])

RUN_ROLES = (Role.TENANT_ADMIN, Role.FACILITY_MANAGER, Role.SUPERADMIN)


@router.post(
    "/facilities/{facility_id}/agent-runs",
    response_model=AgentRunDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def trigger_agent_run(
    facility_id: uuid.UUID,
    body: AgentRunTriggerRequest,
    context: TenantContext = Depends(require_roles(*RUN_ROLES)),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> AgentRunDetailResponse:
    """Triggers the full 5-agent LangGraph pipeline and persists the result to agent_runs."""
    _, run = await execute_and_record_pipeline(
        session,
        tenant_id=context.tenant_id,
        facility_id=facility_id,
        trigger=body.trigger,
        user_query=body.user_query,
    )
    return AgentRunDetailResponse.model_validate(run)


@router.get(
    "/facilities/{facility_id}/agent-runs",
    response_model=list[AgentRunResponse],
)
async def list_facility_agent_runs(
    facility_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> list[AgentRunResponse]:
    """Lists past agent runs for a facility, ordered newest first."""
    runs = await list_agent_runs(
        session,
        tenant_id=context.tenant_id,
        facility_id=facility_id,
        limit=limit,
        offset=offset,
    )
    return [AgentRunResponse.model_validate(r) for r in runs]


@router.get(
    "/agent-runs/{run_id}",
    response_model=AgentRunDetailResponse,
)
async def get_agent_run_details(
    run_id: uuid.UUID,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_tenant_scoped_session),
) -> AgentRunDetailResponse:
    """Retrieves full execution snapshot and reasoning history for a single agent run."""
    run = await get_agent_run(session, tenant_id=context.tenant_id, run_id=run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent run {run_id} not found",
        )
    return AgentRunDetailResponse.model_validate(run)
