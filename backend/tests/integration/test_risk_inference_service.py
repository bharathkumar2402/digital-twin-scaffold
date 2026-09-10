"""Integration tests for `risk_inference_service.score_facility`/`get_latest_risk_scores`
(issue 3.3).

Same dual-container/app_role harness as test_feature_engineering.py, since scoring
pulls features from both the main DB and TimescaleDB. The trained-model boundary
itself (MinIO load/save) is already covered by 3.2's `test_train_risk_model.py`
against a real MinIO container, so here `_resolve_and_load_model` is monkeypatched
with a small deterministic fake XGBoost-shaped model - this test is about the
service's own logic (feature -> vector -> validated row -> RLS-scoped persistence),
not re-proving the MinIO round trip.
"""

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

BACKEND_DIR = Path(__file__).resolve().parents[2]
APP_ROLE_PASSWORD = "test-app-role-password-risk"
APP_TIMESCALE_ROLE_PASSWORD = "test-app-timescale-role-password-risk"


def _run_migrations(config_file: str, env: dict) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", config_file, "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _dsn(pg: PostgresContainer, *, user: str, password: str, driver: str) -> str:
    return (
        f"{driver}://{user}:{password}@{pg.get_container_host_ip()}:"
        f"{pg.get_exposed_port(5432)}/{pg.dbname}"
    )


@pytest.fixture(scope="module")
def main_pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="module")
def timescale_pg_container():
    with PostgresContainer("timescale/timescaledb:latest-pg16") as pg:
        yield pg


@pytest.fixture(scope="module")
def migrated_main_db(main_pg_container: PostgresContainer):
    env = os.environ.copy()
    env.update(
        POSTGRES_HOST=main_pg_container.get_container_host_ip(),
        POSTGRES_PORT=str(main_pg_container.get_exposed_port(5432)),
        POSTGRES_DB=main_pg_container.dbname,
        POSTGRES_USER=main_pg_container.username,
        POSTGRES_PASSWORD=main_pg_container.password,
        APP_DB_PASSWORD=APP_ROLE_PASSWORD,
    )
    _run_migrations("alembic.ini", env)
    return main_pg_container


@pytest.fixture(scope="module")
def migrated_timescale_db(timescale_pg_container: PostgresContainer):
    env = os.environ.copy()
    env.update(
        TIMESCALE_HOST=timescale_pg_container.get_container_host_ip(),
        TIMESCALE_PORT=str(timescale_pg_container.get_exposed_port(5432)),
        TIMESCALE_DB=timescale_pg_container.dbname,
        TIMESCALE_USER=timescale_pg_container.username,
        TIMESCALE_PASSWORD=timescale_pg_container.password,
        APP_TIMESCALE_PASSWORD=APP_TIMESCALE_ROLE_PASSWORD,
    )
    _run_migrations("alembic_timescale.ini", env)
    return timescale_pg_container


@pytest.fixture
async def main_session_factory(
    migrated_main_db: PostgresContainer,
) -> AsyncGenerator[async_sessionmaker, None]:
    engine = create_async_engine(
        _dsn(
            migrated_main_db,
            user="app_role",
            password=APP_ROLE_PASSWORD,
            driver="postgresql+asyncpg",
        ),
        pool_pre_ping=True,
    )
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def timescale_session_factory(
    migrated_timescale_db: PostgresContainer,
) -> AsyncGenerator[async_sessionmaker, None]:
    engine = create_async_engine(
        _dsn(
            migrated_timescale_db,
            user="app_role",
            password=APP_TIMESCALE_ROLE_PASSWORD,
            driver="postgresql+asyncpg",
        ),
        pool_pre_ping=True,
    )
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _make_tenant(migrated_main_db: PostgresContainer, name: str) -> uuid.UUID:
    conn = await asyncpg.connect(
        _dsn(
            migrated_main_db,
            user=migrated_main_db.username,
            password=migrated_main_db.password,
            driver="postgresql",
        )
    )
    try:
        tid = uuid.uuid4()
        await conn.execute(
            "INSERT INTO tenants (id, name, plan_tier) VALUES ($1, $2, $3)", tid, name, "standard"
        )
        return tid
    finally:
        await conn.close()


async def _make_facility(
    migrated_main_db: PostgresContainer, *, tenant_id: uuid.UUID, name: str
) -> uuid.UUID:
    conn = await asyncpg.connect(
        _dsn(
            migrated_main_db,
            user=migrated_main_db.username,
            password=migrated_main_db.password,
            driver="postgresql",
        )
    )
    try:
        fid = uuid.uuid4()
        await conn.execute(
            "INSERT INTO facilities (id, tenant_id, name) VALUES ($1, $2, $3)", fid, tenant_id, name
        )
        return fid
    finally:
        await conn.close()


