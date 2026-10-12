"""Automatic analysis in the worker (Phase L task L.7; rules 4 and 1), against real RLS databases.

What must hold: a storm of "newly high risk" scoring passes yields ONE pipeline run per facility per
window; overlapping triggers yield one; the floor and lock keys are per tenant and facility; a
failed run is recorded and neither blocks the next window nor is retried inside this one; only a
machine that CROSSES into high risk (or a never-analysed facility) triggers anything; scoring never
fails because of the analysis path; and the run executes inside its own tenant's RLS scope. The
pipeline function is replaced by a recorder - agent quality is not under test here; the recording
and validation behaviour of the real pipeline wrapper is covered in
tests/unit/test_auto_analysis_recording.py.
"""

# ruff: noqa: F401, F811  (pytest fixtures are imported from sibling test modules)
import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.models.risk_score import RiskScore
from app.services import auto_analysis_service as auto
from app.services import scheduled_scoring_service as svc
from tests.cross_tenant.test_scheduled_scoring import (
    _run,
    _tenant_with_facility,
    factories,
    redis,
)
from tests.cross_tenant.test_telemetry_freshness import (
    _admin,
    _facility_with_assets,
    _readings,
)
from tests.cross_tenant.test_telemetry_isolation import (
    fake_redis_server,
    main_pg_container,
    migrated_main_db,
    migrated_timescale_db,
    timescale_pg_container,
)


@pytest.fixture(autouse=True)
async def clean(migrated_main_db, migrated_timescale_db):
    main = await _admin(migrated_main_db)
    ts = await _admin(migrated_timescale_db)
    try:
        await main.execute(
            "TRUNCATE agent_runs, risk_scores, asset_dependencies, assets, facilities, "
            "tenants CASCADE"
        )
        await ts.execute("TRUNCATE sensor_readings")
    finally:
        await main.close()
        await ts.close()


class Pipeline:
    """Stands in for `run_pipeline_for_facility`; records the call and the RLS scope it saw."""

    def __init__(self, *, fail: bool = False, gate: asyncio.Event | None = None) -> None:
        self.calls: list[dict] = []
        self.fail = fail
        self.gate = gate
        self.entered = asyncio.Event()

    async def __call__(self, session, *, tenant_id, facility_id, trigger, recorded_trigger):
        scope = (
            await session.execute(text("SELECT current_setting('app.current_tenant_id', true)"))
        ).scalar_one()
        self.calls.append(
            {
                "tenant": tenant_id,
                "facility": facility_id,
                "trigger": trigger,
                "recorded": recorded_trigger,
                "scope": scope,
            }
        )
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("pipeline blew up")


async def _analyse(factories, redis, tenant, facility, pipeline, reason="auto_high_risk"):
    return await auto.run_auto_analysis(
        main_factory=factories[0],
        redis=redis,
        tenant_id=tenant,
        facility_id=facility,
        reason=reason,
        pipeline=pipeline,
    )


def _floor(tenant, facility) -> str:
    return auto._FLOOR_KEY.format(tenant_id=tenant, facility_id=facility)


# ---- the trigger rule (pure) --------------------------------------------------------------------


def _row(asset: uuid.UUID, score: float) -> RiskScore:
    return RiskScore(asset_id=asset, score=score)


def _newly(previous, new):
    return svc.newly_high_risk_assets(previous, new, threshold=67.0)


def test_only_a_machine_that_crosses_the_threshold_is_newly_high_risk() -> None:
    a, b, c, d, e = (uuid.uuid4() for _ in range(5))
    previous = {a: 40.0, b: 80.0, c: 66.9, d: 67.0}
    new = [_row(a, 70.0), _row(b, 95.0), _row(c, 67.0), _row(d, 10.0), _row(e, 20.0)]

    assert _newly(previous, new) == [a, c]  # b stayed high, d fell, e is new but low
    assert _newly({}, [_row(e, 67.0)]) == [e]  # a first-ever score that is already high counts
    assert _newly({}, [_row(e, 66.9)]) == []


