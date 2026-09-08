"""Integration tests for asset dependency graph routes (issue 2.7).

Same real-Postgres/real-migrations/RLS harness as test_assets_routes.py. Beyond the
usual RBAC/cross-tenant coverage, this also exercises the graph-integrity checks that
live in asset_dependency_service.py: self-loop, duplicate edge, cross-facility asset
pairing, and cycle rejection - since those protect Phase 4's cascade simulation from a
malformed graph, not just this endpoint.
"""

import uuid
from collections.abc import AsyncGenerator

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
async def scenario(
    migrated_db: PostgresContainer,
) -> AsyncGenerator[dict, None]:
    """One tenant, one facility, three assets (pump/tank/valve) plus a second facility
    (same tenant) with its own asset, used for the cross-facility-pairing check."""
    conn = await asyncpg.connect(_dsn(migrated_db, driver="postgresql"))
    try:
        tenant_id = uuid.uuid4()
        facility_id = uuid.uuid4()
        other_facility_id = uuid.uuid4()
        pump_id, tank_id, valve_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        other_facility_asset_id = uuid.uuid4()

        await conn.execute(
            "INSERT INTO tenants (id, name, plan_tier) VALUES ($1, $2, $3)",
            tenant_id,
            "Test Tenant",
            "standard",
        )
        await conn.execute(
            "INSERT INTO facilities (id, tenant_id, name) VALUES ($1, $2, $3), ($4, $2, $5)",
            facility_id,
            tenant_id,
            "Plant 1",
            other_facility_id,
            "Plant 2",
        )
        await conn.execute(
            "INSERT INTO assets (id, tenant_id, facility_id, name, type, x, y, status) VALUES "
            "($1, $2, $3, 'Pump 7', 'pump', 0, 0, 'operational'), "
            "($4, $2, $3, 'Tank 1', 'tank', 1, 1, 'operational'), "
            "($5, $2, $3, 'Valve 3', 'valve', 2, 2, 'operational'), "
            "($6, $2, $7, 'Other Facility Pump', 'pump', 0, 0, 'operational')",
            pump_id,
            tenant_id,
            facility_id,
            tank_id,
            valve_id,
            other_facility_asset_id,
            other_facility_id,
        )
        yield {
            "tenant_id": tenant_id,
            "facility_id": facility_id,
            "other_facility_id": other_facility_id,
            "pump_id": pump_id,
            "tank_id": tank_id,
            "valve_id": valve_id,
            "other_facility_asset_id": other_facility_asset_id,
        }
    finally:
        await conn.execute("DELETE FROM asset_dependencies")
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

    engine = create_async_engine(_dsn(migrated_db, driver="postgresql+asyncpg"), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_session() -> AsyncGenerator:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _get_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


def _access_token(*, tenant_id: uuid.UUID, role: str) -> str:
    from app.core.security import create_access_token

    return create_access_token(user_id=uuid.uuid4(), tenant_id=tenant_id, role=role)


def _headers(*, tenant_id: uuid.UUID, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_access_token(tenant_id=tenant_id, role=role)}"}


async def _create_edge(
    client: httpx.AsyncClient,
    *,
    facility_id: uuid.UUID,
    tenant_id: uuid.UUID,
    parent_asset_id: uuid.UUID,
    child_asset_id: uuid.UUID,
    role: str = "tenant_admin",
) -> httpx.Response:
    return await client.post(
        f"/facilities/{facility_id}/asset-dependencies",
        json={"parent_asset_id": str(parent_asset_id), "child_asset_id": str(child_asset_id)},
        headers=_headers(tenant_id=tenant_id, role=role),
    )


# --- create / RBAC ---


async def test_tenant_admin_can_create_dependency(
    client: httpx.AsyncClient, scenario: dict
) -> None:
    resp = await _create_edge(
        client,
        facility_id=scenario["facility_id"],
        tenant_id=scenario["tenant_id"],
        parent_asset_id=scenario["pump_id"],
        child_asset_id=scenario["tank_id"],
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["parent_asset_id"] == str(scenario["pump_id"])
    assert body["child_asset_id"] == str(scenario["tank_id"])


async def test_viewer_cannot_create_dependency(client: httpx.AsyncClient, scenario: dict) -> None:
    resp = await _create_edge(
        client,
        facility_id=scenario["facility_id"],
        tenant_id=scenario["tenant_id"],
        parent_asset_id=scenario["pump_id"],
        child_asset_id=scenario["tank_id"],
        role="viewer",
    )
    assert resp.status_code == 403


async def test_technician_cannot_create_dependency(
    client: httpx.AsyncClient, scenario: dict
) -> None:
    resp = await _create_edge(
        client,
        facility_id=scenario["facility_id"],
        tenant_id=scenario["tenant_id"],
        parent_asset_id=scenario["pump_id"],
        child_asset_id=scenario["tank_id"],
        role="technician",
    )
    assert resp.status_code == 403


# --- adversarial: graph-integrity checks ---


async def test_asset_cannot_depend_on_itself(client: httpx.AsyncClient, scenario: dict) -> None:
    resp = await _create_edge(
        client,
        facility_id=scenario["facility_id"],
        tenant_id=scenario["tenant_id"],
        parent_asset_id=scenario["pump_id"],
        child_asset_id=scenario["pump_id"],
    )
    assert resp.status_code == 400


async def test_duplicate_edge_rejected(client: httpx.AsyncClient, scenario: dict) -> None:
    first = await _create_edge(
        client,
        facility_id=scenario["facility_id"],
        tenant_id=scenario["tenant_id"],
        parent_asset_id=scenario["pump_id"],
        child_asset_id=scenario["tank_id"],
    )
    assert first.status_code == 201, first.text

    dup = await _create_edge(
        client,
        facility_id=scenario["facility_id"],
        tenant_id=scenario["tenant_id"],
        parent_asset_id=scenario["pump_id"],
        child_asset_id=scenario["tank_id"],
    )
    assert dup.status_code == 409


async def test_cycle_rejected(client: httpx.AsyncClient, scenario: dict) -> None:
    """pump -> tank -> valve exists; valve -> pump would close the loop."""
    tenant_id, facility_id = scenario["tenant_id"], scenario["facility_id"]
    pump_id, tank_id, valve_id = scenario["pump_id"], scenario["tank_id"], scenario["valve_id"]

    first = await _create_edge(
        client,
        facility_id=facility_id,
        tenant_id=tenant_id,
        parent_asset_id=pump_id,
        child_asset_id=tank_id,
    )
    assert first.status_code == 201, first.text
    second = await _create_edge(
        client,
        facility_id=facility_id,
        tenant_id=tenant_id,
        parent_asset_id=tank_id,
        child_asset_id=valve_id,
    )
    assert second.status_code == 201, second.text

    cycle = await _create_edge(
        client,
        facility_id=facility_id,
        tenant_id=tenant_id,
        parent_asset_id=valve_id,
        child_asset_id=pump_id,
    )
    assert cycle.status_code == 409


async def test_cross_facility_pairing_rejected(client: httpx.AsyncClient, scenario: dict) -> None:
    """parent in facility 1, child belongs to facility 2 (same tenant) - the child
    lookup must scope by facility_id, not just tenant_id, or this would silently link
    assets across facilities."""
    resp = await _create_edge(
        client,
        facility_id=scenario["facility_id"],
        tenant_id=scenario["tenant_id"],
        parent_asset_id=scenario["pump_id"],
        child_asset_id=scenario["other_facility_asset_id"],
    )
    assert resp.status_code == 404


async def test_cannot_create_dependency_in_another_tenants_facility(
    client: httpx.AsyncClient, scenario: dict
) -> None:
    other_tenant_id = uuid.uuid4()
    resp = await _create_edge(
        client,
        facility_id=scenario["facility_id"],
        tenant_id=other_tenant_id,
        parent_asset_id=scenario["pump_id"],
        child_asset_id=scenario["tank_id"],
    )
    assert resp.status_code == 404


# --- list ---


async def test_viewer_can_list_dependencies(client: httpx.AsyncClient, scenario: dict) -> None:
    await _create_edge(
        client,
        facility_id=scenario["facility_id"],
        tenant_id=scenario["tenant_id"],
        parent_asset_id=scenario["pump_id"],
        child_asset_id=scenario["tank_id"],
    )
    resp = await client.get(
        f"/facilities/{scenario['facility_id']}/asset-dependencies",
        headers=_headers(tenant_id=scenario["tenant_id"], role="viewer"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["parent_asset_id"] == str(scenario["pump_id"])


# --- delete ---


async def test_tenant_admin_can_delete_dependency(
    client: httpx.AsyncClient, scenario: dict
) -> None:
    create_resp = await _create_edge(
        client,
        facility_id=scenario["facility_id"],
        tenant_id=scenario["tenant_id"],
        parent_asset_id=scenario["pump_id"],
        child_asset_id=scenario["tank_id"],
    )
    dependency_id = create_resp.json()["id"]

    resp = await client.delete(
        f"/facilities/{scenario['facility_id']}/asset-dependencies/{dependency_id}",
        headers=_headers(tenant_id=scenario["tenant_id"], role="tenant_admin"),
    )
    assert resp.status_code == 204

    list_resp = await client.get(
        f"/facilities/{scenario['facility_id']}/asset-dependencies",
        headers=_headers(tenant_id=scenario["tenant_id"], role="tenant_admin"),
    )
    assert list_resp.json() == []


async def test_viewer_cannot_delete_dependency(client: httpx.AsyncClient, scenario: dict) -> None:
    create_resp = await _create_edge(
        client,
        facility_id=scenario["facility_id"],
        tenant_id=scenario["tenant_id"],
        parent_asset_id=scenario["pump_id"],
        child_asset_id=scenario["tank_id"],
    )
    dependency_id = create_resp.json()["id"]

    resp = await client.delete(
        f"/facilities/{scenario['facility_id']}/asset-dependencies/{dependency_id}",
        headers=_headers(tenant_id=scenario["tenant_id"], role="viewer"),
    )
    assert resp.status_code == 403


async def test_cannot_delete_another_tenants_dependency(
    client: httpx.AsyncClient, scenario: dict
) -> None:
    """Adversarial: RLS's USING clause must cover DELETE - a forged tenant_id token
    pointed at a real cross-tenant dependency id must 404, and the row must survive."""
    create_resp = await _create_edge(
        client,
        facility_id=scenario["facility_id"],
        tenant_id=scenario["tenant_id"],
        parent_asset_id=scenario["pump_id"],
        child_asset_id=scenario["tank_id"],
    )
    dependency_id = create_resp.json()["id"]

    other_tenant_id = uuid.uuid4()
    resp = await client.delete(
        f"/facilities/{scenario['facility_id']}/asset-dependencies/{dependency_id}",
        headers=_headers(tenant_id=other_tenant_id, role="tenant_admin"),
    )
    assert resp.status_code == 404

    list_resp = await client.get(
        f"/facilities/{scenario['facility_id']}/asset-dependencies",
        headers=_headers(tenant_id=scenario["tenant_id"], role="tenant_admin"),
    )
    assert len(list_resp.json()) == 1
