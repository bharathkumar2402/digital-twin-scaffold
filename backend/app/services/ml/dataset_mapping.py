"""Dataset-to-schema mapping service for AI4I 2020 Predictive Maintenance data.

Maps external raw dataset columns onto this project's database schemas:
- Telemetry measurements -> `sensor_readings` (asset_id, sensor_type, value, unit, timestamp)
- Failure indicators -> `risk_scores` (asset_id, score, model_version, factors, computed_at)

Preserves domain units, records contributing failure factors, and produces
schema-compatible records for training, validation, and simulated telemetry ingest.
"""

from __future__ import annotations

import csv
import math
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.schemas.ml.risk_score import RiskScoreResult
from app.schemas.requests.telemetry import SensorReadingIn

DATASET_NAME: str = "AI4I 2020 Predictive Maintenance Dataset"
DATASET_VERSION: str = "ai4i2020_v1"

# Deterministic namespace UUID for AI4I 2020 asset identity mapping
AI4I_ASSET_NAMESPACE: uuid.UUID = uuid.UUID("a1412020-0000-4000-8000-000000000000")

# Canonical sensor types
SENSOR_AIR_TEMP_K: str = "air_temperature"
SENSOR_AIR_TEMP_C: str = "air_temperature_c"
SENSOR_PROC_TEMP_K: str = "process_temperature"
SENSOR_PROC_TEMP_C: str = "process_temperature_c"
SENSOR_ROTATIONAL_SPEED: str = "rotational_speed"
SENSOR_TORQUE: str = "torque"
SENSOR_TOOL_WEAR: str = "tool_wear"
SENSOR_TEMP_DIFF: str = "temperature_difference"
SENSOR_MECHANICAL_POWER: str = "mechanical_power"

FAILURE_MODE_KEYS: tuple[str, ...] = ("TWF", "HDF", "PWF", "OSF", "RNF")


@dataclass(frozen=True)
class MappedSensorReading:
    """A single sensor measurement mapped onto the `sensor_readings` schema."""

    asset_id: uuid.UUID
    sensor_type: str
    value: float
    unit: str
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": str(self.asset_id),
            "sensor_type": self.sensor_type,
            "value": self.value,
            "unit": self.unit,
            "timestamp": self.timestamp.isoformat(),
        }

    def to_schema_in(self) -> SensorReadingIn:
        """Convert into API request schema `SensorReadingIn`."""
        return SensorReadingIn(
            asset_id=self.asset_id,
            sensor_type=self.sensor_type,
            value=self.value,
            unit=self.unit,
            timestamp=self.timestamp,
        )


@dataclass(frozen=True)
class MappedRiskScoreRecord:
    """A risk assessment score mapped onto the `risk_scores` schema."""

    asset_id: uuid.UUID
    score: float  # 0.0 to 100.0
    model_version: str
    factors: dict[str, float | int | str | None]
    computed_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": str(self.asset_id),
            "score": self.score,
            "model_version": self.model_version,
            "factors": self.factors,
            "computed_at": self.computed_at.isoformat(),
        }

    def to_risk_score_result(
        self, tenant_id: uuid.UUID, facility_id: uuid.UUID
    ) -> RiskScoreResult:
        """Convert into Pydantic-validated `RiskScoreResult`."""
        return RiskScoreResult(
            asset_id=self.asset_id,
            tenant_id=tenant_id,
            facility_id=facility_id,
            score=self.score,
            model_version=self.model_version,
            factors=self.factors,
            computed_at=self.computed_at,
        )


@dataclass(frozen=True)
class MappedMachineRecord:
    """Complete mapped observation combining telemetry and risk assessment."""

    udi: int
    product_id: str
    product_type: str
    asset_id: uuid.UUID
    timestamp: datetime
    is_failure: bool
    failure_modes: list[str]
    readings: list[MappedSensorReading]
    risk_score: MappedRiskScoreRecord

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def deterministic_asset_id(product_id: str) -> uuid.UUID:
    """Derive a reproducible UUID for an asset based on its Product ID."""
    return uuid.uuid5(AI4I_ASSET_NAMESPACE, product_id.strip())


def kelvin_to_celsius(k: float) -> float:
    """Convert Kelvin to Celsius rounded to 2 decimal places."""
    return round(k - 273.15, 2)


def calculate_mechanical_power(torque_nm: float, speed_rpm: float) -> float:
    """Calculate mechanical power: P = Torque [Nm] * Speed [rad/s] -> Watts."""
    rad_s = speed_rpm * (2.0 * math.pi / 60.0)
    return round(torque_nm * rad_s, 2)