async def _make_asset(
    migrated_main_db: PostgresContainer, *, tenant_id: uuid.UUID, facility_id: uuid.UUID, name: str
) -> uuid.UUID:
    conn = await asyncpg.connect(
        _dsn(
            migrated_main_db,
            user=migrated_main_db.username,
            password=migrated_main_db.password,
            driver="postgresql",
        )
    )
    try:
        aid = uuid.uuid4()
        await conn.execute(
            "INSERT INTO assets (id, tenant_id, facility_id, name, type, x, y, status) "
            "VALUES ($1, $2, $3, $4, 'pump', 1.0, 1.0, 'operational')",
            aid,
            tenant_id,
            facility_id,
            name,
        )
        return aid
    finally:
        await conn.close()


class _FakeModel:
    """Deterministic stand-in for the real XGBClassifier - always reports a fixed
    probability, regardless of the feature vector, so the test asserts on the
    service's plumbing (row shape, bounds, model_version, factors), not on any real
    model's learned behavior (that's 3.2's `test_train_risk_model.py`'s job)."""

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return np.tile(np.array([0.35, 0.65]), (X.shape[0], 1))


@pytest.fixture(autouse=True)
def fake_model(monkeypatch: pytest.MonkeyPatch):
    from app.ml.feature_vector import FEATURE_NAMES
    from app.services import risk_inference_service

    monkeypatch.setattr(
        risk_inference_service,
        "_resolve_and_load_model",
        lambda model_version: (_FakeModel(), list(FEATURE_NAMES), "vtest-fixed"),
    )


async def _scoped(session_factory: async_sessionmaker, tenant_id: uuid.UUID) -> AsyncSession:
    from app.core.tenant_context import scope_session_to_tenant

    session = session_factory()
    await scope_session_to_tenant(session, tenant_id)
    return session


async def test_score_facility_creates_one_row_per_asset_with_valid_bounds(
    main_pg_container: PostgresContainer,
    migrated_main_db: PostgresContainer,
    migrated_timescale_db: PostgresContainer,
    main_session_factory: async_sessionmaker,
    timescale_session_factory: async_sessionmaker,
) -> None:
    from app.models.risk_score import RiskScore
    from app.services.risk_inference_service import score_facility

    tenant_id = await _make_tenant(migrated_main_db, "Tenant A")
    facility_id = await _make_facility(migrated_main_db, tenant_id=tenant_id, name="Plant A")
    asset_1 = await _make_asset(
        migrated_main_db, tenant_id=tenant_id, facility_id=facility_id, name="Pump 1"
    )
    asset_2 = await _make_asset(
        migrated_main_db, tenant_id=tenant_id, facility_id=facility_id, name="Pump 2"
    )

    main_session = await _scoped(main_session_factory, tenant_id)
    timescale_session = await _scoped(timescale_session_factory, tenant_id)
    try:
        saved = await score_facility(
            main_session, timescale_session, tenant_id=tenant_id, facility_id=facility_id
        )
    finally:
        await main_session.close()
        await timescale_session.close()

    assert {row.asset_id for row in saved} == {asset_1, asset_2}
    for row in saved:
        assert 0.0 <= row.score <= 100.0
        assert row.score == pytest.approx(65.0)
        assert row.model_version == "vtest-fixed"
        assert "asset_age_days" in row.factors_json

    verify_session = await _scoped(main_session_factory, tenant_id)
    try:
        result = await verify_session.execute(
            select(RiskScore).where(RiskScore.facility_id == facility_id)
        )
        persisted = result.scalars().all()
        assert len(persisted) == 2
    finally:
        await verify_session.close()


async def test_score_facility_handles_asset_with_no_telemetry(
    migrated_main_db: PostgresContainer,
    migrated_timescale_db: PostgresContainer,
    main_session_factory: async_sessionmaker,
    timescale_session_factory: async_sessionmaker,
) -> None:
    """An asset with zero sensor readings (all windows empty) must still score
    without crashing - feature_vector.vectorize zero-fills missing windows."""
    from app.services.risk_inference_service import score_facility

    tenant_id = await _make_tenant(migrated_main_db, "Tenant B")
    facility_id = await _make_facility(migrated_main_db, tenant_id=tenant_id, name="Plant B")
    await _make_asset(
        migrated_main_db, tenant_id=tenant_id, facility_id=facility_id, name="Lonely Pump"
    )

    main_session = await _scoped(main_session_factory, tenant_id)
    timescale_session = await _scoped(timescale_session_factory, tenant_id)
    try:
        saved = await score_facility(
            main_session, timescale_session, tenant_id=tenant_id, facility_id=facility_id
        )
    finally:
        await main_session.close()
        await timescale_session.close()

    assert len(saved) == 1
    assert saved[0].factors_json["dependency_neighbor_count"] == 0


