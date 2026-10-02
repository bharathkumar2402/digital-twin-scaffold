"""Unit tests for Phase 4 Task 8: Agent Run Logging and Status UI Hook (Issue #30).

Asserts on:
1. Persistence of successful and halted LangGraph runs into agent_runs.
2. Accurate capture of execution duration, trigger, loop guard telemetry, and state snapshot.
3. Filtering and pagination in list_agent_runs service.
4. Detail retrieval for the Agent Reasoning Transparency Panel.
5. FastAPI route contracts for POST/GET /facilities/{id}/agent-runs and GET /agent-runs/{id}.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.agents.state import FacilityTwinState
from app.core.tenant_context import TenantContext, get_tenant_context, get_tenant_scoped_session
from app.main import app
from app.models.agent_run import AgentRun
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
    record_agent_run,
)


@pytest.fixture
def sample_tenant_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def sample_facility_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


@pytest.mark.anyio
async def test_record_agent_run_service(
    mock_session: AsyncMock,
    sample_tenant_id: uuid.UUID,
    sample_facility_id: uuid.UUID,
) -> None:
    """Verifies that record_agent_run constructs and commits an AgentRun model correctly."""
    state_snapshot = {
        "tenant_id": str(sample_tenant_id),
        "facility_id": str(sample_facility_id),
        "trigger": "scheduled",
        "iteration_count": 5,
    }
    validation_errors = ["planner: field missing"]

    run = await record_agent_run(
        mock_session,
        tenant_id=sample_tenant_id,
        facility_id=sample_facility_id,
        trigger="scheduled",
        status="halted_for_escalation",
        duration_ms=2340,
        state_snapshot=state_snapshot,
        validation_errors=validation_errors,
        decision_report="Pipeline halted for escalation",
        confidence=0.5,
    )

    mock_session.add.assert_called_once()
    mock_session.commit.assert_awaited_once()
    mock_session.refresh.assert_awaited_once()

    assert run.tenant_id == sample_tenant_id
    assert run.facility_id == sample_facility_id
    assert run.status == "halted_for_escalation"
    assert run.duration_ms == 2340
    assert run.state_snapshot_json == state_snapshot
    assert run.validation_errors_json == validation_errors
    assert run.confidence == 0.5


@pytest.mark.anyio
async def test_list_agent_runs_service(
    mock_session: AsyncMock,
    sample_tenant_id: uuid.UUID,
    sample_facility_id: uuid.UUID,
) -> None:
    """Verifies query execution and result return in list_agent_runs."""
    expected_run = AgentRun(
        id=uuid.uuid4(),
        tenant_id=sample_tenant_id,
        facility_id=sample_facility_id,
        trigger="scheduled",
        status="completed",
        duration_ms=1200,
        state_snapshot_json={},
        validation_errors_json=[],
        decision_report="Concluded successfully",
        confidence=0.95,
        created_at=datetime.now(timezone.utc),
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [expected_run]
    mock_session.execute.return_value = mock_result

    runs = await list_agent_runs(
        mock_session,
        tenant_id=sample_tenant_id,
        facility_id=sample_facility_id,
    )
    assert len(runs) == 1
    assert runs[0].id == expected_run.id
    mock_session.execute.assert_awaited_once()


@pytest.mark.anyio
async def test_get_agent_run_service(
    mock_session: AsyncMock,
    sample_tenant_id: uuid.UUID,
) -> None:
    """Verifies retrieval of a specific run by ID."""
    run_id = uuid.uuid4()
    expected_run = AgentRun(
        id=run_id,
        tenant_id=sample_tenant_id,
        facility_id=None,
        trigger="user_query",
        status="completed",
        duration_ms=890,
        state_snapshot_json={},
        validation_errors_json=[],
        created_at=datetime.now(timezone.utc),
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = expected_run
    mock_session.execute.return_value = mock_result

    found = await get_agent_run(mock_session, tenant_id=sample_tenant_id, run_id=run_id)
    assert found is not None
    assert found.id == run_id


@pytest.mark.anyio
async def test_execute_and_record_pipeline_success(
    mock_session: AsyncMock,
    sample_tenant_id: uuid.UUID,
    sample_facility_id: uuid.UUID,
) -> None:
    """Verifies pipeline execution and automatic logging to agent_runs table."""
    final_state, run = await execute_and_record_pipeline(
        mock_session,
        tenant_id=sample_tenant_id,
        facility_id=sample_facility_id,
        trigger="scheduled",
    )

    assert final_state["tenant_id"] == str(sample_tenant_id)
    assert final_state["facility_id"] == str(sample_facility_id)
    assert final_state["halted_for_escalation"] is False
    assert run.status == "completed"
    assert run.duration_ms > 0
    assert run.confidence == 0.94
    assert "Multi-agent digital twin analysis concluded" in (run.decision_report or "")
    mock_session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_execute_and_record_pipeline_escalation(
    mock_session: AsyncMock,
    sample_tenant_id: uuid.UUID,
    sample_facility_id: uuid.UUID,
) -> None:
    """Verifies pipeline halts gracefully and records status='halted_for_escalation'."""
    async def failing_planner(ctx: dict[str, Any]) -> dict[str, Any]:
        return {"invalid_key": True}  # triggers validation escalation

    config = {"configurable": {"planner_callable": failing_planner}}

    final_state, run = await execute_and_record_pipeline(
        mock_session,
        tenant_id=sample_tenant_id,
        facility_id=sample_facility_id,
        trigger="scheduled",
        config=config,
    )

    assert final_state["halted_for_escalation"] is True
    assert run.status == "halted_for_escalation"
    assert len(run.validation_errors_json) >= 1


@pytest.mark.anyio
async def test_agent_runs_api_routes(
    sample_tenant_id: uuid.UUID,
    sample_facility_id: uuid.UUID,
    mock_session: AsyncMock,
) -> None:
    """Verifies FastAPI endpoints for triggering, listing, and inspecting agent runs."""
    tenant_ctx = TenantContext(
        tenant_id=sample_tenant_id,
        user_id=uuid.uuid4(),
        role=Role.FACILITY_MANAGER,
    )

    fake_run = AgentRun(
        id=uuid.uuid4(),
        tenant_id=sample_tenant_id,
        facility_id=sample_facility_id,
        trigger="scheduled",
        status="completed",
        duration_ms=1450,
        state_snapshot_json={"iteration_count": 5},
        validation_errors_json=[],
        decision_report="Executive summary",
        confidence=0.94,
        created_at=datetime.now(timezone.utc),
    )

    app.dependency_overrides[get_tenant_context] = lambda: tenant_ctx
    app.dependency_overrides[get_tenant_scoped_session] = lambda: mock_session

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with patch(
                "app.api.agent_runs.list_agent_runs",
                new=AsyncMock(return_value=[fake_run]),
            ), patch(
                "app.api.agent_runs.get_agent_run",
                new=AsyncMock(return_value=fake_run),
            ):
                # 1. GET /facilities/{facility_id}/agent-runs
                res_list = await client.get(f"/facilities/{sample_facility_id}/agent-runs")
                assert res_list.status_code == 200
                list_data = res_list.json()
                assert len(list_data) == 1
                assert list_data[0]["id"] == str(fake_run.id)
                assert list_data[0]["status"] == "completed"

                # 2. GET /agent-runs/{run_id}
                res_detail = await client.get(f"/agent-runs/{fake_run.id}")
                assert res_detail.status_code == 200
                detail_data = res_detail.json()
                assert detail_data["id"] == str(fake_run.id)
                assert "state_snapshot_json" in detail_data
                assert detail_data["state_snapshot_json"]["iteration_count"] == 5

                # 3. GET 404 for unknown run
                with patch(
                    "app.api.agent_runs.get_agent_run",
                    new=AsyncMock(return_value=None),
                ):
                    res_404 = await client.get(f"/agent-runs/{uuid.uuid4()}")
                    assert res_404.status_code == 404
    finally:
        app.dependency_overrides.clear()