# ---- the floor and the lock (rule 4) ------------------------------------------------------------


async def test_a_storm_of_triggers_in_one_window_runs_the_pipeline_once(
    factories, redis, migrated_main_db
) -> None:
    tenant, facility, _ = await _tenant_with_facility(migrated_main_db, "T")
    pipeline = Pipeline()

    outcomes = [await _analyse(factories, redis, tenant, facility, pipeline) for _ in range(50)]

    assert outcomes.count("ran") == 1 and outcomes.count("too_soon") == 49
    assert len(pipeline.calls) == 1
    ttl = await redis.ttl(_floor(tenant, facility))
    assert 0 < ttl <= settings.auto_analysis_min_interval_seconds


async def test_overlapping_triggers_run_the_pipeline_once_and_the_loser_does_not_wait(
    factories, redis, migrated_main_db
) -> None:
    tenant, facility, _ = await _tenant_with_facility(migrated_main_db, "T")
    gate = asyncio.Event()
    slow = Pipeline(gate=gate)
    first = asyncio.create_task(_analyse(factories, redis, tenant, facility, slow))
    await asyncio.wait_for(slow.entered.wait(), 20)
    try:  # a timeout rather than a hang if the lock were broken
        second = await asyncio.wait_for(_analyse(factories, redis, tenant, facility, slow), 20)
    finally:
        gate.set()

    assert second == "busy"
    assert await first == "ran"
    assert len(slow.calls) == 1


async def test_after_the_window_expires_the_next_trigger_runs(
    factories, redis, migrated_main_db
) -> None:
    tenant, facility, _ = await _tenant_with_facility(migrated_main_db, "T")
    pipeline = Pipeline()
    assert await _analyse(factories, redis, tenant, facility, pipeline) == "ran"
    assert await _analyse(factories, redis, tenant, facility, pipeline) == "too_soon"

    await redis.delete(_floor(tenant, facility))  # what Redis does when the 10 min TTL elapses

    assert await _analyse(factories, redis, tenant, facility, pipeline) == "ran"
    assert len(pipeline.calls) == 2


async def test_a_failed_run_is_not_retried_in_the_window_releases_the_lock_and_next_window_runs(
    factories, redis, migrated_main_db
) -> None:
    tenant, facility, _ = await _tenant_with_facility(migrated_main_db, "T")
    lock = auto._LOCK_KEY.format(tenant_id=tenant, facility_id=facility)

    assert await _analyse(factories, redis, tenant, facility, Pipeline(fail=True)) == "failed"
    assert await redis.exists(lock) == 0  # released, so nothing is stuck
    assert await _analyse(factories, redis, tenant, facility, Pipeline()) == "too_soon"

    await redis.delete(_floor(tenant, facility))
    assert await _analyse(factories, redis, tenant, facility, Pipeline()) == "ran"


async def test_keys_are_per_tenant_and_per_facility_so_one_does_not_throttle_another(
    factories, redis, migrated_main_db
) -> None:
    tenant_a, facility_a, _ = await _tenant_with_facility(migrated_main_db, "A")
    tenant_b, facility_b, _ = await _tenant_with_facility(migrated_main_db, "B")
    facility_a2, _ = await _facility_with_assets(migrated_main_db, tenant_a, 1)
    pipeline = Pipeline()
    pairs = ((tenant_a, facility_a), (tenant_b, facility_b), (tenant_a, facility_a2))

    results = [await _analyse(factories, redis, t, f, pipeline) for t, f in pairs]

    assert results == ["ran", "ran", "ran"]
    assert _floor(tenant_a, facility_a) != _floor(tenant_b, facility_b)
    assert str(tenant_a) in _floor(tenant_a, facility_a)


