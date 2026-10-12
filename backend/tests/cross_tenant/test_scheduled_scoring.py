"""Scheduled scoring across tenants (Phase L task L.4), against real RLS-enforcing databases.

`score_all_facilities` is the first job that loops over every tenant. What must hold: it scores only
facilities with new readings, never twice at once, a failure in one facility or tenant never stops
the others, every facility is scored inside its OWN tenant's RLS scope, and the "scores updated"
push can only reach the tenant it belongs to. The real service runs against the real databases,
connected as the non-bypass `app_role`; only the model-scoring function is replaced by a recorder,
because model quality is not under test here.
"""

# ruff: noqa: F401, F811  (pytest fixtures are imported from test_telemetry_isolation)
import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from fakeredis import aioredis as fakeredis
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from app.core.tenant_context import scope_session_to_tenant
from app.models.risk_score import RiskScore
from app.schemas.ml.scores_updated import ScoresUpdated
from app.services import scheduled_scoring_service as svc
from app.services.alert_publish_service import alert_channel
from app.services.risk_inference_service import get_latest_risk_scores
from tests.cross_tenant.test_telemetry_freshness import _admin, _facility_with_assets, _readings
from tests.cross_tenant.test_telemetry_isolation import (
    APP_ROLE_PASSWORD,
    APP_TIMESCALE_ROLE_PASSWORD,
    _dsn,
    _make_tenant,
    fake_redis_server,
    main_pg_container,
    migrated_main_db,
    migrated_timescale_db,
    timescale_pg_container,
)


def _ago(**kw) -> datetime:
    return datetime.now(UTC) - timedelta(**kw)


# ---- fixtures -----------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def clean(migrated_main_db, migrated_timescale_db):
    """Every test starts from empty tables: the loop under test visits ALL tenants in the DB."""
    main = await _admin(migrated_main_db)
    ts = await _admin(migrated_timescale_db)
    try:
        await main.execute(
            "TRUNCATE risk_scores, asset_dependencies, assets, facilities, tenants CASCADE"
        )
        await ts.execute("TRUNCATE sensor_readings")
    finally:
        await main.close()
        await ts.close()


@pytest.fixture
async def factories(migrated_main_db, migrated_timescale_db):
    main_engine = create_async_engine(
        _dsn(
            migrated_main_db,
            user="app_role",
            password=APP_ROLE_PASSWORD,
            driver="postgresql+asyncpg",
        )
    )
    ts_engine = create_async_engine(
        _dsn(
            migrated_timescale_db,
            user="app_role",
            password=APP_TIMESCALE_ROLE_PASSWORD,
            driver="postgresql+asyncpg",
        )
    )
    try:
        yield (
            async_sessionmaker(main_engine, expire_on_commit=False),
            async_sessionmaker(ts_engine, expire_on_commit=False),
        )
    finally:
        await main_engine.dispose()
        await ts_engine.dispose()


