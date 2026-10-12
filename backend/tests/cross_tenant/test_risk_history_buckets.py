"""risk-history with `bucket=hour|day` (Phase L task L.5), against a real RLS-enforcing Postgres.

Scoring every minute makes the raw "last 50 scores" cover under an hour. `bucket` reduces a window
of calendar hours or days to the NEWEST score of each. The query reads an RLS table with a new
aggregation, so the failures to guard against are: a wrong point per bucket (average, oldest, a
neighbouring bucket), UTC boundaries moved by the session time zone, an unbounded or invalid
request, and another tenant's (or facility's) scores appearing in the answer. The route runs as the
non-bypass `app_role`, so row-level security is really in force.
"""

# ruff: noqa: F401, F811  (pytest fixtures are imported from test_telemetry_isolation)
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import create_access_token
from app.core.tenant_context import scope_session_to_tenant
from app.services.risk_inference_service import get_risk_history
from tests.cross_tenant.test_telemetry_freshness import _admin, _facility_with_assets
from tests.cross_tenant.test_telemetry_isolation import (
    APP_ROLE_PASSWORD,
    _dsn,
    _make_tenant,
    client,
    fake_redis_server,
    main_pg_container,
    migrated_main_db,
    migrated_timescale_db,
    timescale_pg_container,
)

# A fixed "now" for service-level tests: Thursday 2026-10-15 10:20 UTC.
NOW = datetime(2026, 10, 15, 10, 20, tzinfo=UTC)


