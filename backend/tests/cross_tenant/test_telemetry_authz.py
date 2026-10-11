"""Who may write sensor data: authorization of POST /telemetry (Phase L task L.1, decision D7).

Before L.1 any authenticated user could ingest, including a viewer, and self-registration
creates viewers. These are adversarial tests: each one tries to get a write through (or to
make a denial look like a success) and then checks the database, not just the status code.

Reuses the two-container fixtures (main Postgres + TimescaleDB, fake Redis) of
`test_telemetry_isolation.py`.
"""

# ruff: noqa: F401, F811  (pytest fixtures are imported from test_telemetry_isolation)
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest
from jose import jwt
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token
from app.models.user import Role
from tests.cross_tenant.test_telemetry_isolation import (
    APP_ROLE_PASSWORD,
    _dsn,
    _reading,
    _rows_visible_to_tenant,
    client,
    fake_redis_server,
    main_pg_container,
    migrated_main_db,
    migrated_timescale_db,
    tenant_a,
    tenant_b,
    timescale_pg_container,
)

ALLOWED = ["technician", "facility_manager", "tenant_admin", "superadmin"]


def _token(tenant_id: uuid.UUID, role: str) -> str:
    return create_access_token(user_id=uuid.uuid4(), tenant_id=tenant_id, role=role)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _post(
    client: httpx.AsyncClient, token: str | None, **reading_overrides
) -> httpx.Response:
    headers = _auth(token) if token is not None else {}
    return await client.post(
        "/telemetry", json={"readings": [_reading(**reading_overrides)]}, headers=headers
    )


@pytest.mark.parametrize("role", ALLOWED)
async def test_roles_allowed_to_ingest_get_201(
    client: httpx.AsyncClient, tenant_a: uuid.UUID, role: str
) -> None:
    resp = await _post(client, _token(tenant_a, role))

    assert resp.status_code == 201, resp.text
    assert resp.json()["accepted"] == 1


async def test_viewer_gets_403_and_nothing_is_written(
    client: httpx.AsyncClient, migrated_timescale_db: PostgresContainer, tenant_a: uuid.UUID
) -> None:
    # A unique sensor type, so a leaked row cannot be confused with another test's data.
    sensor = f"viewer-probe-{uuid.uuid4()}"

    resp = await _post(client, _token(tenant_a, "viewer"), sensor_type=sensor)

    assert resp.status_code == 403
    rows = await _rows_visible_to_tenant(migrated_timescale_db, tenant_a)
    assert sensor not in {r["sensor_type"] for r in rows}, "a denied request must not insert"


@pytest.mark.parametrize("role", ["Technician", "TECHNICIAN", "", "admin", "operator", "root"])
async def test_unknown_or_oddly_cased_roles_are_denied(
    client: httpx.AsyncClient, tenant_a: uuid.UUID, role: str
) -> None:
    resp = await _post(client, _token(tenant_a, role))

    assert resp.status_code == 403  # not 201, and not a 500 from an enum lookup


async def test_no_token_is_401(client: httpx.AsyncClient) -> None:
    assert (await _post(client, None)).status_code == 401


async def test_garbage_token_is_401(client: httpx.AsyncClient) -> None:
    assert (await _post(client, "not.a.jwt")).status_code == 401


