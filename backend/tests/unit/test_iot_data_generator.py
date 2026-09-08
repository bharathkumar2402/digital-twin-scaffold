import math
import random
import uuid

import pytest

from scripts.iot_data_generator import (
    MAX_BATCH_SIZE,
    SENSOR_PROFILES,
    ReadingPayload,
    chunk_readings,
    generate_batch,
    generate_reading,
    parse_args,
)


def _dummy_readings(n: int) -> list[ReadingPayload]:
    return [
        ReadingPayload(
            asset_id=str(uuid.uuid4()),
            sensor_type="temperature_c",
            value=float(i),
            unit="C",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        for i in range(n)
    ]


def test_generate_reading_normal_stays_near_baseline() -> None:
    rng = random.Random(0)
    asset_id = uuid.uuid4()
    reading = generate_reading(
        asset_id=asset_id, sensor_type="temperature_c", tick_s=0.0, anomaly=False, rng=rng
    )

    profile = SENSOR_PROFILES["temperature_c"]
    # Bounded by amplitude + a few noise stddevs — never exactly hits an anomaly-sized swing.
    bound = profile.amplitude + 6 * profile.noise_stddev
    assert abs(reading["value"] - profile.baseline) < bound
    assert reading["asset_id"] == str(asset_id)
    assert reading["sensor_type"] == "temperature_c"
    assert reading["unit"] == profile.unit
    assert math.isfinite(reading["value"])


def test_generate_reading_anomaly_exceeds_normal_bound() -> None:
    rng = random.Random(1)
    profile = SENSOR_PROFILES["vibration_mm_s"]
    normal_bound = profile.amplitude + 6 * profile.noise_stddev

    reading = generate_reading(
        asset_id=uuid.uuid4(),
        sensor_type="vibration_mm_s",
        tick_s=0.0,
        anomaly=True,
        rng=rng,
    )

    assert abs(reading["value"] - profile.baseline) > normal_bound
    assert math.isfinite(reading["value"])


@pytest.mark.parametrize("sensor_type", list(SENSOR_PROFILES))
def test_generate_reading_always_finite_across_many_ticks(sensor_type: str) -> None:
    rng = random.Random(42)
    for tick in range(0, 10_000, 137):
        reading = generate_reading(
            asset_id=uuid.uuid4(),
            sensor_type=sensor_type,
            tick_s=float(tick),
            anomaly=rng.random() < 0.5,
            rng=rng,
        )
        assert math.isfinite(reading["value"])


def test_generate_batch_covers_every_asset_and_sensor_type() -> None:
    rng = random.Random(7)
    asset_ids = [uuid.uuid4() for _ in range(3)]
    sensor_types = ["temperature_c", "pressure_kpa"]

    batch = generate_batch(
        asset_ids=asset_ids, sensor_types=sensor_types, tick_s=0.0, anomaly_rate=0.0, rng=rng
    )

    assert len(batch) == len(asset_ids) * len(sensor_types)
    seen_pairs = {(r["asset_id"], r["sensor_type"]) for r in batch}
    expected_pairs = {(str(a), s) for a in asset_ids for s in sensor_types}
    assert seen_pairs == expected_pairs


def test_generate_batch_zero_anomaly_rate_never_spikes() -> None:
    rng = random.Random(3)
    asset_ids = [uuid.uuid4()]
    profile = SENSOR_PROFILES["pressure_kpa"]
    normal_bound = profile.amplitude + 6 * profile.noise_stddev

    batch = generate_batch(
        asset_ids=asset_ids,
        sensor_types=["pressure_kpa"],
        tick_s=0.0,
        anomaly_rate=0.0,
        rng=rng,
    )

    assert abs(batch[0]["value"] - profile.baseline) < normal_bound


def test_chunk_readings_respects_batch_size() -> None:
    readings = _dummy_readings(25)
    chunks = chunk_readings(readings, batch_size=10)

    assert [len(c) for c in chunks] == [10, 10, 5]
    assert [r for c in chunks for r in c] == readings


def test_chunk_readings_caps_at_schema_max_batch_size() -> None:
    readings = _dummy_readings(50)
    chunks = chunk_readings(readings, batch_size=MAX_BATCH_SIZE + 500)

    assert len(chunks) == 1
    assert len(chunks[0]) == 50


def test_parse_args_requires_credentials() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--base-url", "http://localhost:8000"])


def test_parse_args_defaults() -> None:
    tenant_id = uuid.uuid4()
    args = parse_args(
        [
            "--base-url",
            "http://localhost:8000",
            "--tenant-id",
            str(tenant_id),
            "--email",
            "demo@tenant.example",
            "--password",
            "secretpass",
        ]
    )

    assert args.tenant_id == tenant_id
    assert args.num_assets == 5
    assert args.rate == 1.0
    assert set(args.sensor_types) == set(SENSOR_PROFILES)
