"""Integration tests for GET /facilities/{id}/risk-history (UI task U7).

Real Postgres + real migrations, same harness as test_risk_scores_routes.py. NOTE: this harness
connects as the DB superuser, so these tests exercise the route's own boundary (JWT tenant, facility
ownership check, explicit tenant filter, bounds, window logic). Proof that Postgres RLS alone also
isolates the history query lives in tests/cross_tenant/test_risk_scores_rls.py.
"""

import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.test_auth_routes import _dsn, _run_migrations

T0 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)


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


class World:
    """Two tenants; tenant A has two facilities; one asset per facility plus a second in A1."""

    def __init__(self) -> None:
        self.tenant_a = uuid.uuid4()
        self.tenant_b = uuid.uuid4()
        self.fac_a1 = uuid.uuid4()
        self.fac_a2 = uuid.uuid4()
        self.fac_b = uuid.uuid4()
        self.asset_x = uuid.uuid4()  # A1, many scores
        self.asset_y = uuid.uuid4()  # A1, few scores
        self.asset_z = uuid.uuid4()  # A2
        self.asset_b = uuid.uuid4()  # tenant B


@pytest.fixture
async def world(migrated_db: PostgresContainer) -> AsyncGenerator[World, None]:
    w = World()
    conn = await asyncpg.connect(_dsn(migrated_db, driver="postgresql"))
    try:
        await conn.execute(
            "INSERT INTO tenants (id, name, plan_tier) "
            "VALUES ($1,'A','standard'),($2,'B','standard')",
            w.tenant_a,
            w.tenant_b,
        )
        await conn.execute(
            "INSERT INTO facilities (id, tenant_id, name) "
            "VALUES ($1,$2,'A1'),($3,$2,'A2'),($4,$5,'B1')",
            w.fac_a1,
            w.tenant_a,
            w.fac_a2,
            w.fac_b,
            w.tenant_b,
        )
        for asset_id, tenant, facility, name in [
            (w.asset_x, w.tenant_a, w.fac_a1, "X"),
            (w.asset_y, w.tenant_a, w.fac_a1, "Y"),
            (w.asset_z, w.tenant_a, w.fac_a2, "Z"),
            (w.asset_b, w.tenant_b, w.fac_b, "B"),
        ]:
            await conn.execute(
                "INSERT INTO assets (id, tenant_id, facility_id, name, type, x, y, status) "
                "VALUES ($1,$2,$3,$4,'pump',1,1,'operational')",
                asset_id,
                tenant,
                facility,
                name,
            )
        yield w
    finally:
        await conn.execute("DELETE FROM risk_scores")
        await conn.execute("DELETE FROM assets")
        await conn.execute("DELETE FROM facilities")
        await conn.execute("DELETE FROM tenants")
        await conn.close()


@pytest.fixture
async def client(migrated_db: PostgresContainer, monkeypatch: pytest.MonkeyPatch):
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
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


def _auth(tenant_id: uuid.UUID, role: str = "viewer") -> dict[str, str]:
    from app.core.security import create_access_token

    token = create_access_token(user_id=uuid.uuid4(), tenant_id=tenant_id, role=role)
    return {"Authorization": f"Bearer {token}"}


async def _add_scores(
    migrated_db: PostgresContainer,
    *,
    tenant: uuid.UUID,
    facility: uuid.UUID,
    asset: uuid.UUID,
    scores: list[float],
    rul: list[float | None] | None = None,
    same_time: bool = False,
) -> None:
    conn = await asyncpg.connect(_dsn(migrated_db, driver="postgresql"))
    try:
        for i, value in enumerate(scores):
            await conn.execute(
                "INSERT INTO risk_scores "
                "(tenant_id, facility_id, asset_id, score, rul_cycles, model_version, "
                "factors_json, computed_at) VALUES ($1,$2,$3,$4,$5,'secret-model-v9',"
                "'{\"secret\": 1}',$6)",
                tenant,
                facility,
                asset,
                value,
                rul[i] if rul else None,
                T0 if same_time else T0 + timedelta(hours=i),
            )
    finally:
        await conn.close()