def map_ai4i2020_row(
    row: dict[str, str],
    base_timestamp: datetime | None = None,
    interval_seconds: int = 60,
    include_celsius: bool = True,
    model_version: str = DATASET_VERSION,
) -> MappedMachineRecord:
    """Map one raw AI4I 2020 CSV row onto internal schemas.

    Args:
        row: Key-value dictionary from ai4i2020.csv.
        base_timestamp: Starting chronological time for synthetic sequence.
        interval_seconds: Step interval between sequential observations.
        include_celsius: Whether to also emit Celsius readings alongside Kelvin.
        model_version: Version identifier string for the dataset / model provenance.

    Returns:
        MappedMachineRecord with structured readings and risk score.
    """
    udi_str = row.get("UDI") or row.get("\ufeffUDI") or "0"
    udi = int(udi_str)
    product_id = row["Product ID"].strip()
    product_type = row["Type"].strip()

    asset_id = deterministic_asset_id(product_id)

    if base_timestamp is None:
        base_timestamp = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)

    # Chronological offset based on observation sequence UDI
    timestamp = base_timestamp + timedelta(seconds=(max(udi - 1, 0) * interval_seconds))

    # Parse continuous physical sensors
    air_temp_k = float(row["Air temperature [K]"])
    proc_temp_k = float(row["Process temperature [K]"])
    speed_rpm = float(row["Rotational speed [rpm]"])
    torque_nm = float(row["Torque [Nm]"])
    tool_wear_min = float(row["Tool wear [min]"])

    # Derived physics
    temp_diff_k = round(proc_temp_k - air_temp_k, 2)
    power_watts = calculate_mechanical_power(torque_nm, speed_rpm)

    # Parse failure labels
    machine_failure_flag = int(row["Machine failure"])
    is_failure = machine_failure_flag == 1

    active_modes: list[str] = [
        mode for mode in FAILURE_MODE_KEYS if int(row.get(mode, "0")) == 1
    ]

    # Build sensor readings
    readings: list[MappedSensorReading] = [
        MappedSensorReading(
            asset_id=asset_id,
            sensor_type=SENSOR_AIR_TEMP_K,
            value=air_temp_k,
            unit="K",
            timestamp=timestamp,
        ),
        MappedSensorReading(
            asset_id=asset_id,
            sensor_type=SENSOR_PROC_TEMP_K,
            value=proc_temp_k,
            unit="K",
            timestamp=timestamp,
        ),
        MappedSensorReading(
            asset_id=asset_id,
            sensor_type=SENSOR_ROTATIONAL_SPEED,
            value=speed_rpm,
            unit="rpm",
            timestamp=timestamp,
        ),
        MappedSensorReading(
            asset_id=asset_id,
            sensor_type=SENSOR_TORQUE,
            value=torque_nm,
            unit="Nm",
            timestamp=timestamp,
        ),
        MappedSensorReading(
            asset_id=asset_id,
            sensor_type=SENSOR_TOOL_WEAR,
            value=tool_wear_min,
            unit="min",
            timestamp=timestamp,
        ),
        MappedSensorReading(
            asset_id=asset_id,
            sensor_type=SENSOR_TEMP_DIFF,
            value=temp_diff_k,
            unit="K",
            timestamp=timestamp,
        ),
        MappedSensorReading(
            asset_id=asset_id,
            sensor_type=SENSOR_MECHANICAL_POWER,
            value=power_watts,
            unit="W",
            timestamp=timestamp,
        ),
    ]

    if include_celsius:
        readings.append(
            MappedSensorReading(
                asset_id=asset_id,
                sensor_type=SENSOR_AIR_TEMP_C,
                value=kelvin_to_celsius(air_temp_k),
                unit="C",
                timestamp=timestamp,
            )
        )
        readings.append(
            MappedSensorReading(
                asset_id=asset_id,
                sensor_type=SENSOR_PROC_TEMP_C,
                value=kelvin_to_celsius(proc_temp_k),
                unit="C",
                timestamp=timestamp,
            )
        )

    # Risk score: 100.0 indicates full failure, 0.0 indicates normal operation
    risk_score_value = 100.0 if is_failure else 0.0

    # Detailed audit factors preserved in factors_json
    factors: dict[str, float | int | str | None] = {
        "dataset_name": DATASET_NAME,
        "product_id": product_id,
        "product_type": product_type,
        "machine_failure": machine_failure_flag,
        "failure_modes": ",".join(active_modes) if active_modes else "none",
        "twf": int(row.get("TWF", "0")),
        "hdf": int(row.get("HDF", "0")),
        "pwf": int(row.get("PWF", "0")),
        "osf": int(row.get("OSF", "0")),
        "rnf": int(row.get("RNF", "0")),
        "air_temperature_k": air_temp_k,
        "process_temperature_k": proc_temp_k,
        "temperature_difference_k": temp_diff_k,
        "rotational_speed_rpm": speed_rpm,
        "torque_nm": torque_nm,
        "tool_wear_min": tool_wear_min,
        "mechanical_power_w": power_watts,
    }

    risk_record = MappedRiskScoreRecord(
        asset_id=asset_id,
        score=risk_score_value,
        model_version=model_version,
        factors=factors,
        computed_at=timestamp,
    )

    return MappedMachineRecord(
        udi=udi,
        product_id=product_id,
        product_type=product_type,
        asset_id=asset_id,
        timestamp=timestamp,
        is_failure=is_failure,
        failure_modes=active_modes,
        readings=readings,
        risk_score=risk_record,
    )


def map_ai4i2020_dataset(
    csv_path: Path | str,
    base_timestamp: datetime | None = None,
    interval_seconds: int = 60,
    include_celsius: bool = True,
    model_version: str = DATASET_VERSION,
) -> list[MappedMachineRecord]:
    """Map full AI4I 2020 CSV dataset into internal schema records."""
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"AI4I 2020 CSV not found at: {path}")

    with path.open(mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        records = [
            map_ai4i2020_row(
                row=row,
                base_timestamp=base_timestamp,
                interval_seconds=interval_seconds,
                include_celsius=include_celsius,
                model_version=model_version,
            )
            for row in reader
        ]

    return records


def extract_all_sensor_readings(
    records: list[MappedMachineRecord],
) -> list[MappedSensorReading]:
    """Extract flat list of all sensor readings across all mapped records."""
    all_readings: list[MappedSensorReading] = []
    for r in records:
        all_readings.extend(r.readings)
    return all_readings


def extract_all_risk_scores(
    records: list[MappedMachineRecord],
) -> list[MappedRiskScoreRecord]:
    """Extract flat list of all risk score records across all mapped records."""
    return [r.risk_score for r in records]