async def test_expired_technician_token_is_401(
    client: httpx.AsyncClient, tenant_a: uuid.UUID
) -> None:
    past = datetime.now(UTC) - timedelta(hours=2)
    payload = {
        "sub": str(uuid.uuid4()),
        "tenant_id": str(tenant_a),
        "role": "technician",
        "type": "access",
        "iat": past,
        "exp": past + timedelta(minutes=5),
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    assert (await _post(client, token)).status_code == 401


async def test_refresh_token_cannot_be_used_to_ingest(
    client: httpx.AsyncClient, tenant_a: uuid.UUID
) -> None:
    refresh = create_refresh_token(user_id=uuid.uuid4(), tenant_id=tenant_a, role="technician")

    assert (await _post(client, refresh)).status_code == 401


async def test_role_claim_cannot_be_forged_with_another_secret(
    client: httpx.AsyncClient, migrated_timescale_db: PostgresContainer, tenant_a: uuid.UUID
) -> None:
    """A viewer who edits the role claim to tenant_admin and re-signs with a guessed key."""
    sensor = f"forged-{uuid.uuid4()}"
    now = datetime.now(UTC)
    payload = {
        "sub": str(uuid.uuid4()),
        "tenant_id": str(tenant_a),
        "role": "tenant_admin",
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    forged = jwt.encode(payload, "a-guessed-secret", algorithm=settings.jwt_algorithm)

    resp = await _post(client, forged, sensor_type=sensor)

    assert resp.status_code == 401
    rows = await _rows_visible_to_tenant(migrated_timescale_db, tenant_a)
    assert sensor not in {r["sensor_type"] for r in rows}


async def test_unsigned_alg_none_token_is_401(
    client: httpx.AsyncClient, tenant_a: uuid.UUID
) -> None:
    import base64
    import json

    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    now = int(datetime.now(UTC).timestamp())
    token = (
        b64({"alg": "none", "typ": "JWT"})
        + "."
        + b64(
            {
                "sub": str(uuid.uuid4()),
                "tenant_id": str(tenant_a),
                "role": "tenant_admin",
                "type": "access",
                "iat": now,
                "exp": now + 300,
            }
        )
        + "."
    )

    assert (await _post(client, token)).status_code == 401


async def test_forged_tenant_id_in_the_body_is_ignored(
    client: httpx.AsyncClient,
    migrated_timescale_db: PostgresContainer,
    tenant_a: uuid.UUID,
    tenant_b: uuid.UUID,
) -> None:
    """A tenant A technician names tenant B in the body (top level and per reading)."""
    sensor = f"forged-tenant-{uuid.uuid4()}"
    body = {
        "tenant_id": str(tenant_b),
        "readings": [_reading(sensor_type=sensor, tenant_id=str(tenant_b))],
    }

    resp = await client.post("/telemetry", json=body, headers=_auth(_token(tenant_a, "technician")))

    assert resp.status_code == 201, resp.text
    rows_a = await _rows_visible_to_tenant(migrated_timescale_db, tenant_a)
    rows_b = await _rows_visible_to_tenant(migrated_timescale_db, tenant_b)
    assert sensor in {r["sensor_type"] for r in rows_a}
    assert sensor not in {r["sensor_type"] for r in rows_b}
    assert {r["tenant_id"] for r in rows_a if r["sensor_type"] == sensor} == {tenant_a}


async def test_viewer_can_still_read_telemetry(
    client: httpx.AsyncClient, tenant_a: uuid.UUID
) -> None:
    """Only writing is restricted: the chart in the UI reads with whatever role the user has."""
    asset_id = uuid.uuid4()
    await _post(client, _token(tenant_a, "technician"), asset_id=str(asset_id))

    resp = await client.get(
        f"/facilities/{uuid.uuid4()}/assets/{asset_id}/telemetry",
        headers=_auth(_token(tenant_a, "viewer")),
    )

    assert resp.status_code == 200
    assert len(resp.json()) == 1


async def test_seed_demo_creates_each_role_idempotently_and_they_can_log_in(
    client: httpx.AsyncClient,
    migrated_main_db: PostgresContainer,
    tenant_a: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import seed_demo

    engine = create_async_engine(
        _dsn(
            migrated_main_db,
            user="app_role",
            password=APP_ROLE_PASSWORD,
            driver="postgresql+asyncpg",
        )
    )
    monkeypatch.setattr(
        seed_demo, "async_session_factory", async_sessionmaker(engine, expire_on_commit=False)
    )
    try:
        first = await seed_demo.seed_extra_accounts(tenant_a)
        second = await seed_demo.seed_extra_accounts(
            tenant_a
        )  # a re-run must not fail or duplicate
    finally:
        await engine.dispose()

    assert first == second
    assert set(first.values()) == {Role.TECHNICIAN, Role.VIEWER}
    conn = await asyncpg.connect(
        _dsn(
            migrated_main_db,
            user=migrated_main_db.username,
            password=migrated_main_db.password,
            driver="postgresql",
        )
    )
    try:
        count = await conn.fetchval("SELECT count(*) FROM users WHERE tenant_id = $1", tenant_a)
    finally:
        await conn.close()
    assert count == len(seed_demo.DEMO_EXTRA_ACCOUNTS)

    # Every seeded account signs in with the role the docs promise, and only the viewer is refused.
    for email, role in seed_demo.DEMO_EXTRA_ACCOUNTS:
        login = await client.post(
            "/login",
            json={
                "tenant_id": str(tenant_a),
                "email": email,
                "password": seed_demo.DEMO_EXTRA_PASSWORD,
            },
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        assert jwt.get_unverified_claims(token)["role"] == role.value
        ingest = await _post(client, token)
        assert ingest.status_code == (403 if role is Role.VIEWER else 201), (email, ingest.text)
