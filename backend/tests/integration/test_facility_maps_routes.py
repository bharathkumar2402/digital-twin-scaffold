"""Integration tests for POST /facilities/{id}/map (issue 2.1).

Reuses the real-Postgres/real-migrations harness from test_auth_routes.py. MinIO and
Celery/Redis are not part of this harness, so `put_raw_upload` and `celery_app.send_task`
are monkeypatched — this test is about the endpoint's own boundary checks (RBAC,
ownership, extension allowlist, size cap) and about what gets handed off downstream, not
about MinIO/Celery themselves.
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
        await conn.execute("DELETE FROM facility_map_uploads")
        await conn.execute("DELETE FROM facilities")
        await conn.execute("DELETE FROM tenants")
        await conn.close()


@pytest.fixture
async def other_tenant_facility(migrated_db: PostgresContainer) -> AsyncGenerator[uuid.UUID, None]:
    """A facility that belongs to a *different* tenant than any token used below."""
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

    # No real MinIO/Redis in this test — assert the service layer calls these with the
    # right shape rather than actually reaching either.
    import app.services.facility_map_service as facility_map_service

    put_calls: list[tuple[str, bytes]] = []
    send_task_calls: list[tuple[str, dict, str]] = []
    monkeypatch.setattr(
        facility_map_service,
        "put_raw_upload",
        lambda key, content: put_calls.append((key, content)),
    )
    monkeypatch.setattr(
        facility_map_service.celery_app,
        "send_task",
        lambda name, kwargs, queue: send_task_calls.append((name, kwargs, queue)),
    )

    engine = create_async_engine(_dsn(migrated_db, driver="postgresql+asyncpg"), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_session() -> AsyncGenerator:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _get_session
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            ac.put_calls = put_calls  # type: ignore[attr-defined]
            ac.send_task_calls = send_task_calls  # type: ignore[attr-defined]
            yield ac
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


def _access_token(*, tenant_id: uuid.UUID, role: str) -> str:
    from app.core.security import create_access_token

    return create_access_token(user_id=uuid.uuid4(), tenant_id=tenant_id, role=role)


async def test_viewer_cannot_upload_a_map(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    token = _access_token(tenant_id=tenant_id, role="viewer")

    resp = await client.post(
        f"/facilities/{facility_id}/map",
        files={"file": ("plan.svg", b"<svg></svg>", "image/svg+xml")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403


async def test_tenant_admin_cannot_upload_to_another_tenants_facility(
    client: httpx.AsyncClient,
    tenant_and_facility: tuple[uuid.UUID, uuid.UUID],
    other_tenant_facility: uuid.UUID,
) -> None:
    tenant_id, _facility_id = tenant_and_facility
    token = _access_token(tenant_id=tenant_id, role="tenant_admin")

    resp = await client.post(
        f"/facilities/{other_tenant_facility}/map",
        files={"file": ("plan.svg", b"<svg></svg>", "image/svg+xml")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404
    assert client.send_task_calls == []  # type: ignore[attr-defined]


async def test_upload_to_nonexistent_facility_404s(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, _facility_id = tenant_and_facility
    token = _access_token(tenant_id=tenant_id, role="tenant_admin")

    resp = await client.post(
        f"/facilities/{uuid.uuid4()}/map",
        files={"file": ("plan.svg", b"<svg></svg>", "image/svg+xml")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404


async def test_disallowed_extension_rejected(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    token = _access_token(tenant_id=tenant_id, role="tenant_admin")

    resp = await client.post(
        f"/facilities/{facility_id}/map",
        files={"file": ("plan.exe", b"MZ...", "application/octet-stream")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 400
    assert client.put_calls == []  # type: ignore[attr-defined]
    assert client.send_task_calls == []  # type: ignore[attr-defined]


async def test_empty_file_rejected(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    token = _access_token(tenant_id=tenant_id, role="tenant_admin")

    resp = await client.post(
        f"/facilities/{facility_id}/map",
        files={"file": ("plan.svg", b"", "image/svg+xml")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 400


async def test_oversized_file_rejected(
    client: httpx.AsyncClient,
    tenant_and_facility: tuple[uuid.UUID, uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "max_map_upload_bytes", 10)
    tenant_id, facility_id = tenant_and_facility
    token = _access_token(tenant_id=tenant_id, role="tenant_admin")

    resp = await client.post(
        f"/facilities/{facility_id}/map",
        files={"file": ("plan.svg", b"<svg>" * 10, "image/svg+xml")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 413
    assert client.put_calls == []  # type: ignore[attr-defined]


async def test_valid_upload_is_stored_and_handed_off_to_sandbox_queue(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    token = _access_token(tenant_id=tenant_id, role="tenant_admin")

    resp = await client.post(
        f"/facilities/{facility_id}/map",
        files={"file": ("plan.svg", b"<svg><rect/></svg>", "image/svg+xml")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["facility_id"] == str(facility_id)
    assert body["format"] == "svg"
    assert body["status"] == "pending"

    put_calls = client.put_calls  # type: ignore[attr-defined]
    assert len(put_calls) == 1
    storage_key, content = put_calls[0]
    assert str(tenant_id) in storage_key
    assert str(facility_id) in storage_key
    assert content == b"<svg><rect/></svg>"

    send_task_calls = client.send_task_calls  # type: ignore[attr-defined]
    assert len(send_task_calls) == 1
    task_name, kwargs, queue = send_task_calls[0]
    assert task_name == "receive_map_upload"
    assert queue == "upload-sandbox"
    # Narrow payload only — never raw file bytes, never DB credentials.
    assert set(kwargs.keys()) == {"upload_id", "tenant_id", "storage_key"}
    assert kwargs["tenant_id"] == str(tenant_id)


# --- GET /facilities/{id}/map/{upload_id} (issue 2.4) ---


async def test_get_upload_status_before_tiling_has_no_tile_url(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    token = _access_token(tenant_id=tenant_id, role="tenant_admin")

    upload_resp = await client.post(
        f"/facilities/{facility_id}/map",
        files={"file": ("plan.svg", b"<svg><rect/></svg>", "image/svg+xml")},
        headers={"Authorization": f"Bearer {token}"},
    )
    upload_id = upload_resp.json()["id"]

    resp = await client.get(
        f"/facilities/{facility_id}/map/{upload_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["tile_prefix"] is None
    assert body["tile_url_template"] is None


async def test_get_upload_status_after_tiling_returns_tile_url_template(
    client: httpx.AsyncClient,
    tenant_and_facility: tuple[uuid.UUID, uuid.UUID],
    migrated_db: PostgresContainer,
) -> None:
    from app.core.config import settings

    tenant_id, facility_id = tenant_and_facility
    token = _access_token(tenant_id=tenant_id, role="tenant_admin")

    upload_resp = await client.post(
        f"/facilities/{facility_id}/map",
        files={"file": ("plan.svg", b"<svg><rect/></svg>", "image/svg+xml")},
        headers={"Authorization": f"Bearer {token}"},
    )
    upload_id = upload_resp.json()["id"]
    tile_prefix = f"{tenant_id}/{facility_id}/{upload_id}"

    # Simulate the callback path (app/workers/callback_tasks.py) recording a finished
    # tiling run — done via a raw admin connection since MinIO/Celery aren't part of
    # this harness (see the `client` fixture's docstring), matching how the other
    # fixtures in this file seed rows the API layer doesn't create.
    conn = await asyncpg.connect(_dsn(migrated_db, driver="postgresql"))
    try:
        await conn.execute(
            "UPDATE facility_map_uploads SET status = 'tiled', tile_prefix = $1 WHERE id = $2",
            tile_prefix,
            uuid.UUID(upload_id),
        )
    finally:
        await conn.close()

    resp = await client.get(
        f"/facilities/{facility_id}/map/{upload_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "tiled"
    assert body["tile_prefix"] == f"{tenant_id}/{facility_id}/{upload_id}"
    assert body["tile_url_template"] == settings.tile_url_template(body["tile_prefix"])
    assert "{z}/{x}/{y}.png" in body["tile_url_template"]


async def test_get_upload_status_for_another_tenant_404s(
    client: httpx.AsyncClient,
    tenant_and_facility: tuple[uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, facility_id = tenant_and_facility
    token = _access_token(tenant_id=tenant_id, role="tenant_admin")

    upload_resp = await client.post(
        f"/facilities/{facility_id}/map",
        files={"file": ("plan.svg", b"<svg><rect/></svg>", "image/svg+xml")},
        headers={"Authorization": f"Bearer {token}"},
    )
    upload_id = upload_resp.json()["id"]

    other_tenant_token = _access_token(tenant_id=uuid.uuid4(), role="tenant_admin")

    resp = await client.get(
        f"/facilities/{facility_id}/map/{upload_id}",
        headers={"Authorization": f"Bearer {other_tenant_token}"},
    )

    assert resp.status_code == 404


async def test_get_upload_status_nonexistent_upload_404s(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    tenant_id, facility_id = tenant_and_facility
    token = _access_token(tenant_id=tenant_id, role="tenant_admin")

    resp = await client.get(
        f"/facilities/{facility_id}/map/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 404


async def test_viewer_can_read_upload_status(
    client: httpx.AsyncClient, tenant_and_facility: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Unlike POST (tenant_admin/superadmin only), GET status has no role restriction —
    any authenticated member of the tenant can check on a facility's map processing,
    since it's read-only and needed by any viewer of the eventual map (task 2.5)."""
    tenant_id, facility_id = tenant_and_facility
    admin_token = _access_token(tenant_id=tenant_id, role="tenant_admin")

    upload_resp = await client.post(
        f"/facilities/{facility_id}/map",
        files={"file": ("plan.svg", b"<svg><rect/></svg>", "image/svg+xml")},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    upload_id = upload_resp.json()["id"]

    viewer_token = _access_token(tenant_id=tenant_id, role="viewer")
    resp = await client.get(
        f"/facilities/{facility_id}/map/{upload_id}",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )

    assert resp.status_code == 200, resp.text