async def test_the_analysis_lock_is_not_the_scoring_lock(
    factories, redis, migrated_main_db
) -> None:
    """A scoring pass holding its facility lock must not block the analysis it just triggered."""
    tenant, facility, _ = await _tenant_with_facility(migrated_main_db, "T")
    assert await svc.acquire_lock(redis, tenant_id=tenant, facility_id=facility, ttl_seconds=60)

    assert await _analyse(factories, redis, tenant, facility, Pipeline()) == "ran"


async def test_the_run_executes_in_its_own_tenants_rls_scope_with_a_plain_scheduled_trigger(
    factories, redis, migrated_main_db
) -> None:
    tenant_a, facility_a, _ = await _tenant_with_facility(migrated_main_db, "A")
    tenant_b, facility_b, _ = await _tenant_with_facility(migrated_main_db, "B")
    pipeline = Pipeline()

    await _analyse(factories, redis, tenant_a, facility_a, pipeline, reason="auto_startup")
    await _analyse(factories, redis, tenant_b, facility_b, pipeline)

    by_tenant = {c["tenant"]: c for c in pipeline.calls}
    assert by_tenant[tenant_a]["scope"] == str(tenant_a)
    assert by_tenant[tenant_b]["scope"] == str(tenant_b)
    assert by_tenant[tenant_a]["facility"] == facility_a
    assert by_tenant[tenant_a]["recorded"] == "auto_startup"
    assert by_tenant[tenant_b]["recorded"] == "auto_high_risk"
    assert {c["trigger"] for c in pipeline.calls} == {"scheduled"}  # agents see a scheduled run


# ---- the scoring pass decides when to enqueue ---------------------------------------------------


class Scorer:
    """Writes one risk_scores row per asset with a chosen score, like the real `score_facility`."""

    def __init__(self, score: float | dict[int, float]) -> None:
        self.score = score

    async def __call__(self, main_session, timescale_session, *, tenant_id, facility_id, now):
        ids = list(
            (
                await main_session.execute(
                    text("SELECT id FROM assets WHERE facility_id = :f ORDER BY id"),
                    {"f": facility_id},
                )
            ).scalars()
        )
        rows = []
        for i, asset_id in enumerate(ids):
            value = self.score[i] if isinstance(self.score, dict) else self.score
            row = RiskScore(
                tenant_id=tenant_id,
                facility_id=facility_id,
                asset_id=asset_id,
                score=value,
                rul_cycles=None,
                model_version="test",
                factors_json={},
                computed_at=now,
            )
            main_session.add(row)
            rows.append(row)
        await main_session.commit()
        return rows


async def _pass(factories, redis, scorer, enqueued):
    main_factory, ts_factory = factories
    return await svc.score_all_facilities(
        main_factory=main_factory,
        timescale_factory=ts_factory,
        redis=redis,
        score=scorer,
        enqueue_analysis=lambda t, f, r: enqueued.append((t, f, r)),
    )


async def _fresh_reading(ts_db, tenant, asset):
    await _readings(ts_db, tenant, asset, [("rpm", datetime.now(UTC))])


async def _insert_agent_run(main_db, tenant, facility) -> None:
    admin = await _admin(main_db)
    try:
        await admin.execute(
            "INSERT INTO agent_runs (tenant_id, facility_id, trigger, status, duration_ms, "
            "state_snapshot_json, validation_errors_json) "
            "VALUES ($1,$2,'scheduled','completed',1,'{}','[]')",
            tenant, facility,
        )  # fmt: skip
    finally:
        await admin.close()


async def test_a_machine_newly_entering_high_risk_enqueues_one_analysis_with_the_reason(
    factories, redis, migrated_main_db, migrated_timescale_db
) -> None:
    tenant, facility, assets = await _tenant_with_facility(migrated_main_db, "T", assets=2)
    await _insert_agent_run(migrated_main_db, tenant, facility)  # not a start-up case
    enqueued: list = []
    await _fresh_reading(migrated_timescale_db, tenant, assets[0])
    await _pass(factories, redis, Scorer(20.0), enqueued)
    assert enqueued == []  # low everywhere: nothing to analyse

    await _fresh_reading(migrated_timescale_db, tenant, assets[0])
    await _pass(factories, redis, Scorer({0: 90.0, 1: 20.0}), enqueued)

    assert enqueued == [(tenant, facility, "auto_high_risk")]


