"""L.7 recording and validation guarantees of automatic analysis (root CLAUDE.md rules 1 and 4).

The automatic path reuses `execute_and_record_pipeline` unchanged except for the `trigger` stored
in `agent_runs`. What matters: the reason is recorded, the agents still see a plain scheduled run,
a failed pipeline is recorded as `failed` (not lost), and no API module can start an automatic run.
"""

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agent_run_service import execute_and_record_pipeline


def _session() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.mark.anyio
async def test_the_reason_is_recorded_while_the_agents_see_a_scheduled_run() -> None:
    session = _session()
    seen: dict = {}

    async def pipeline(state, config=None):
        seen["trigger"] = state["trigger"]
        return {**state, "errors": [], "validation_errors": []}

    with patch("app.services.agent_run_service.run_facility_twin_pipeline", pipeline):
        _, run = await execute_and_record_pipeline(
            session,
            tenant_id=uuid.uuid4(),
            facility_id=uuid.uuid4(),
            trigger="scheduled",
            recorded_trigger="auto_high_risk",
        )

    assert seen["trigger"] == "scheduled"
    assert run.trigger == "auto_high_risk" and run.status == "completed"


@pytest.mark.anyio
async def test_without_a_reason_the_trigger_is_stored_as_before() -> None:
    async def pipeline(state, config=None):
        return {**state, "errors": [], "validation_errors": []}

    with patch("app.services.agent_run_service.run_facility_twin_pipeline", pipeline):
        _, run = await execute_and_record_pipeline(
            _session(), tenant_id=uuid.uuid4(), facility_id=uuid.uuid4(), trigger="alert"
        )

    assert run.trigger == "alert"


@pytest.mark.anyio
async def test_a_crashing_pipeline_is_recorded_as_failed_with_the_reason_and_reraised() -> None:
    session = _session()

    async def pipeline(state, config=None):
        raise RuntimeError("llm down")

    with patch("app.services.agent_run_service.run_facility_twin_pipeline", pipeline):
        with pytest.raises(RuntimeError):
            await execute_and_record_pipeline(
                session,
                tenant_id=uuid.uuid4(),
                facility_id=uuid.uuid4(),
                recorded_trigger="auto_startup",
            )

    (row,) = (call.args[0] for call in session.add.call_args_list)
    assert (row.trigger, row.status) == ("auto_startup", "failed")
    assert "llm down" in row.validation_errors_json[0]


@pytest.mark.anyio
async def test_a_run_whose_agents_failed_validation_is_recorded_failed_not_completed() -> None:
    """Rule 1 is enforced inside the graph; the wrapper must not paper over its errors."""

    async def pipeline(state, config=None):
        return {
            **state,
            "errors": ["risk_assessment: output failed validation twice"],
            "validation_errors": ["score out of range"],
        }

    with patch("app.services.agent_run_service.run_facility_twin_pipeline", pipeline):
        _, run = await execute_and_record_pipeline(
            _session(),
            tenant_id=uuid.uuid4(),
            facility_id=uuid.uuid4(),
            recorded_trigger="auto_high_risk",
        )

    assert run.status == "failed"
    assert run.validation_errors_json == ["score out of range"]


def test_no_api_module_can_start_an_automatic_analysis() -> None:
    """Rule 4: the automatic path lives only in the worker, never in a request handler."""
    api_dir = Path(__file__).resolve().parents[2] / "app" / "api"
    for source in api_dir.glob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert "auto_analysis" not in text, source.name
        assert "run_scheduled_analysis" not in text, source.name
        assert "auto_high_risk" not in text, source.name


def test_the_celery_task_rejects_an_unknown_reason() -> None:
    from app.workers.auto_analysis import run_scheduled_analysis

    with pytest.raises(ValueError):
        run_scheduled_analysis(str(uuid.uuid4()), str(uuid.uuid4()), "because_i_said_so")