async def test_score_facility_for_another_tenants_facility_id_raises_404(
    migrated_main_db: PostgresContainer,
    migrated_timescale_db: PostgresContainer,
    main_session_factory: async_sessionmaker,
    timescale_session_factory: async_sessionmaker,
) -> None:
    from fastapi import HTTPException

    from app.services.risk_inference_service import score_facility

    tenant_a = await _make_tenant(migrated_main_db, "Tenant C")
    tenant_b = await _make_tenant(migrated_main_db, "Tenant D")
    facility_a = await _make_facility(migrated_main_db, tenant_id=tenant_a, name="Plant C")

    main_session = await _scoped(main_session_factory, tenant_b)
    timescale_session = await _scoped(timescale_session_factory, tenant_b)
    try:
        with pytest.raises(HTTPException) as exc_info:
            await score_facility(
                main_session, timescale_session, tenant_id=tenant_b, facility_id=facility_a
            )
        assert exc_info.value.status_code == 404
    finally:
        await main_session.close()
        await timescale_session.close()


async def test_score_facility_refuses_a_model_with_mismatched_feature_names(
    migrated_main_db: PostgresContainer,
    migrated_timescale_db: PostgresContainer,
    main_session_factory: async_sessionmaker,
    timescale_session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale or corrupt model whose `feature_names.json` no longer matches
    `app.ml.feature_vector.FEATURE_NAMES` must fail loudly (ValueError, no rows
    written), not silently score against a misaligned column layout."""
    from app.services import risk_inference_service
    from app.services.risk_inference_service import score_facility

    monkeypatch.setattr(
        risk_inference_service,
        "_resolve_and_load_model",
        lambda model_version: (_FakeModel(), ["some_other_column"], "vtest-stale"),
    )

    tenant_id = await _make_tenant(migrated_main_db, "Tenant F")
    facility_id = await _make_facility(migrated_main_db, tenant_id=tenant_id, name="Plant F")
    await _make_asset(migrated_main_db, tenant_id=tenant_id, facility_id=facility_id, name="Pump F")

    main_session = await _scoped(main_session_factory, tenant_id)
    timescale_session = await _scoped(timescale_session_factory, tenant_id)
    try:
        with pytest.raises(ValueError, match="feature_names"):
            await score_facility(
                main_session, timescale_session, tenant_id=tenant_id, facility_id=facility_id
            )
    finally:
        await main_session.close()
        await timescale_session.close()


async def test_get_latest_risk_scores_returns_only_the_newest_row_per_asset(
    migrated_main_db: PostgresContainer,
    migrated_timescale_db: PostgresContainer,
    main_session_factory: async_sessionmaker,
    timescale_session_factory: async_sessionmaker,
) -> None:
    from app.core.tenant_context import scope_session_to_tenant
    from app.services.risk_inference_service import get_latest_risk_scores, score_facility

    tenant_id = await _make_tenant(migrated_main_db, "Tenant E")
    facility_id = await _make_facility(migrated_main_db, tenant_id=tenant_id, name="Plant E")
    await _make_asset(migrated_main_db, tenant_id=tenant_id, facility_id=facility_id, name="Pump E")

    main_session = await _scoped(main_session_factory, tenant_id)
    timescale_session = await _scoped(timescale_session_factory, tenant_id)
    try:
        await score_facility(
            main_session, timescale_session, tenant_id=tenant_id, facility_id=facility_id
        )
        # Each score_facility call commits, and the RLS GUC is transaction-scoped
        # (SET LOCAL) - a real caller reusing one session across multiple calls (a
        # fresh Celery task invocation never does; it opens one session per run) must
        # re-scope after each commit, same as migration 0008's documented GUC
        # lifetime. Not re-scoping here would 404 on the second call, correctly
        # failing closed rather than crashing - see migration 0008.
        await scope_session_to_tenant(main_session, tenant_id)
        await scope_session_to_tenant(timescale_session, tenant_id)
        await score_facility(
            main_session, timescale_session, tenant_id=tenant_id, facility_id=facility_id
        )

        await scope_session_to_tenant(main_session, tenant_id)
        latest = await get_latest_risk_scores(
            main_session, tenant_id=tenant_id, facility_id=facility_id
        )
    finally:
        await main_session.close()
        await timescale_session.close()

    assert len(latest) == 1
