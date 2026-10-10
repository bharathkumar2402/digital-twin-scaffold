# ruff: noqa: F811 - pytest fixtures imported from the sibling module and re-requested by name
"""`record_agent_run` under the real non-bypass `app_role` (RLS enforced).

Regression: it used to `session.refresh()` after `commit()`. The tenant GUC is SET LOCAL, so
it is gone after commit and the refresh SELECT saw zero rows under RLS - "Could not refresh
instance" on every real API call (the row was saved, the request failed). The earlier tests
passed because they did not run as the RLS-enforced role.
"""

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker
from testcontainers.postgres import PostgresContainer

from tests.integration.test_risk_inference_service import (  # noqa: F401 - fixtures
    _make_facility,
    _make_tenant,
    _scoped,
    fake_model,
    main_pg_container,
    main_session_factory,
    migrated_main_db,
)


async def test_record_agent_run_succeeds_under_rls_and_stays_tenant_isolated(
    migrated_main_db: PostgresContainer,
    main_session_factory: async_sessionmaker,
) -> None:
    from app.services.agent_run_service import get_agent_run, list_agent_runs, record_agent_run

    tenant_a = await _make_tenant(migrated_main_db, "Tenant Runs A")
    tenant_b = await _make_tenant(migrated_main_db, "Tenant Runs B")
    facility_a = await _make_facility(migrated_main_db, tenant_id=tenant_a, name="Plant A")

    session = await _scoped(main_session_factory, tenant_a)
    try:
        run = await record_agent_run(
            session,
            tenant_id=tenant_a,
            facility_id=facility_a,
            trigger="user_query",
            status="completed",
            duration_ms=12,
            state_snapshot={"k": "v"},
            validation_errors=[],
            decision_report="ok",
            confidence=0.9,
        )
        # server-side defaults are populated without a refresh round trip
        assert isinstance(run.id, uuid.UUID) and run.created_at is not None
    finally:
        await session.close()

    # the owner can read it back
    own = await _scoped(main_session_factory, tenant_a)
    try:
        assert [r.id for r in await list_agent_runs(own, tenant_id=tenant_a)] == [run.id]
    finally:
        await own.close()

    # attack: tenant B asks for tenant A's runs, both by listing under A's tenant_id and by
    # fetching A's run id - RLS must hide the row either way.
    other = await _scoped(main_session_factory, tenant_b)
    try:
        assert await list_agent_runs(other, tenant_id=tenant_a) == []
        assert await list_agent_runs(other, tenant_id=tenant_b) == []
        try:
            leaked = await get_agent_run(other, tenant_id=tenant_b, run_id=run.id)
        except Exception:  # noqa: BLE001 - a 404 is the expected, safe outcome
            leaked = None
        assert leaked is None
    finally:
        await other.close()