def _hdr(tenant: uuid.UUID, role: str = "viewer") -> dict[str, str]:
    token = create_access_token(user_id=uuid.uuid4(), tenant_id=tenant, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
async def clean(migrated_main_db):
    admin = await _admin(migrated_main_db)
    try:
        await admin.execute(
            "TRUNCATE risk_scores, asset_dependencies, assets, facilities, tenants CASCADE"
        )
    finally:
        await admin.close()


async def _scores(
    db,
    tenant: uuid.UUID,
    facility: uuid.UUID,
    asset: uuid.UUID,
    rows: list[tuple[datetime, float] | tuple[datetime, float, float | None]],
) -> None:
    admin = await _admin(db)
    try:
        for row in rows:
            at, score = row[0], row[1]
            rul = row[2] if len(row) > 2 else None
            await admin.execute(
                "INSERT INTO risk_scores (tenant_id, facility_id, asset_id, score, rul_cycles, "
                "model_version, factors_json, computed_at) "
                "VALUES ($1,$2,$3,$4,$5,'secret-v9','{\"secret\": 1}',$6)",
                tenant, facility, asset, score, rul, at,
            )  # fmt: skip
    finally:
        await admin.close()


@pytest.fixture
async def app_session(migrated_main_db):
    engine = create_async_engine(
        _dsn(
            migrated_main_db,
            user="app_role",
            password=APP_ROLE_PASSWORD,
            driver="postgresql+asyncpg",
        )
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _history(factory, tenant, facility, *, limit=24, bucket="hour", now=NOW, scope=None):
    async with factory() as session:
        await scope_session_to_tenant(session, scope if scope is not None else tenant)
        return await get_risk_history(
            session,
            tenant_id=tenant,
            facility_id=facility,
            limit_per_asset=limit,
            bucket=bucket,
            now=now,
        )


async def _world(db, assets=1):
    tenant = await _make_tenant(db, "A")
    facility, ids = await _facility_with_assets(db, tenant, assets)
    return tenant, facility, ids


def _at(h: int, m: int = 0, s: int = 0, day: int = 15) -> datetime:
    return datetime(2026, 10, day, h, m, s, tzinfo=UTC)


# ---- which point represents a bucket ----------------------------------------------------------


async def test_each_hour_is_represented_by_its_newest_score_not_an_average_or_the_highest(
    migrated_main_db, app_session
) -> None:
    tenant, facility, (asset,) = await _world(migrated_main_db)
    await _scores(
        migrated_main_db, tenant, facility, asset,
        [(_at(8, 5), 1.0), (_at(8, 30), 90.0), (_at(8, 59, 59), 4.0),  # 8h: newest = 4.0
         (_at(9, 0, 0), 5.0), (_at(9, 20), 2.0, 12.5)],  # 9h: newest = 2.0 (spike stays hidden)
    )  # fmt: skip

    points = (await _history(app_session, tenant, facility))[asset]

    assert [(p[0], p[1], p[2]) for p in points] == [
        (_at(8, 59, 59), 4.0, None),  # the real time of the newest score, not the bucket start
        (_at(9, 20), 2.0, 12.5),  # RUL travels with it
    ]


async def test_a_score_at_the_last_second_of_an_hour_or_day_stays_in_that_bucket(
    migrated_main_db, app_session
) -> None:
    tenant, facility, (asset,) = await _world(migrated_main_db)
    await _scores(
        migrated_main_db, tenant, facility, asset,
        [(_at(23, 59, 59, day=13), 1.0), (_at(0, 0, 0, day=14), 2.0),
         (_at(23, 59, 59, day=14), 3.0)],
    )  # fmt: skip

    by_day = (await _history(app_session, tenant, facility, bucket="day", limit=5))[asset]
    by_hour = (await _history(app_session, tenant, facility, bucket="hour", limit=48))[asset]

    assert [p[1] for p in by_day] == [1.0, 3.0]  # Oct 13 | Oct 14 (00:00:00 + 23:59:59 -> newest)
    assert [p[1] for p in by_hour] == [1.0, 2.0, 3.0]  # three different hours


async def test_the_session_time_zone_cannot_move_the_utc_buckets(
    migrated_main_db, app_session
) -> None:
    """18:00 and 19:00 UTC are one UTC day but two days in Asia/Kolkata (midnight is 18:30 UTC)."""
    tenant, facility, (asset,) = await _world(migrated_main_db)
    await _scores(
        migrated_main_db, tenant, facility, asset,
        [(_at(18, 0, day=14), 1.0), (_at(19, 0, day=14), 2.0)],
    )  # fmt: skip
    async with app_session() as session:
        await session.execute(text("SET TIME ZONE 'Asia/Kolkata'"))
        await scope_session_to_tenant(session, tenant)
        got = await get_risk_history(
            session,
            tenant_id=tenant,
            facility_id=facility,
            limit_per_asset=5,
            bucket="day",
            now=NOW,
        )

    assert [p[1] for p in got[asset]] == [2.0]  # one UTC day -> its newest score


async def test_hours_with_no_score_have_no_point_and_a_single_score_is_fine(
    migrated_main_db, app_session
) -> None:
    tenant, facility, (busy, lonely) = await _world(migrated_main_db, assets=2)
    await _scores(migrated_main_db, tenant, facility, busy, [(_at(3), 1.0), (_at(9, 40), 2.0)])
    await _scores(migrated_main_db, tenant, facility, lonely, [(_at(10, 10), 7.0)])

    got = await _history(app_session, tenant, facility)

    assert [p[1] for p in got[busy]] == [1.0, 2.0]  # nothing invented for 4h-8h
    assert [p[1] for p in got[lonely]] == [7.0]


async def test_equal_timestamps_in_a_bucket_give_one_stable_row(
    migrated_main_db, app_session
) -> None:
    tenant, facility, (asset,) = await _world(migrated_main_db)
    await _scores(migrated_main_db, tenant, facility, asset, [(_at(9, 5), 1.0), (_at(9, 5), 2.0)])

    first = (await _history(app_session, tenant, facility))[asset]
    second = (await _history(app_session, tenant, facility))[asset]

    assert len(first) == 1 and first == second


# ---- the window -------------------------------------------------------------------------------


async def test_limit_is_a_calendar_window_of_buckets_ending_now_returned_oldest_first(
    migrated_main_db, app_session
) -> None:
    tenant, facility, (asset,) = await _world(migrated_main_db)
    await _scores(
        migrated_main_db, tenant, facility, asset,
        [(_at(h, 30), float(h)) for h in range(0, 11)],  # a score at hh:30 for 00h .. 10h
    )  # fmt: skip

    last_three = (await _history(app_session, tenant, facility, limit=3))[asset]
    one = (await _history(app_session, tenant, facility, limit=1))[asset]

    assert [p[1] for p in last_three] == [8.0, 9.0, 10.0]  # the current hour and the two before it
    assert [p[1] for p in one] == [10.0]


async def test_scores_older_than_the_window_are_not_read_even_for_a_silent_asset(
    migrated_main_db, app_session
) -> None:
    tenant, facility, (old, fresh) = await _world(migrated_main_db, assets=2)
    await _scores(migrated_main_db, tenant, facility, old, [(_at(10, day=1), 1.0)])
    await _scores(migrated_main_db, tenant, facility, fresh, [(_at(10, 5), 2.0)])

    got = await _history(app_session, tenant, facility, limit=24)

    assert old not in got and [p[1] for p in got[fresh]] == [2.0]


async def test_without_a_bucket_the_answer_is_exactly_what_it_was_before(
    migrated_main_db, app_session
) -> None:
    tenant, facility, (asset,) = await _world(migrated_main_db)
    await _scores(
        migrated_main_db, tenant, facility, asset,
        [(_at(8, 1), 1.0), (_at(8, 2), 2.0), (_at(8, 3), 3.0)],
    )  # fmt: skip

    async with app_session() as session:
        await scope_session_to_tenant(session, tenant)
        raw = await get_risk_history(
            session, tenant_id=tenant, facility_id=facility, limit_per_asset=2
        )

    assert [p[1] for p in raw[asset]] == [
        2.0,
        3.0,
    ]  # three minutes of one hour are all still distinct


# ---- isolation --------------------------------------------------------------------------------


async def test_another_tenants_facility_is_a_404_and_my_own_other_facility_never_leaks_in(
    client: httpx.AsyncClient, migrated_main_db
) -> None:
    now = datetime.now(UTC)
    tenant_a, facility_a1, (asset_a1,) = await _world(migrated_main_db)
    facility_a2, (asset_a2,) = await _facility_with_assets(migrated_main_db, tenant_a, 1)
    tenant_b = await _make_tenant(migrated_main_db, "B")
    facility_b, (asset_b,) = await _facility_with_assets(migrated_main_db, tenant_b, 1)
    for tenant, facility, asset in (
        (tenant_a, facility_a1, asset_a1),
        (tenant_a, facility_a2, asset_a2),
        (tenant_b, facility_b, asset_b),
    ):
        await _scores(
            migrated_main_db, tenant, facility, asset, [(now - timedelta(minutes=2), 5.0)]
        )

    foreign = await client.get(
        f"/facilities/{facility_b}/risk-history?bucket=hour", headers=_hdr(tenant_a)
    )
    own = await client.get(
        f"/facilities/{facility_a1}/risk-history?bucket=hour", headers=_hdr(tenant_a)
    )

    assert foreign.status_code == 404 and str(asset_b) not in foreign.text
    assert [s["asset_id"] for s in own.json()] == [str(asset_a1)]  # not A2, not B


async def test_rls_alone_stops_a_bucketed_read_even_when_the_tenant_filter_names_the_owner(
    migrated_main_db, app_session
) -> None:
    tenant_a, facility_a, (asset,) = await _world(migrated_main_db)
    tenant_b = await _make_tenant(migrated_main_db, "B")
    await _scores(migrated_main_db, tenant_a, facility_a, asset, [(_at(10, 5), 5.0)])

    for scope in (tenant_b, uuid.uuid4()):  # a different tenant, and a tenant that does not exist
        with pytest.raises(HTTPException) as exc:
            await _history(app_session, tenant_a, facility_a, scope=scope)
        assert exc.value.status_code == 404

    async with app_session() as session:  # no tenant at all: fail closed
        with pytest.raises(HTTPException) as exc:
            await get_risk_history(
                session,
                tenant_id=tenant_a,
                facility_id=facility_a,
                limit_per_asset=5,
                bucket="hour",
                now=NOW,
            )
        assert exc.value.status_code == 404


async def test_without_the_tenant_filter_rls_would_still_hide_other_tenants_rows(
    migrated_main_db, app_session
) -> None:
    """The query's own WHERE is not the only wall: as tenant B, a raw read of all risk_scores
    (no filter at all) returns none of tenant A's rows."""
    tenant_a, facility_a, (asset,) = await _world(migrated_main_db)
    tenant_b = await _make_tenant(migrated_main_db, "B")
    await _scores(migrated_main_db, tenant_a, facility_a, asset, [(_at(10, 5), 5.0)])
    async with app_session() as session:
        await scope_session_to_tenant(session, tenant_b)
        assert (await session.execute(text("SELECT count(*) FROM risk_scores"))).scalar_one() == 0


# ---- abuse ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bucket", ["week", "minute", "", "HOUR", "Day", "hour;DROP TABLE risk_scores", "' OR 1=1 --"]
)
async def test_any_bucket_other_than_hour_or_day_is_rejected(
    client: httpx.AsyncClient, migrated_main_db, bucket: str
) -> None:
    tenant, facility, _ = await _world(migrated_main_db)

    resp = await client.get(
        f"/facilities/{facility}/risk-history", params={"bucket": bucket}, headers=_hdr(tenant)
    )

    assert resp.status_code == 422


@pytest.mark.parametrize("limit", [0, -1, 51, 1000, "abc"])
async def test_the_limit_stays_bounded_in_bucket_mode(
    client: httpx.AsyncClient, migrated_main_db, limit
) -> None:
    tenant, facility, _ = await _world(migrated_main_db)

    resp = await client.get(
        f"/facilities/{facility}/risk-history",
        params={"bucket": "day", "limit_per_asset": limit},
        headers=_hdr(tenant),
    )

    assert resp.status_code == 422


async def test_the_maximum_window_works_and_never_returns_more_than_that_many_points(
    client: httpx.AsyncClient, migrated_main_db
) -> None:
    now = datetime.now(UTC)
    tenant, facility, (asset,) = await _world(migrated_main_db)
    await _scores(
        migrated_main_db, tenant, facility, asset,
        [(now - timedelta(hours=h, minutes=1), float(h % 100)) for h in range(0, 200)],
    )  # fmt: skip

    resp = await client.get(
        f"/facilities/{facility}/risk-history",
        params={"bucket": "hour", "limit_per_asset": 50},
        headers=_hdr(tenant),
    )

    points = resp.json()[0]["points"]
    assert resp.status_code == 200 and 49 <= len(points) <= 50
    assert [p["computed_at"] for p in points] == sorted(p["computed_at"] for p in points)


async def test_the_response_shows_only_time_score_and_rul_and_needs_a_token(
    client: httpx.AsyncClient, migrated_main_db
) -> None:
    now = datetime.now(UTC)
    tenant, facility, (asset,) = await _world(migrated_main_db)
    await _scores(
        migrated_main_db, tenant, facility, asset, [(now - timedelta(minutes=1), 5.0, 9.0)]
    )

    ok = await client.get(f"/facilities/{facility}/risk-history?bucket=day", headers=_hdr(tenant))
    anon = await client.get(f"/facilities/{facility}/risk-history?bucket=day")

    assert set(ok.json()[0]["points"][0]) == {"computed_at", "score", "rul_cycles"}
    assert "secret" not in ok.text
    assert anon.status_code == 401


async def test_a_direct_caller_cannot_slip_an_unknown_bucket_past_the_service(
    migrated_main_db, app_session
) -> None:
    tenant, facility, _ = await _world(migrated_main_db)
    with pytest.raises(ValueError):
        await _history(app_session, tenant, facility, bucket="week")  # type: ignore[arg-type]


# ---- cost -------------------------------------------------------------------------------------


async def test_a_long_history_costs_only_the_window_not_the_whole_table(
    migrated_main_db, app_session
) -> None:
    """60 days of per-minute scores for 3 assets (~260k rows): a 24 hour hourly view returns
    quickly and reads far fewer rows than exist."""
    import time

    tenant, facility, assets = await _world(migrated_main_db, assets=3)
    admin = await _admin(migrated_main_db)
    try:
        await admin.execute(
            "INSERT INTO risk_scores (tenant_id, facility_id, asset_id, score, model_version, "
            "factors_json, computed_at) "
            "SELECT $1, $2, a, (random()*100), 'v', '{}'::json, "
            "$4::timestamptz - (g * interval '1 minute') "
            "FROM unnest($3::uuid[]) AS a, generate_series(0, 86399) AS g",
            tenant, facility, assets, NOW,
        )  # fmt: skip
        await admin.execute("ANALYZE risk_scores")
    finally:
        await admin.close()

    started = time.perf_counter()
    got = await _history(app_session, tenant, facility, limit=24)
    elapsed = time.perf_counter() - started

    assert all(len(points) == 24 for points in got.values()) and len(got) == 3
    assert elapsed < 2.0, f"24h hourly view took {elapsed:.2f}s over a 60-day history"
