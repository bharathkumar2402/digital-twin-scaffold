"""Integration tests for the risk-scores routes (issue 3.3).

Reuses the real-Postgres/real-migrations harness from test_auth_routes.py, same as
test_facility_maps_routes.py. Celery/Redis aren't part of this harness, so
`compute_facility_risk_scores.delay` is monkeypatched - this test is about the
endpoint's own boundary checks (RBAC on the compute trigger, read scoping on the
list route), not about Celery/Redis themselves.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import asyncpg
import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.test_auth_routes import _dsn, _run_migrations


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="module")
def migrated_db(pg_container: PostgresContainer):
    import os

    env = os.environ.copy()
    env.update(
        POSTGRES_HOST=pg_container.get_container_host_ip(),
        POSTGRES_PORT=str(pg_container.get_exposed_port(5432)),
        POSTGRES_DB=pg_container.dbname,
        POSTGRES_USER=pg_container.username,
        POSTGRES_PASSWORD=pg_container.password,
    )
    _run_migrations(env)
    return pg_container


@pytest.fixture
async def tenant_and_facility(
    migrated_db: PostgresContainer,
) -> AsyncGenerator[tuple[uuid.UUID, uuid.UUID], None]:
    conn = await asyncpg.connect(_dsn(migrated_db, driver="postgresql"))
    try:
        tenant_id = uuid.uuid4()
        other_tenant_id = uuid.uuid4()
        facility_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO tenants (id, name, plan_tier) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Test Tenant",
            "standard",
            other_tenant_id,
            "Other Tenant",
            "standard",
        )
        await conn.execute(
            "INSERT INTO facilities (id, tenant_id, name) VALUES ($1, $2, $3)",
            facility_id,
            tenant_id,
            "Plant 1",
        )
        yield tenant_id, facility_id
    finally:
        await conn.execute("DELETE FROM risk_scores")
        await conn.execute("DELETE FROM assets")
        await conn.execute("DELETE FROM facilities")
        await conn.execute("DELETE FROM tenants")
        await conn.close()


@pytest.fixture
async def client(
    migrated_db: PostgresContainer, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[httpx.AsyncClient, None]:
    from app.core.config import settings
    from app.core.db import get_session
    from app.main import app

    monkeypatch.setattr(settings, "cookie_secure", False)

    import app.api.risk_scores as risk_scores_api

    delay_calls: list[tuple[str, str]] = []

    class _FakeAsyncResult:
        id = "fake-task-id"

    def _fake_delay(tenant_id: str, facility_id: str) -> _FakeAsyncResult:
        delay_calls.append((tenant_id, facility_id))
        return _FakeAsyncResult()

    monkeypatch.setattr(risk_scores_api.compute_facility_risk_scores, "delay", _fake_delay)

    engine = create_async_engine(_dsn(migrated_db, driver="postgresql+asyncpg"), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_session() -> AsyncGenerator:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _get_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            ac.delay_calls = delay_calls  # type: ignore[attr-defined]
            yield ac
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


def _token(*, tenant_id: uuid.UUID, role: str = "tenant_admin") -> str:
    from app.core.security import create_access_token

    return create_access_token(user_id=uuid.uuid4(), tenant_id=tenant_id, role=role)


async def test_compute_requires_authorization(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    _tenant_id, facility_id = tenant_and_facility
    resp = await client.post(f"/facilities/{facility_id}/risk-scores/compute")
    assert resp.status_code == 401


async def test_compute_rejects_viewer_role(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    token = _token(tenant_id=tenant_id, role="viewer")
    resp = await client.post(
        f"/facilities/{facility_id}/risk-scores/compute",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert client.delay_calls == []  # type: ignore[attr-defined]


async def test_compute_enqueues_task_for_tenant_admin(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    token = _token(tenant_id=tenant_id, role="tenant_admin")
    resp = await client.post(
        f"/facilities/{facility_id}/risk-scores/compute",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["task_id"] == "fake-task-id"
    assert client.delay_calls == [(str(tenant_id), str(facility_id))]  # type: ignore[attr-defined]


async def test_list_requires_authorization(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    _tenant_id, facility_id = tenant_and_facility
    resp = await client.get(f"/facilities/{facility_id}/risk-scores")
    assert resp.status_code == 401


async def test_list_open_to_viewer_role(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    token = _token(tenant_id=tenant_id, role="viewer")
    resp = await client.get(
        f"/facilities/{facility_id}/risk-scores", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    assert resp.json() == []


async def test_list_returns_scores_for_own_facility(
    client: httpx.AsyncClient,
    tenant_and_facility: tuple[uuid.UUID, uuid.UUID],
    migrated_db: PostgresContainer,
) -> None:
    tenant_id, facility_id = tenant_and_facility
    conn = await asyncpg.connect(_dsn(migrated_db, driver="postgresql"))
    try:
        asset_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO assets (id, tenant_id, facility_id, name, type, x, y, status) "
            "VALUES ($1, $2, $3, 'Pump 1', 'pump', 1.0, 1.0, 'operational')",
            asset_id,
            tenant_id,
            facility_id,
        )
        await conn.execute(
            "INSERT INTO risk_scores "
            "(tenant_id, facility_id, asset_id, score, model_version, factors_json, computed_at) "
            "VALUES ($1, $2, $3, $4, 'v1', '{}', $5)",
            tenant_id,
            facility_id,
            asset_id,
            77.0,
            datetime.now(UTC),
        )
    finally:
        await conn.close()

    token = _token(tenant_id=tenant_id, role="technician")
    resp = await client.get(
        f"/facilities/{facility_id}/risk-scores", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["score"] == 77.0
    assert body[0]["model_version"] == "v1"


async def test_compute_for_another_tenants_facility_does_not_leak_ownership_check(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """The compute route itself doesn't check facility ownership (it just enqueues) -
    that check lives in the Celery task via `risk_inference_service.score_facility`'s
    facility lookup, scoped to the caller's own tenant_id from the JWT. Confirms the
    route still enqueues (202) using the *caller's* tenant_id, not a client-supplied
    one - the isolation guarantee lives downstream, not bypassed here."""
    tenant_id, _facility_id = tenant_and_facility
    other_facility_id = uuid.uuid4()  # belongs to nobody the caller's tenant owns
    token = _token(tenant_id=tenant_id, role="tenant_admin")
    resp = await client.post(
        f"/facilities/{other_facility_id}/risk-scores/compute",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 202
    assert client.delay_calls == [(str(tenant_id), str(other_facility_id))]  # type: ignore[attr-defined]