@pytest.fixture
async def redis(fake_redis_server):
    client = fakeredis.FakeRedis(server=fake_redis_server, decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


class Recorder:
    """Stands in for `score_facility`: records who it was called for and under which RLS scope, and
    writes one risk_scores row per asset like the real one (so 'last scored' is real)."""

    def __init__(self, *, fail_for: set[uuid.UUID] | None = None, write_rows: bool = True) -> None:
        self.calls: list[uuid.UUID] = []
        self.scope_seen: dict[uuid.UUID, tuple[str, str, int]] = {}
        self.fail_for = fail_for or set()
        self.write_rows = write_rows

    async def __call__(self, main_session, timescale_session, *, tenant_id, facility_id, now):
        self.calls.append(facility_id)
        if facility_id in self.fail_for:
            raise RuntimeError("model blew up")
        guc = "SELECT current_setting('app.current_tenant_id', true)"
        main_scope = (await main_session.execute(text(guc))).scalar_one()
        ts_scope = (await timescale_session.execute(text(guc))).scalar_one()
        visible_assets = (
            await main_session.execute(text("SELECT count(*) FROM assets"))
        ).scalar_one()
        self.scope_seen[facility_id] = (main_scope, ts_scope, visible_assets)
        rows = []
        if self.write_rows:
            asset_ids = (
                await main_session.execute(
                    text("SELECT id FROM assets WHERE facility_id = :f"), {"f": facility_id}
                )
            ).scalars()
            for asset_id in asset_ids:
                row = RiskScore(
                    tenant_id=tenant_id,
                    facility_id=facility_id,
                    asset_id=asset_id,
                    score=10.0,
                    rul_cycles=None,
                    model_version="test",
                    factors_json={},
                    computed_at=now,
                )
                main_session.add(row)
                rows.append(row)
            await main_session.commit()
        return rows


async def _run(factories, redis, scorer):
    main_factory, ts_factory = factories
    return await svc.score_all_facilities(
        main_factory=main_factory, timescale_factory=ts_factory, redis=redis, score=scorer
    )


async def _tenant_with_facility(main_db, name: str, assets: int = 2):
    tenant = await _make_tenant(main_db, name)
    facility, asset_ids = await _facility_with_assets(main_db, tenant, assets)
    return tenant, facility, asset_ids


# ---- skip logic ---------------------------------------------------------------------------------


async def test_a_facility_with_no_new_readings_is_not_scored(
    factories, redis, migrated_main_db, migrated_timescale_db
) -> None:
    tenant, facility, assets = await _tenant_with_facility(migrated_main_db, "T")
    scorer = Recorder()

    no_readings = await _run(factories, redis, scorer)  # assets exist, nothing has ever reported
    assert (no_readings.scored, no_readings.skipped_no_new_data) == (0, 1)

    await _readings(migrated_timescale_db, tenant, assets[0], [("rpm", _ago(seconds=10))])
    first = await _run(factories, redis, scorer)
    second = await _run(factories, redis, scorer)  # nothing arrived since

    assert (first.scored, second.scored, second.skipped_no_new_data) == (1, 0, 1)
    assert scorer.calls == [facility]


async def test_a_reading_newer_than_the_last_score_triggers_exactly_one_more_score(
    factories, redis, migrated_main_db, migrated_timescale_db
) -> None:
    tenant, facility, assets = await _tenant_with_facility(migrated_main_db, "T")
    scorer = Recorder()
    await _readings(migrated_timescale_db, tenant, assets[0], [("rpm", _ago(seconds=30))])
    await _run(factories, redis, scorer)

    # stamped after the first pass finished, like a real reading arriving now
    await _readings(migrated_timescale_db, tenant, assets[1], [("rpm", _ago(seconds=0))])
    again = await _run(factories, redis, scorer)
    idle = await _run(factories, redis, scorer)

    assert (again.scored, idle.scored) == (1, 0)
    assert scorer.calls == [facility, facility]


async def test_readings_older_than_the_last_stored_score_do_not_trigger_scoring_without_marker(
    factories, redis, migrated_main_db, migrated_timescale_db
) -> None:
    """Redis can be flushed. The database's own newest risk score is the second reference point."""
    tenant, facility, assets = await _tenant_with_facility(migrated_main_db, "T", assets=1)
    await _readings(migrated_timescale_db, tenant, assets[0], [("rpm", _ago(minutes=5))])
    admin = await _admin(migrated_main_db)
    try:
        await admin.execute(
            "INSERT INTO risk_scores (tenant_id, facility_id, asset_id, score, model_version, "
            "factors_json, computed_at) VALUES ($1,$2,$3,5,'t','{}',$4)",
            tenant, facility, assets[0], _ago(minutes=1),
        )  # fmt: skip
    finally:
        await admin.close()
    scorer = Recorder()

    summary = await _run(factories, redis, scorer)

    assert (summary.scored, summary.skipped_no_new_data, scorer.calls) == (0, 1, [])


async def test_another_tenants_readings_under_my_asset_id_never_trigger_my_scoring(
    factories, redis, migrated_main_db, migrated_timescale_db
) -> None:
    tenant_a, facility_a, assets_a = await _tenant_with_facility(migrated_main_db, "A", assets=1)
    tenant_b = await _make_tenant(migrated_main_db, "B")
    await _readings(migrated_timescale_db, tenant_b, assets_a[0], [("rpm", _ago(seconds=1))])
    scorer = Recorder()

    summary = await _run(factories, redis, scorer)

    assert (summary.scored, scorer.calls) == (0, [])


async def test_a_facility_whose_assets_cannot_be_scored_is_not_retried_every_minute(
    factories, redis, migrated_main_db, migrated_timescale_db
) -> None:
    """No score rows get written (no coherent snapshot): only the Redis marker prevents a loop."""
    tenant, facility, assets = await _tenant_with_facility(migrated_main_db, "T", assets=1)
    await _readings(migrated_timescale_db, tenant, assets[0], [("rpm", _ago(seconds=20))])
    scorer = Recorder(write_rows=False)

    first = await _run(factories, redis, scorer)
    second = await _run(factories, redis, scorer)

    assert (first.scored, second.scored, scorer.calls) == (1, 0, [facility])


# ---- lock ---------------------------------------------------------------------------------------


async def test_a_held_lock_skips_the_facility_without_scoring_it(
    factories, redis, migrated_main_db, migrated_timescale_db
) -> None:
    tenant, facility, assets = await _tenant_with_facility(migrated_main_db, "T")
    await _readings(migrated_timescale_db, tenant, assets[0], [("rpm", _ago(seconds=5))])
    token = await svc.acquire_lock(redis, tenant_id=tenant, facility_id=facility, ttl_seconds=60)
    assert token
    scorer = Recorder()

    summary = await _run(factories, redis, scorer)

    assert (summary.skipped_busy, summary.scored, scorer.calls) == (1, 0, [])
    # ...and the other run's lock was left alone
    assert await redis.get(svc._LOCK_KEY.format(tenant_id=tenant, facility_id=facility)) == token


async def test_two_overlapping_runs_score_the_facility_once(
    factories, redis, migrated_main_db, migrated_timescale_db
) -> None:
    tenant, facility, assets = await _tenant_with_facility(migrated_main_db, "T")
    await _readings(migrated_timescale_db, tenant, assets[0], [("rpm", _ago(seconds=5))])
    entered, gate = asyncio.Event(), asyncio.Event()
    inner = Recorder()

    async def slow(*args, **kwargs):
        entered.set()
        await gate.wait()
        return await inner(*args, **kwargs)

    first_run = asyncio.create_task(_run(factories, redis, slow))
    await asyncio.wait_for(entered.wait(), 20)
    # the next beat tick, while run 1 is still going; a timeout (not a hang) if the lock is broken
    try:
        overlapping = await asyncio.wait_for(_run(factories, redis, slow), 30)
    finally:
        gate.set()
    first = await first_run

    assert (overlapping.skipped_busy, overlapping.scored) == (1, 0)
    assert (first.scored, inner.calls) == (1, [facility])


async def test_the_lock_is_released_after_success_and_after_failure_and_has_an_expiry(
    factories, redis, migrated_main_db, migrated_timescale_db
) -> None:
    tenant, facility, assets = await _tenant_with_facility(migrated_main_db, "T")
    await _readings(migrated_timescale_db, tenant, assets[0], [("rpm", _ago(seconds=5))])
    key = svc._LOCK_KEY.format(tenant_id=tenant, facility_id=facility)

    failed = await _run(factories, redis, Recorder(fail_for={facility}))
    assert failed.failed == 1 and await redis.exists(key) == 0

    ok = await _run(factories, redis, Recorder())  # the failed attempt did not set the marker
    assert ok.scored == 1 and await redis.exists(key) == 0

    token = await svc.acquire_lock(redis, tenant_id=tenant, facility_id=facility, ttl_seconds=90)
    assert token and 0 < await redis.ttl(key) <= 90  # a dead run cannot block the facility forever


async def test_a_run_cannot_release_a_lock_that_another_run_now_holds(redis) -> None:
    tenant, facility = uuid.uuid4(), uuid.uuid4()
    mine = await svc.acquire_lock(redis, tenant_id=tenant, facility_id=facility, ttl_seconds=60)
    assert mine
    assert (
        await svc.acquire_lock(redis, tenant_id=tenant, facility_id=facility, ttl_seconds=60)
        is None
    )
    assert not await svc.release_lock(
        redis, tenant_id=tenant, facility_id=facility, token="not-mine"
    )
    assert await redis.exists(svc._LOCK_KEY.format(tenant_id=tenant, facility_id=facility)) == 1
    assert await svc.release_lock(redis, tenant_id=tenant, facility_id=facility, token=mine)
    assert await svc.acquire_lock(redis, tenant_id=tenant, facility_id=facility, ttl_seconds=60)


# ---- isolation between facilities and tenants ---------------------------------------------------


async def test_a_failing_facility_does_not_stop_the_others_in_any_tenant(
    factories, redis, migrated_main_db, migrated_timescale_db
) -> None:
    tenant_a, facility_a, assets_a = await _tenant_with_facility(migrated_main_db, "A")
    tenant_b, facility_b, assets_b = await _tenant_with_facility(migrated_main_db, "B")
    facility_a2, assets_a2 = await _facility_with_assets(migrated_main_db, tenant_a, 1)
    for tenant, asset in (
        (tenant_a, assets_a[0]),
        (tenant_a, assets_a2[0]),
        (tenant_b, assets_b[0]),
    ):
        await _readings(migrated_timescale_db, tenant, asset, [("rpm", _ago(seconds=3))])
    scorer = Recorder(fail_for={facility_a})

    summary = await _run(factories, redis, scorer)

    assert (summary.failed, summary.scored) == (1, 2)
    assert set(scorer.calls) == {facility_a, facility_a2, facility_b}
    assert str(facility_a) in summary.failures[0]
    # the failed facility has no marker, so it is retried on the next pass (the others are not)
    retry = await _run(factories, redis, Recorder())
    assert (retry.scored, retry.skipped_no_new_data) == (1, 2)


async def test_a_tenant_whose_facilities_cannot_be_listed_does_not_stop_the_other_tenants(
    factories, redis, migrated_main_db, migrated_timescale_db
) -> None:
    tenant_a, _, assets_a = await _tenant_with_facility(migrated_main_db, "A")
    tenant_b, facility_b, assets_b = await _tenant_with_facility(migrated_main_db, "B")
    for tenant, asset in ((tenant_a, assets_a[0]), (tenant_b, assets_b[0])):
        await _readings(migrated_timescale_db, tenant, asset, [("rpm", _ago(seconds=3))])
    main_factory, ts_factory = factories
    calls = {"n": 0}

    def flaky_main():
        calls["n"] += 1
        if calls["n"] == 2:  # call 1 lists tenants; call 2 lists the first tenant's facilities
            raise ConnectionError("database hiccup")
        return main_factory()

    scorer = Recorder()
    summary = await svc.score_all_facilities(
        main_factory=flaky_main,
        timescale_factory=ts_factory,
        redis=redis,
        score=scorer,  # type: ignore[arg-type]
    )

    assert summary.tenants_failed == 1
    assert summary.scored == 1 and len(scorer.calls) == 1


async def test_every_facility_is_scored_inside_its_own_tenants_rls_scope(
    factories, redis, migrated_main_db, migrated_timescale_db
) -> None:
    tenant_a, facility_a, assets_a = await _tenant_with_facility(migrated_main_db, "A", assets=3)
    tenant_b, facility_b, assets_b = await _tenant_with_facility(migrated_main_db, "B", assets=1)
    for tenant, assets in ((tenant_a, assets_a), (tenant_b, assets_b)):
        await _readings(migrated_timescale_db, tenant, assets[0], [("rpm", _ago(seconds=3))])
    scorer = Recorder()

    await _run(factories, redis, scorer)

    # both databases' sessions carry the right tenant; the main one sees only that tenant's assets
    assert scorer.scope_seen[facility_a] == (str(tenant_a), str(tenant_a), 3)
    assert scorer.scope_seen[facility_b] == (str(tenant_b), str(tenant_b), 1)


# ---- the push -----------------------------------------------------------------------------------


async def _listen(redis_server, tenant: uuid.UUID):
    client = fakeredis.FakeRedis(server=redis_server, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe(alert_channel(tenant))
    await pubsub.get_message(timeout=1)  # the subscribe confirmation
    return client, pubsub


async def _drain(pubsub) -> list[dict]:
    got = []
    while (
        message := await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
    ) is not None:
        got.append(json.loads(message["data"]))
    return got


async def test_scores_updated_reaches_only_the_tenant_it_belongs_to(
    factories, fake_redis_server, migrated_main_db, migrated_timescale_db
) -> None:
    tenant_a, facility_a, assets_a = await _tenant_with_facility(migrated_main_db, "A", assets=2)
    tenant_b, facility_b, assets_b = await _tenant_with_facility(migrated_main_db, "B", assets=1)
    await _readings(migrated_timescale_db, tenant_a, assets_a[0], [("rpm", _ago(seconds=3))])
    client_a, sub_a = await _listen(fake_redis_server, tenant_a)
    client_b, sub_b = await _listen(fake_redis_server, tenant_b)
    runner = fakeredis.FakeRedis(server=fake_redis_server, decode_responses=True)
    try:
        await _run(factories, runner, Recorder())  # only tenant A has readings
        events_a, events_b = await _drain(sub_a), await _drain(sub_b)
    finally:
        for c in (client_a, client_b, runner):
            await c.aclose()

    assert events_b == []  # tenant B neither scored nor listening to A's channel
    assert len(events_a) == 1
    event = ScoresUpdated.model_validate(events_a[0])  # what went out is a valid, typed event
    assert (event.type, event.facility_id, event.scored) == ("scores_updated", facility_a, 2)
    assert set(events_a[0]) == {
        "type",
        "facility_id",
        "scored",
        "computed_at",
    }  # ids and a count only


async def test_nothing_is_published_when_nothing_was_scored_and_a_lost_push_does_not_fail_the_run(
    factories, fake_redis_server, redis, migrated_main_db, migrated_timescale_db, monkeypatch
) -> None:
    tenant, facility, assets = await _tenant_with_facility(migrated_main_db, "T", assets=1)
    await _readings(migrated_timescale_db, tenant, assets[0], [("rpm", _ago(seconds=3))])
    client, sub = await _listen(fake_redis_server, tenant)
    try:
        await _run(
            factories, redis, Recorder(write_rows=False)
        )  # scored nothing: no rows, no event
        assert await _drain(sub) == []

        async def broken(*a, **k):
            raise ConnectionError("redis went away")

        monkeypatch.setattr(svc, "publish_scores_updated", broken)
        await _readings(migrated_timescale_db, tenant, assets[0], [("rpm", _ago(seconds=0))])
        summary = await _run(factories, redis, Recorder())
    finally:
        await client.aclose()

    assert (summary.scored, summary.failed) == (1, 0)


# ---- get_latest_risk_scores + the migration -----------------------------------------------------


async def test_latest_scores_returns_exactly_the_newest_row_per_asset(
    factories, migrated_main_db
) -> None:
    tenant, facility, assets = await _tenant_with_facility(migrated_main_db, "T", assets=3)
    admin = await _admin(migrated_main_db)
    try:
        for i, asset in enumerate(assets):
            for minutes_ago, score in ((30, 1.0), (10, 2.0), (0, 3.0 + i)):  # newest last
                await admin.execute(
                    "INSERT INTO risk_scores (tenant_id, facility_id, asset_id, score, "
                    "model_version, factors_json, computed_at) VALUES ($1,$2,$3,$4,'t','{}',$5)",
                    tenant, facility, asset, score, _ago(minutes=minutes_ago, microseconds=i),
                )  # fmt: skip
        stamp = _ago(
            minutes=2
        )  # two rows with the SAME computed_at: the answer must still be one row
        for score in (50.0, 60.0):
            await admin.execute(
                "INSERT INTO risk_scores (tenant_id, facility_id, asset_id, score, model_version, "
                "factors_json, computed_at) VALUES ($1,$2,$3,$4,'t','{}',$5)",
                tenant, facility, assets[0], score, stamp + timedelta(days=1),
            )  # fmt: skip
    finally:
        await admin.close()

    main_factory, _ = factories
    async with main_factory() as session:
        await scope_session_to_tenant(session, tenant)
        latest = await get_latest_risk_scores(session, tenant_id=tenant, facility_id=facility)

    by_asset = {row.asset_id: row.score for row in latest}
    assert len(latest) == 3 and set(by_asset) == set(assets)
    assert by_asset[assets[1]] == 4.0 and by_asset[assets[2]] == 5.0
    assert by_asset[assets[0]] in (50.0, 60.0)  # the future-dated pair wins; one row, not both


async def test_latest_scores_of_another_tenants_facility_is_a_404_even_when_the_filter_names_it(
    factories, migrated_main_db
) -> None:
    tenant_a, facility_a, _ = await _tenant_with_facility(migrated_main_db, "A", assets=1)
    tenant_b = await _make_tenant(migrated_main_db, "B")
    main_factory, _ = factories

    async with main_factory() as session:
        await scope_session_to_tenant(session, tenant_b)  # session scoped to B...
        with pytest.raises(HTTPException) as exc:
            await get_latest_risk_scores(session, tenant_id=tenant_b, facility_id=facility_a)
        assert exc.value.status_code == 404
    async with main_factory() as session:
        await scope_session_to_tenant(
            session, tenant_b
        )  # ...and even a filter naming A is stopped by RLS
        with pytest.raises(HTTPException) as exc:
            await get_latest_risk_scores(session, tenant_id=tenant_a, facility_id=facility_a)
        assert exc.value.status_code == 404


async def test_the_new_index_exists_and_the_forced_tenant_policy_is_untouched(
    migrated_main_db,
) -> None:
    admin = await _admin(migrated_main_db)
    try:
        index = await admin.fetchval(
            "SELECT indexdef FROM pg_indexes "
            "WHERE indexname='ix_risk_scores_facility_asset_computed'"
        )
        flags = await admin.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='risk_scores'"
        )
        policies = await admin.fetch(
            "SELECT policyname FROM pg_policies WHERE tablename='risk_scores'"
        )
    finally:
        await admin.close()
    assert index and "facility_id, asset_id, computed_at DESC" in index
    assert (flags["relrowsecurity"], flags["relforcerowsecurity"]) == (True, True)
    assert [p["policyname"] for p in policies] == ["tenant_isolation_risk_scores"]


# ---- beat configuration -------------------------------------------------------------------------


def test_beat_runs_the_registered_task_every_minute_by_default() -> None:
    from app.core.celery_app import celery_app
    from app.core.config import settings

    entry = celery_app.conf.beat_schedule["score-all-facilities"]
    assert entry["task"] == "score_all_facilities"
    assert entry["task"] in celery_app.tasks  # the worker would actually find it
    assert entry["schedule"] == settings.scoring_interval_seconds == 60.0
    assert 0 < settings.scoring_lock_ttl_seconds


def test_scores_updated_is_a_closed_typed_message() -> None:
    event = ScoresUpdated(facility_id=uuid.uuid4(), scored=3, computed_at=datetime.now(UTC))
    assert json.loads(event.model_dump_json())["type"] == "scores_updated"
    with pytest.raises(ValidationError):
        ScoresUpdated.model_validate(
            {
                "type": "alert",
                "facility_id": str(uuid.uuid4()),
                "scored": 1,
                "computed_at": datetime.now(UTC).isoformat(),
            }
        )


# ---- the Celery entry point ---------------------------------------------------------------------


def test_the_task_entry_point_survives_being_run_again_in_the_same_worker_process(
    migrated_main_db, migrated_timescale_db, fake_redis_server, monkeypatch
) -> None:
    """Regression for a bug found by running the task for real: the task calls asyncio.run on every
    beat tick, a new event loop each time, and the global engines' pooled connections belong to the
    previous (closed) loop. The second tick crashed. The entry point must work run after run."""
    from types import SimpleNamespace

    from app.workers import scheduled_scoring

    monkeypatch.setattr(
        scheduled_scoring,
        "settings",
        SimpleNamespace(
            app_database_url=_dsn(
                migrated_main_db,
                user="app_role",
                password=APP_ROLE_PASSWORD,
                driver="postgresql+asyncpg",
            ),
            app_timescale_url=_dsn(
                migrated_timescale_db,
                user="app_role",
                password=APP_TIMESCALE_ROLE_PASSWORD,
                driver="postgresql+asyncpg",
            ),
        ),
    )
    monkeypatch.setattr(
        scheduled_scoring,
        "get_redis_client",
        lambda: fakeredis.FakeRedis(server=fake_redis_server, decode_responses=True),
    )
    recorder = Recorder()

    async def real_service_with_fake_model(**kwargs):
        return await svc.score_all_facilities(score=recorder, **kwargs)

    monkeypatch.setattr(scheduled_scoring, "score_all_facilities", real_service_with_fake_model)

    async def arrange():
        tenant, facility, assets = await _tenant_with_facility(migrated_main_db, "T", assets=1)
        await _readings(migrated_timescale_db, tenant, assets[0], [("rpm", _ago(seconds=2))])
        return facility

    facility = asyncio.run(arrange())

    first = scheduled_scoring.run_scheduled_scoring()
    second = scheduled_scoring.run_scheduled_scoring()  # a new event loop, as on the next beat tick
    third = scheduled_scoring.run_scheduled_scoring()

    assert first["scored"] == 1 and first["failed"] == 0
    assert second["skipped_no_new_data"] == 1 and second["failed"] == 0
    assert third["failed"] == 0 and third["tenants_failed"] == 0
    assert recorder.calls == [facility]
