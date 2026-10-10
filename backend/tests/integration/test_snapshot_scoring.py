# ruff: noqa: F811 - pytest fixtures imported from the sibling module and re-requested by name
"""Risk scoring runs on the latest-snapshot features, through real TimescaleDB + RLS.

Adversarial focus: windowed history must not leak into the model input, a live feed cannot
spoof the derived channels, incoherent / incomplete snapshots are not scored, and another
tenant's telemetry is invisible.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from testcontainers.postgres import PostgresContainer

from app.ml.ai4i_replay import pick_row, replay_readings
from app.ml.feature_vector import FEATURE_NAMES, vectorize
from app.services.feature_engineering_service import (
    SNAPSHOT_INCOHERENT,
    SNAPSHOT_MISSING_SENSORS,
    build_asset_features_from_mapped_record,
    build_asset_features_from_readings,
    build_snapshot_features,
)
from app.services.ml.dataset_mapping import map_ai4i2020_row
from tests.integration.test_risk_inference_service import (  # noqa: F401 - fixtures + helpers
    _heldout_ai4i_rows,
    _insert_ai4i_snapshot,
    _insert_telemetry,
    _make_asset,
    _make_facility,
    _make_tenant,
    _scoped,
    fake_model,
    main_pg_container,
    main_session_factory,
    migrated_main_db,
    migrated_timescale_db,
    timescale_pg_container,
    timescale_session_factory,
)

NOW = datetime.now(UTC)


def _base(tenant_id: uuid.UUID, facility_id: uuid.UUID, asset_id: uuid.UUID) -> Any:
    """Windowed-features stand-in carrying only the non-sensor context (status, ids)."""
    return build_asset_features_from_readings(
        asset_id, [], tenant_id=tenant_id, facility_id=facility_id, computed_at=NOW
    )


async def _snapshots(
    session_factory: async_sessionmaker,
    session_tenant: uuid.UUID,
    tenant_id: uuid.UUID,
    assets: Any,
) -> dict:
    session = await _scoped(session_factory, session_tenant)
    try:
        return await build_snapshot_features(session, tenant_id=tenant_id, base_features=assets)
    finally:
        await session.close()


def _training_vector(row: dict[str, str]) -> np.ndarray:
    return vectorize(build_asset_features_from_mapped_record(map_ai4i2020_row(row)))


async def test_db_snapshot_equals_training_vector_despite_history_and_spoofed_channels(
    migrated_main_db: PostgresContainer,
    migrated_timescale_db: PostgresContainer,
    main_session_factory: async_sessionmaker,
    timescale_session_factory: async_sessionmaker,
) -> None:
    tenant = await _make_tenant(migrated_main_db, "Tenant Snap A")
    facility = await _make_facility(migrated_main_db, tenant_id=tenant, name="Plant Snap")
    asset = uuid.uuid4()
    row = pick_row(_heldout_ai4i_rows(), failure=True, nth=2)
    snapshot_at = NOW - timedelta(minutes=1)

    # 60 hours of noisy history on every AI4I sensor + spoofed derived channels, all OLDER
    # than the snapshot would be a no-op; make the spoof the *latest* derived readings.
    rng = np.random.default_rng(0)
    history = [
        (snapshot_at - timedelta(hours=h), kind, float(rng.normal(100, 30)), "x")
        for h in range(1, 61)
        for kind in ("air_temperature", "process_temperature", "rotational_speed", "torque",
                     "tool_wear")
    ]  # fmt: skip
    spoof = [
        (snapshot_at + timedelta(seconds=1), "temperature_difference", 9999.0, "K"),
        (snapshot_at + timedelta(seconds=1), "mechanical_power", -5.0, "W"),
    ]
    await _insert_telemetry(
        migrated_timescale_db, tenant_id=tenant, asset_id=asset, rows=[*history, *spoof]
    )
    await _insert_ai4i_snapshot(
        migrated_timescale_db,
        tenant_id=tenant,
        asset_id=asset,
        failure=True,
        nth=2,
        stamp=snapshot_at,
    )

    out = (
        await _snapshots(
            timescale_session_factory, tenant, tenant, [_base(tenant, facility, asset)]
        )
    )[asset]
    assert out.features is not None and out.skip_reason is None
    np.testing.assert_array_equal(vectorize(out.features), _training_vector(row))
    assert out.snapshot_at == snapshot_at

    # contrast: what the OLD windowed path would have fed the model for this very asset
    count_idx = FEATURE_NAMES.index("w30_torque_count")
    assert vectorize(out.features)[count_idx] == 1.0


async def test_incomplete_or_incoherent_snapshots_are_not_scored(
    migrated_main_db: PostgresContainer,
    migrated_timescale_db: PostgresContainer,
    main_session_factory: async_sessionmaker,
    timescale_session_factory: async_sessionmaker,
) -> None:
    tenant = await _make_tenant(migrated_main_db, "Tenant Snap B")
    facility = await _make_facility(migrated_main_db, tenant_id=tenant, name="Plant Snap B")
    row = pick_row(_heldout_ai4i_rows(), failure=False, nth=3)
    no_torque, stale_torque, nothing, good = (uuid.uuid4() for _ in range(4))
    when = NOW - timedelta(minutes=1)

    def rows_for(
        asset: uuid.UUID, shift: dict[str, timedelta] | None = None, drop: str | None = None
    ):
        out = []
        for r in replay_readings(row, asset, when):
            if r.sensor_type == drop:
                continue
            out.append(
                (
                    r.timestamp - (shift or {}).get(r.sensor_type, timedelta()),
                    r.sensor_type,
                    r.value,
                    r.unit,
                )
            )
        return out

    for asset, rows in (
        (no_torque, rows_for(no_torque, drop="torque")),
        (stale_torque, rows_for(stale_torque, shift={"torque": timedelta(minutes=6)})),
        (good, rows_for(good)),
    ):
        await _insert_telemetry(migrated_timescale_db, tenant_id=tenant, asset_id=asset, rows=rows)

    out = await _snapshots(
        timescale_session_factory,
        tenant,
        tenant,
        [_base(tenant, facility, a) for a in (no_torque, stale_torque, nothing, good)],
    )
    assert out[no_torque].features is None
    assert (out[no_torque].skip_reason or "").startswith(SNAPSHOT_MISSING_SENSORS)
    assert "torque" in (out[no_torque].skip_reason or "")
    assert out[stale_torque].skip_reason == SNAPSHOT_INCOHERENT
    assert (out[nothing].skip_reason or "").startswith(SNAPSHOT_MISSING_SENSORS)
    assert out[good].features is not None  # a healthy neighbour is unaffected


async def test_snapshot_query_never_reads_another_tenants_telemetry(
    migrated_main_db: PostgresContainer,
    migrated_timescale_db: PostgresContainer,
    main_session_factory: async_sessionmaker,
    timescale_session_factory: async_sessionmaker,
) -> None:
    tenant_a = await _make_tenant(migrated_main_db, "Tenant Snap C")
    tenant_b = await _make_tenant(migrated_main_db, "Tenant Snap D")
    facility_a = await _make_facility(migrated_main_db, tenant_id=tenant_a, name="Plant C")
    asset = uuid.uuid4()
    await _insert_ai4i_snapshot(migrated_timescale_db, tenant_id=tenant_a, asset_id=asset)
    base = [_base(tenant_a, facility_a, asset)]

    own = await _snapshots(timescale_session_factory, tenant_a, tenant_a, base)
    assert own[asset].features is not None  # sanity: the owner sees it

    # attack 1: tenant B's session asks for A's asset, claiming B's tenant id
    spoof_id = await _snapshots(timescale_session_factory, tenant_b, tenant_b, base)
    # attack 2: tenant B's session claims A's tenant id in the query (RLS must still hide it)
    spoof_claim = await _snapshots(timescale_session_factory, tenant_b, tenant_a, base)
    for result in (spoof_id, spoof_claim):
        assert result[asset].features is None
        assert (result[asset].skip_reason or "").startswith(SNAPSHOT_MISSING_SENSORS)


async def test_score_facility_feeds_the_model_the_snapshot_not_the_windows(
    monkeypatch: pytest.MonkeyPatch,
    migrated_main_db: PostgresContainer,
    migrated_timescale_db: PostgresContainer,
    main_session_factory: async_sessionmaker,
    timescale_session_factory: async_sessionmaker,
) -> None:
    from app.ml.feature_vector import FEATURE_NAMES as NAMES
    from app.services import risk_inference_service
    from app.services.risk_inference_service import score_facility

    seen: list[np.ndarray] = []

    class Recorder:
        def predict_proba(self, X: np.ndarray) -> np.ndarray:
            seen.append(X.copy())
            return np.tile(np.array([0.9, 0.1]), (X.shape[0], 1))

    monkeypatch.setattr(
        risk_inference_service,
        "_resolve_and_load_model",
        lambda model_version: (Recorder(), list(NAMES), "vrec"),
    )
    tenant = await _make_tenant(migrated_main_db, "Tenant Snap E")
    facility = await _make_facility(migrated_main_db, tenant_id=tenant, name="Plant E2")
    asset = await _make_asset(
        migrated_main_db, tenant_id=tenant, facility_id=facility, name="Pump S"
    )
    rng = np.random.default_rng(1)
    snapshot_at = NOW - timedelta(minutes=1)
    history = [
        (snapshot_at - timedelta(hours=h), "torque", float(rng.normal(40, 10)), "Nm")
        for h in range(1, 80)
    ]
    await _insert_telemetry(migrated_timescale_db, tenant_id=tenant, asset_id=asset, rows=history)
    await _insert_ai4i_snapshot(
        migrated_timescale_db, tenant_id=tenant, asset_id=asset, stamp=snapshot_at
    )

    main = await _scoped(main_session_factory, tenant)
    ts = await _scoped(timescale_session_factory, tenant)
    try:
        saved = await score_facility(main, ts, tenant_id=tenant, facility_id=facility)
    finally:
        await main.close()
        await ts.close()

    assert len(saved) == 1 and len(seen) == 1
    vec = seen[0][0]
    assert vec[NAMES.index("w30_torque_count")] == 1.0  # one reading, as in training
    assert vec[NAMES.index("w30_torque_stddev")] == 0.0
    assert vec[NAMES.index("w365_torque_anomaly_count")] == 0.0
    # the explainability factors are still computed from the real 80-reading history, so they
    # show window anomalies even though the model input does not
    assert saved[0].factors_json["torque_30d_anomaly_count"] > 0
    assert saved[0].factors_json["scored_on_snapshot_at"] == snapshot_at.isoformat()