def _by_asset(body: list[dict]) -> dict[str, list[dict]]:
    return {series["asset_id"]: series["points"] for series in body}


# ---- access control ---------------------------------------------------------------------------


async def test_requires_authorization(client: httpx.AsyncClient, world: World) -> None:
    resp = await client.get(f"/facilities/{world.fac_a1}/risk-history")
    assert resp.status_code == 401


async def test_open_to_a_viewer_like_the_latest_scores_list(
    client: httpx.AsyncClient, world: World
) -> None:
    resp = await client.get(
        f"/facilities/{world.fac_a1}/risk-history", headers=_auth(world.tenant_a)
    )
    assert resp.status_code == 200
    assert resp.json() == []


async def test_unknown_facility_is_404(client: httpx.AsyncClient, world: World) -> None:
    resp = await client.get(
        f"/facilities/{uuid.uuid4()}/risk-history", headers=_auth(world.tenant_a)
    )
    assert resp.status_code == 404


async def test_another_tenants_facility_is_404_and_leaks_nothing(
    client: httpx.AsyncClient, world: World, migrated_db: PostgresContainer
) -> None:
    await _add_scores(
        migrated_db,
        tenant=world.tenant_a,
        facility=world.fac_a1,
        asset=world.asset_x,
        scores=[91, 92],
    )
    resp = await client.get(
        f"/facilities/{world.fac_a1}/risk-history", headers=_auth(world.tenant_b)
    )
    assert resp.status_code == 404
    assert str(world.asset_x) not in resp.text
    assert "91" not in resp.text


async def test_same_tenant_other_facility_never_leaks_in(
    client: httpx.AsyncClient, world: World, migrated_db: PostgresContainer
) -> None:
    await _add_scores(
        migrated_db,
        tenant=world.tenant_a,
        facility=world.fac_a1,
        asset=world.asset_x,
        scores=[10, 20],
    )
    await _add_scores(
        migrated_db, tenant=world.tenant_a, facility=world.fac_a2, asset=world.asset_z, scores=[55]
    )
    a2 = await client.get(f"/facilities/{world.fac_a2}/risk-history", headers=_auth(world.tenant_a))
    assert set(_by_asset(a2.json())) == {str(world.asset_z)}
    a1 = await client.get(f"/facilities/{world.fac_a1}/risk-history", headers=_auth(world.tenant_a))
    assert set(_by_asset(a1.json())) == {str(world.asset_x)}


