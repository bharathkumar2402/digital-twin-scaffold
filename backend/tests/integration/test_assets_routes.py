"""Integration tests for asset CRUD routes (issue 2.6).

Reuses the real-Postgres/real-migrations harness from test_auth_routes.py, same
pattern as test_facility_maps_routes.py. No MinIO/Celery/sandbox involved here —
assets are plain CRUD, so this exercises the endpoint's RBAC/ownership boundaries
and the real DB round-trip (including RLS, since this connects as `app_role` via
the app's normal session factory, not an admin/BYPASSRLS connection).
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
async def tenant_and_facility(
    migrated_db: PostgresContainer,
) -> AsyncGenerator[tuple[uuid.UUID, uuid.UUID], None]:
    conn = await asyncpg.connect(_dsn(migrated_db, driver="postgresql"))
    try:
        tenant_id = uuid.uuid4()
        facility_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO tenants (id, name, plan_tier) VALUES ($1, $2, $3)",
            tenant_id,
            "Test Tenant",
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
        await conn.execute("DELETE FROM assets")
        await conn.execute("DELETE FROM facilities")
        await conn.execute("DELETE FROM tenants")
        await conn.close()


@pytest.fixture
async def other_tenant_facility(migrated_db: PostgresContainer) -> AsyncGenerator[uuid.UUID, None]:
    """A facility (and asset) that belong to a *different* tenant than any token used below."""
    conn = await asyncpg.connect(_dsn(migrated_db, driver="postgresql"))
    try:
        tenant_id = uuid.uuid4()
        facility_id = uuid.uuid4()
        await conn.execute(
            "INSERT INTO tenants (id, name, plan_tier) VALUES ($1, $2, $3)",
            tenant_id,
            "Someone Else's Tenant",
            "standard",
        )
        await conn.execute(
            "INSERT INTO facilities (id, tenant_id, name) VALUES ($1, $2, $3)",
            facility_id,
            tenant_id,
            "Someone Else's Plant",
        )
        yield facility_id
    finally:
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


async def _create_asset(
    client: httpx.AsyncClient,
    *,
    facility_id: uuid.UUID,
    tenant_id: uuid.UUID,
    role: str = "tenant_admin",
) -> httpx.Response:
    return await client.post(
        f"/facilities/{facility_id}/assets",
        json={"name": "Pump 7", "type": "pump", "x": 12.5, "y": 40.0},
        headers=_headers(tenant_id=tenant_id, role=role),
    )


# --- create ---


async def test_tenant_admin_can_create_asset(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    resp = await _create_asset(client, facility_id=facility_id, tenant_id=tenant_id)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Pump 7"
    assert body["type"] == "pump"
    assert body["x"] == 12.5
    assert body["y"] == 40.0
    assert body["status"] == "operational"
    assert body["facility_id"] == str(facility_id)


async def test_facility_manager_can_create_asset(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    resp = await _create_asset(
        client, facility_id=facility_id, tenant_id=tenant_id, role="facility_manager"
    )
    assert resp.status_code == 201, resp.text


async def test_viewer_cannot_create_asset(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    resp = await _create_asset(client, facility_id=facility_id, tenant_id=tenant_id, role="viewer")
    assert resp.status_code == 403


async def test_technician_cannot_create_asset(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    resp = await _create_asset(
        client, facility_id=facility_id, tenant_id=tenant_id, role="technician"
    )
    assert resp.status_code == 403


async def test_cannot_create_asset_in_another_tenants_facility(
    client: httpx.AsyncClient,
    tenant_and_facility: tuple[uuid.UUID, uuid.UUID],
    other_tenant_facility: uuid.UUID,
) -> None:
    """Adversarial: an admin token for tenant A, aimed at tenant B's facility_id. RLS
    (via the tenant-scoped session) plus the explicit tenant_id filter in
    asset_service._get_facility_or_404 must both independently prevent this, not just
    404 on a coincidence."""
    tenant_id, _facility_id = tenant_and_facility
    resp = await _create_asset(client, facility_id=other_tenant_facility, tenant_id=tenant_id)
    assert resp.status_code == 404


async def test_create_asset_in_nonexistent_facility_404s(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, _facility_id = tenant_and_facility
    resp = await _create_asset(client, facility_id=uuid.uuid4(), tenant_id=tenant_id)
    assert resp.status_code == 404


async def test_create_asset_rejects_invalid_body(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    resp = await client.post(
        f"/facilities/{facility_id}/assets",
        json={"name": "", "type": "pump", "x": 1.0, "y": 1.0},
        headers=_headers(tenant_id=tenant_id, role="tenant_admin"),
    )
    assert resp.status_code == 422


# --- list / get ---


async def test_list_assets_returns_only_this_facilitys_assets(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    await _create_asset(client, facility_id=facility_id, tenant_id=tenant_id)

    resp = await client.get(
        f"/facilities/{facility_id}/assets", headers=_headers(tenant_id=tenant_id, role="viewer")
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "Pump 7"


async def test_viewer_can_list_and_get_assets(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    create_resp = await _create_asset(client, facility_id=facility_id, tenant_id=tenant_id)
    asset_id = create_resp.json()["id"]

    resp = await client.get(
        f"/facilities/{facility_id}/assets/{asset_id}",
        headers=_headers(tenant_id=tenant_id, role="viewer"),
    )
    assert resp.status_code == 200, resp.text


async def test_get_asset_from_another_tenant_404s(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    create_resp = await _create_asset(client, facility_id=facility_id, tenant_id=tenant_id)
    asset_id = create_resp.json()["id"]

    other_tenant_id = uuid.uuid4()
    resp = await client.get(
        f"/facilities/{facility_id}/assets/{asset_id}",
        headers=_headers(tenant_id=other_tenant_id, role="tenant_admin"),
    )
    assert resp.status_code == 404


# --- update (incl. drag-and-drop placement) ---


async def test_tenant_admin_can_update_asset_coordinates(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    create_resp = await _create_asset(client, facility_id=facility_id, tenant_id=tenant_id)
    asset_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/facilities/{facility_id}/assets/{asset_id}",
        json={"x": 99.0, "y": 5.0},
        headers=_headers(tenant_id=tenant_id, role="tenant_admin"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["x"] == 99.0
    assert body["y"] == 5.0
    assert body["name"] == "Pump 7"  # untouched fields survive a partial update


async def test_viewer_cannot_update_asset(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    create_resp = await _create_asset(client, facility_id=facility_id, tenant_id=tenant_id)
    asset_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/facilities/{facility_id}/assets/{asset_id}",
        json={"x": 1.0, "y": 1.0},
        headers=_headers(tenant_id=tenant_id, role="viewer"),
    )
    assert resp.status_code == 403


async def test_cannot_update_another_tenants_asset(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Adversarial: a valid tenant_admin token for tenant B, targeting tenant A's real
    asset/facility ids directly (not guessing — the ids are known here). RLS on the
    tenant-scoped session must make the row invisible regardless."""
    tenant_id, facility_id = tenant_and_facility
    create_resp = await _create_asset(client, facility_id=facility_id, tenant_id=tenant_id)
    asset_id = create_resp.json()["id"]

    other_tenant_id = uuid.uuid4()
    resp = await client.patch(
        f"/facilities/{facility_id}/assets/{asset_id}",
        json={"x": 1.0, "y": 1.0},
        headers=_headers(tenant_id=other_tenant_id, role="tenant_admin"),
    )
    assert resp.status_code == 404


