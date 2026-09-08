"""Integration tests for /register, /login, /refresh against a real Postgres.

Runs the real Alembic migrations (so RLS policies are in place, matching prod)
and exercises the FastAPI app over ASGI, not the service layer directly.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _run_migrations(env: dict) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _dsn(pg: PostgresContainer, *, driver: str) -> str:
    return (
        f"{driver}://{pg.username}:{pg.password}"
        f"@{pg.get_container_host_ip()}:{pg.get_exposed_port(5432)}/{pg.dbname}"
    )


@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="module")
def migrated_db(pg_container: PostgresContainer):
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
async def tenant_id(migrated_db: PostgresContainer) -> AsyncGenerator[uuid.UUID, None]:
    conn = await asyncpg.connect(_dsn(migrated_db, driver="postgresql"))
    try:
        tid = uuid.uuid4()
        await conn.execute(
            "INSERT INTO tenants (id, name, plan_tier) VALUES ($1, $2, $3)",
            tid,
            "Test Tenant",
            "standard",
        )
        yield tid
    finally:
        await conn.execute("DELETE FROM users WHERE tenant_id = $1", tid)
        await conn.execute("DELETE FROM tenants WHERE id = $1", tid)
        await conn.close()


@pytest.fixture
async def client(
    migrated_db: PostgresContainer, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[httpx.AsyncClient, None]:
    from app.core.config import settings
    from app.core.db import get_session
    from app.main import app

    # Test client talks over plain http://, so the refresh cookie's Secure flag
    # (correctly required in real deployments) must be disabled here or no
    # browser/client would ever send it back.
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


async def test_register_returns_created_user(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> None:
    resp = await client.post(
        "/register",
        json={
            "tenant_id": str(tenant_id),
            "email": "alice@example.com",
            "password": "correct-horse-1",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "alice@example.com"
    assert body["tenant_id"] == str(tenant_id)
    assert body["role"] == "viewer"
    assert "password" not in body


async def test_register_duplicate_email_in_same_tenant_rejected(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> None:
    payload = {
        "tenant_id": str(tenant_id),
        "email": "bob@example.com",
        "password": "correct-horse-1",
    }
    first = await client.post("/register", json=payload)
    assert first.status_code == 201

    second = await client.post("/register", json=payload)
    assert second.status_code == 409


async def test_login_with_correct_credentials_issues_access_token_and_refresh_cookie(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> None:
    await client.post(
        "/register",
        json={
            "tenant_id": str(tenant_id),
            "email": "carol@example.com",
            "password": "correct-horse-1",
        },
    )

    resp = await client.post(
        "/login",
        json={
            "tenant_id": str(tenant_id),
            "email": "carol@example.com",
            "password": "correct-horse-1",
        },
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()
    assert "refresh_token" in resp.cookies
    assert "refresh_token" not in resp.json()


async def test_login_with_wrong_password_rejected(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> None:
    await client.post(
        "/register",
        json={
            "tenant_id": str(tenant_id),
            "email": "dave@example.com",
            "password": "correct-horse-1",
        },
    )

    resp = await client.post(
        "/login",
        json={
            "tenant_id": str(tenant_id),
            "email": "dave@example.com",
            "password": "wrong-password",
        },
    )
    assert resp.status_code == 401


async def test_login_with_nonexistent_user_rejected(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> None:
    resp = await client.post(
        "/login",
        json={"tenant_id": str(tenant_id), "email": "nobody@example.com", "password": "whatever12"},
    )
    assert resp.status_code == 401


async def test_refresh_without_cookie_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.post("/refresh")
    assert resp.status_code == 401


async def test_refresh_with_tampered_cookie_rejected(client: httpx.AsyncClient) -> None:
    client.cookies.set("refresh_token", "not-a-real-jwt", path="/refresh")
    resp = await client.post("/refresh")
    assert resp.status_code == 401


async def test_refresh_with_valid_cookie_issues_new_access_token(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> None:
    await client.post(
        "/register",
        json={
            "tenant_id": str(tenant_id),
            "email": "erin@example.com",
            "password": "correct-horse-1",
        },
    )
    login_resp = await client.post(
        "/login",
        json={
            "tenant_id": str(tenant_id),
            "email": "erin@example.com",
            "password": "correct-horse-1",
        },
    )
    original_access_token = login_resp.json()["access_token"]

    # JWT `iat`/`exp` claims have second-granularity, so a refresh issued in the
    # same wall-clock second as login would otherwise produce a byte-identical
    # token; sleep to cross a second boundary so the two are distinguishable.
    await asyncio.sleep(1.1)

    refresh_resp = await client.post("/refresh")
    assert refresh_resp.status_code == 200
    assert refresh_resp.json()["access_token"] != original_access_token


async def test_refresh_rejects_an_access_token_used_as_refresh_token(
    client: httpx.AsyncClient, tenant_id: uuid.UUID
) -> None:
    await client.post(
        "/register",
        json={
            "tenant_id": str(tenant_id),
            "email": "frank@example.com",
            "password": "correct-horse-1",
        },
    )
    login_resp = await client.post(
        "/login",
        json={
            "tenant_id": str(tenant_id),
            "email": "frank@example.com",
            "password": "correct-horse-1",
        },
    )
    access_token = login_resp.json()["access_token"]

    client.cookies.set("refresh_token", access_token, path="/refresh")
    resp = await client.post("/refresh")
    assert resp.status_code == 401
