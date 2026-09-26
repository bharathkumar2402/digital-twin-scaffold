"""Unit tests for dataset-to-schema mapping service."""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.schemas.ml.risk_score import RiskScoreResult
from app.schemas.requests.telemetry import SensorReadingIn
from app.services.ml.dataset_mapping import (
    DATASET_NAME,
    DATASET_VERSION,
    SENSOR_AIR_TEMP_C,
    SENSOR_AIR_TEMP_K,
    SENSOR_MECHANICAL_POWER,
    SENSOR_PROC_TEMP_C,
    SENSOR_PROC_TEMP_K,
    SENSOR_ROTATIONAL_SPEED,
    SENSOR_TEMP_DIFF,
    SENSOR_TOOL_WEAR,
    SENSOR_TORQUE,
    calculate_mechanical_power,
    deterministic_asset_id,
    extract_all_risk_scores,
    extract_all_sensor_readings,
    kelvin_to_celsius,
    map_ai4i2020_dataset,
    map_ai4i2020_row,
)

RAW_DATA_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "raw" / "ai4i2020.csv"
)


def test_deterministic_asset_id() -> None:
    """Verify that product ID hashing produces consistent, collision-free UUIDs."""
    uid1 = deterministic_asset_id("M14860")
    uid2 = deterministic_asset_id("M14860")
    uid3 = deterministic_asset_id("L47181")

    assert uid1 == uid2
    assert uid1 != uid3
    assert isinstance(uid1, uuid.UUID)


def test_kelvin_to_celsius() -> None:
    """Verify temperature conversion accuracy."""
    assert kelvin_to_celsius(273.15) == 0.0
    assert kelvin_to_celsius(298.15) == 25.0
    assert kelvin_to_celsius(300.0) == 26.85


def test_calculate_mechanical_power() -> None:
    """Verify mechanical power calculation: P = Torque * (Speed * 2 * pi / 60)."""
    # 40.0 Nm at 1500 rpm -> ~6283.19 W
    power = calculate_mechanical_power(40.0, 1500.0)
    assert 6280.0 <= power <= 6285.0


def test_map_single_normal_row() -> None:
    """Verify mapping of a standard normal observation record."""
    raw_row = {
        "UDI": "1",
        "Product ID": "M14860",
        "Type": "M",
        "Air temperature [K]": "298.1",
        "Process temperature [K]": "308.6",
        "Rotational speed [rpm]": "1551",
        "Torque [Nm]": "42.8",
        "Tool wear [min]": "0",
        "Machine failure": "0",
        "TWF": "0",
        "HDF": "0",
        "PWF": "0",
        "OSF": "0",
        "RNF": "0",
    }
    base_time = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    record = map_ai4i2020_row(raw_row, base_timestamp=base_time, interval_seconds=60)

    assert record.udi == 1
    assert record.product_id == "M14860"
    assert record.product_type == "M"
    assert record.is_failure is False
    assert record.failure_modes == []
    assert record.timestamp == base_time

    # Sensor readings check (7 base + 2 celsius = 9 total)
    assert len(record.readings) == 9
    types = {r.sensor_type: r for r in record.readings}

    assert types[SENSOR_AIR_TEMP_K].value == 298.1
    assert types[SENSOR_AIR_TEMP_K].unit == "K"
    assert types[SENSOR_AIR_TEMP_C].value == 24.95
    assert types[SENSOR_AIR_TEMP_C].unit == "C"

    assert types[SENSOR_PROC_TEMP_K].value == 308.6
    assert types[SENSOR_PROC_TEMP_C].value == 35.45
    assert types[SENSOR_TEMP_DIFF].value == 10.5
    assert types[SENSOR_ROTATIONAL_SPEED].value == 1551.0
    assert types[SENSOR_TORQUE].value == 42.8
    assert types[SENSOR_TOOL_WEAR].value == 0.0
    assert types[SENSOR_MECHANICAL_POWER].value > 0.0

    # Ensure all readings validate through Pydantic SensorReadingIn schema
    for r in record.readings:
        schema_in = r.to_schema_in()
        assert isinstance(schema_in, SensorReadingIn)
        assert schema_in.value == r.value

    # Risk score check
    risk = record.risk_score
    assert risk.score == 0.0
    assert risk.model_version == DATASET_VERSION
    assert risk.factors["dataset_name"] == DATASET_NAME
    assert risk.factors["machine_failure"] == 0
    assert risk.factors["failure_modes"] == "none"

    # Ensure risk score validates through Pydantic RiskScoreResult schema
    tenant_id = uuid.uuid4()
    facility_id = uuid.uuid4()
    result = risk.to_risk_score_result(tenant_id=tenant_id, facility_id=facility_id)
    assert isinstance(result, RiskScoreResult)
    assert result.score == 0.0


def test_map_failure_row_with_factors() -> None:
    """Verify mapping of an observation with multiple active failure modes."""
    raw_row = {
        "UDI": "161",
        "Product ID": "L47340",
        "Type": "L",
        "Air temperature [K]": "298.3",
        "Process temperature [K]": "308.1",
        "Rotational speed [rpm]": "1412",
        "Torque [Nm]": "52.3",
        "Tool wear [min]": "172",
        "Machine failure": "1",
        "TWF": "0",
        "HDF": "1",
        "PWF": "1",
        "OSF": "0",
        "RNF": "0",
    }
    record = map_ai4i2020_row(raw_row)

    assert record.is_failure is True
    assert set(record.failure_modes) == {"HDF", "PWF"}
    assert record.risk_score.score == 100.0
    assert record.risk_score.factors["hdf"] == 1
    assert record.risk_score.factors["pwf"] == 1
    assert record.risk_score.factors["twf"] == 0
    assert "HDF" in str(record.risk_score.factors["failure_modes"])


def test_map_full_dataset() -> None:
    """Verify mapping across all 10,000 records in ai4i2020.csv."""
    assert RAW_DATA_PATH.is_file(), f"Dataset file not found: {RAW_DATA_PATH}"
    records = map_ai4i2020_dataset(RAW_DATA_PATH, interval_seconds=30)

    assert len(records) == 10000

    readings = extract_all_sensor_readings(records)
    # 10,000 * 9 channels = 90,000 readings
    assert len(readings) == 90000

    risk_scores = extract_all_risk_scores(records)
    assert len(risk_scores) == 10000

    failures = [r for r in records if r.is_failure]
    # Exact ground truth from AI4I 2020: 339 failures
    assert len(failures) == 339

    # Check timestamps are monotonically advancing
    for i in range(1, 10):
        assert records[i].timestamp > records[i - 1].timestamp


def test_nonexistent_csv_raises_error(tmp_path: Path) -> None:
    """Verify FileNotFoundError is raised when target CSV does not exist."""
    missing = tmp_path / "does_not_exist.csv"
    with pytest.raises(FileNotFoundError, match="not found"):
        map_ai4i2020_dataset(missing)