# ---- bounds -----------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["0", "-1", "51", "1000000000", "abc", "", "1.5"])
async def test_limit_abuse_is_rejected_never_unbounded(
    client: httpx.AsyncClient, world: World, bad: str
) -> None:
    resp = await client.get(
        f"/facilities/{world.fac_a1}/risk-history?limit_per_asset={bad}",
        headers=_auth(world.tenant_a),
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("ok", ["1", "50"])
async def test_limit_edges_are_accepted(client: httpx.AsyncClient, world: World, ok: str) -> None:
    resp = await client.get(
        f"/facilities/{world.fac_a1}/risk-history?limit_per_asset={ok}",
        headers=_auth(world.tenant_a),
    )
    assert resp.status_code == 200


# ---- window correctness -----------------------------------------------------------------------


async def test_returns_the_newest_n_oldest_first_and_assets_do_not_share_a_quota(
    client: httpx.AsyncClient, world: World, migrated_db: PostgresContainer
) -> None:
    await _add_scores(
        migrated_db,
        tenant=world.tenant_a,
        facility=world.fac_a1,
        asset=world.asset_x,
        scores=[float(i) for i in range(12)],  # 0..11, one per hour
    )
    await _add_scores(
        migrated_db,
        tenant=world.tenant_a,
        facility=world.fac_a1,
        asset=world.asset_y,
        scores=[70, 71, 72],
    )
    resp = await client.get(
        f"/facilities/{world.fac_a1}/risk-history?limit_per_asset=10", headers=_auth(world.tenant_a)
    )
    series = _by_asset(resp.json())
    assert [p["score"] for p in series[str(world.asset_x)]] == [float(i) for i in range(2, 12)]
    assert [p["score"] for p in series[str(world.asset_y)]] == [70, 71, 72]
    times = [p["computed_at"] for p in series[str(world.asset_x)]]
    assert times == sorted(times)


async def test_default_limit_is_ten(
    client: httpx.AsyncClient, world: World, migrated_db: PostgresContainer
) -> None:
    await _add_scores(
        migrated_db,
        tenant=world.tenant_a,
        facility=world.fac_a1,
        asset=world.asset_x,
        scores=[float(i) for i in range(12)],
    )
    resp = await client.get(
        f"/facilities/{world.fac_a1}/risk-history", headers=_auth(world.tenant_a)
    )
    assert len(_by_asset(resp.json())[str(world.asset_x)]) == 10


async def test_limit_one_returns_just_the_latest(
    client: httpx.AsyncClient, world: World, migrated_db: PostgresContainer
) -> None:
    await _add_scores(
        migrated_db,
        tenant=world.tenant_a,
        facility=world.fac_a1,
        asset=world.asset_x,
        scores=[5, 6, 7],
    )
    resp = await client.get(
        f"/facilities/{world.fac_a1}/risk-history?limit_per_asset=1", headers=_auth(world.tenant_a)
    )
    assert [p["score"] for p in _by_asset(resp.json())[str(world.asset_x)]] == [7]


async def test_equal_timestamps_give_a_stable_answer(
    client: httpx.AsyncClient, world: World, migrated_db: PostgresContainer
) -> None:
    await _add_scores(
        migrated_db,
        tenant=world.tenant_a,
        facility=world.fac_a1,
        asset=world.asset_x,
        scores=[1, 2, 3],
        same_time=True,
    )
    url = f"/facilities/{world.fac_a1}/risk-history?limit_per_asset=2"
    first = await client.get(url, headers=_auth(world.tenant_a))
    second = await client.get(url, headers=_auth(world.tenant_a))
    assert first.json() == second.json()
    assert len(_by_asset(first.json())[str(world.asset_x)]) == 2


async def test_rul_is_kept_when_present_and_null_when_absent(
    client: httpx.AsyncClient, world: World, migrated_db: PostgresContainer
) -> None:
    await _add_scores(
        migrated_db,
        tenant=world.tenant_a,
        facility=world.fac_a1,
        asset=world.asset_x,
        scores=[10, 20],
        rul=[None, 3.6],
    )
    resp = await client.get(
        f"/facilities/{world.fac_a1}/risk-history", headers=_auth(world.tenant_a)
    )
    assert [p["rul_cycles"] for p in _by_asset(resp.json())[str(world.asset_x)]] == [None, 3.6]


# ---- response shape ---------------------------------------------------------------------------


async def test_response_exposes_only_time_score_and_rul(
    client: httpx.AsyncClient, world: World, migrated_db: PostgresContainer
) -> None:
    await _add_scores(
        migrated_db, tenant=world.tenant_a, facility=world.fac_a1, asset=world.asset_x, scores=[10]
    )
    resp = await client.get(
        f"/facilities/{world.fac_a1}/risk-history", headers=_auth(world.tenant_a)
    )
    [series] = resp.json()
    assert set(series) == {"asset_id", "points"}
    assert set(series["points"][0]) == {"computed_at", "score", "rul_cycles"}
    assert "secret" not in resp.text
    assert "model_version" not in resp.text
    assert "factors_json" not in resp.text