# --- delete ---


async def test_tenant_admin_can_delete_asset(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    create_resp = await _create_asset(client, facility_id=facility_id, tenant_id=tenant_id)
    asset_id = create_resp.json()["id"]

    resp = await client.delete(
        f"/facilities/{facility_id}/assets/{asset_id}",
        headers=_headers(tenant_id=tenant_id, role="tenant_admin"),
    )
    assert resp.status_code == 204

    get_resp = await client.get(
        f"/facilities/{facility_id}/assets/{asset_id}",
        headers=_headers(tenant_id=tenant_id, role="tenant_admin"),
    )
    assert get_resp.status_code == 404


async def test_viewer_cannot_delete_asset(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    create_resp = await _create_asset(client, facility_id=facility_id, tenant_id=tenant_id)
    asset_id = create_resp.json()["id"]

    resp = await client.delete(
        f"/facilities/{facility_id}/assets/{asset_id}",
        headers=_headers(tenant_id=tenant_id, role="viewer"),
    )
    assert resp.status_code == 403


async def test_cannot_delete_another_tenants_asset(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Adversarial: RLS's USING clause must cover DELETE too — a forged tenant_id
    token pointed at a real cross-tenant asset id must 404, and the row must survive."""
    tenant_id, facility_id = tenant_and_facility
    create_resp = await _create_asset(client, facility_id=facility_id, tenant_id=tenant_id)
    asset_id = create_resp.json()["id"]

    other_tenant_id = uuid.uuid4()
    resp = await client.delete(
        f"/facilities/{facility_id}/assets/{asset_id}",
        headers=_headers(tenant_id=other_tenant_id, role="tenant_admin"),
    )
    assert resp.status_code == 404

    still_there = await client.get(
        f"/facilities/{facility_id}/assets/{asset_id}",
        headers=_headers(tenant_id=tenant_id, role="tenant_admin"),
    )
    assert still_there.status_code == 200