async def test_a_machine_that_stays_high_or_falls_does_not_enqueue_again(
    factories, redis, migrated_main_db, migrated_timescale_db
) -> None:
    tenant, facility, assets = await _tenant_with_facility(migrated_main_db, "T", assets=1)
    await _insert_agent_run(migrated_main_db, tenant, facility)
    enqueued: list = []
    for score in (90.0, 95.0, 30.0):  # newly high, still high, recovered
        await _fresh_reading(migrated_timescale_db, tenant, assets[0])
        await _pass(factories, redis, Scorer(score), enqueued)

    assert enqueued == [(tenant, facility, "auto_high_risk")]


async def test_a_facility_never_analysed_enqueues_one_startup_run_even_when_low_risk(
    factories, redis, migrated_main_db, migrated_timescale_db
) -> None:
    tenant, facility, assets = await _tenant_with_facility(migrated_main_db, "T", assets=1)
    enqueued: list = []
    await _fresh_reading(migrated_timescale_db, tenant, assets[0])

    await _pass(factories, redis, Scorer(10.0), enqueued)

    assert enqueued == [(tenant, facility, "auto_startup")]

    # once any run exists (even a failed one) the start-up reason stops firing
    await _insert_agent_run(migrated_main_db, tenant, facility)
    await _fresh_reading(migrated_timescale_db, tenant, assets[0])
    await _pass(factories, redis, Scorer(10.0), enqueued)
    assert len(enqueued) == 1


async def test_another_tenants_agent_run_does_not_count_as_my_startup_run(
    factories, redis, migrated_main_db, migrated_timescale_db
) -> None:
    tenant_a, facility_a, assets_a = await _tenant_with_facility(migrated_main_db, "A", assets=1)
    tenant_b, facility_b, _ = await _tenant_with_facility(migrated_main_db, "B", assets=1)
    await _insert_agent_run(migrated_main_db, tenant_b, facility_b)
    enqueued: list = []
    await _fresh_reading(migrated_timescale_db, tenant_a, assets_a[0])

    await _pass(factories, redis, Scorer(10.0), enqueued)

    assert enqueued == [(tenant_a, facility_a, "auto_startup")]


async def test_nothing_is_enqueued_when_scoring_fails_or_finds_no_new_readings(
    factories, redis, migrated_main_db, migrated_timescale_db
) -> None:
    tenant, facility, assets = await _tenant_with_facility(migrated_main_db, "T", assets=1)
    enqueued: list = []

    idle = await _pass(factories, redis, Scorer(99.0), enqueued)  # no readings at all
    await _fresh_reading(migrated_timescale_db, tenant, assets[0])

    async def boom(*a, **k):
        raise RuntimeError("model blew up")

    failed = await _pass(factories, redis, boom, enqueued)

    assert (idle.scored, failed.failed, enqueued) == (0, 1, [])


async def test_a_broker_failure_while_enqueueing_does_not_fail_the_scoring_pass(
    factories, redis, migrated_main_db, migrated_timescale_db
) -> None:
    tenant, facility, assets = await _tenant_with_facility(migrated_main_db, "T", assets=1)
    await _fresh_reading(migrated_timescale_db, tenant, assets[0])
    main_factory, ts_factory = factories

    def broker_down(*a):
        raise ConnectionError("broker unreachable")

    summary = await svc.score_all_facilities(
        main_factory=main_factory,
        timescale_factory=ts_factory,
        redis=redis,
        score=Scorer(99.0),
        enqueue_analysis=broker_down,
    )

    assert (summary.scored, summary.failed) == (1, 0)
