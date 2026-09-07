import math
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.requests.telemetry import SensorReadingIn, TelemetryIngestRequest

VALID_READING = {
    "asset_id": str(uuid.uuid4()),
    "sensor_type": "temperature",
    "value": 42.5,
    "unit": "celsius",
    "timestamp": datetime.now(UTC).isoformat(),
}


def test_valid_reading_parses() -> None:
    reading = SensorReadingIn.model_validate(VALID_READING)
    assert reading.value == 42.5


@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
def test_non_finite_value_rejected(bad_value: float) -> None:
    with pytest.raises(ValidationError):
        SensorReadingIn.model_validate({**VALID_READING, "value": bad_value})


def test_empty_readings_list_rejected() -> None:
    with pytest.raises(ValidationError):
        TelemetryIngestRequest.model_validate({"readings": []})


def test_oversized_readings_list_rejected() -> None:
    with pytest.raises(ValidationError):
        TelemetryIngestRequest.model_validate({"readings": [VALID_READING] * 1001})


def test_max_size_readings_list_accepted() -> None:
    request = TelemetryIngestRequest.model_validate({"readings": [VALID_READING] * 1000})
    assert len(request.readings) == 1000


def test_missing_sensor_type_rejected() -> None:
    reading = dict(VALID_READING)
    del reading["sensor_type"]
    with pytest.raises(ValidationError):
        SensorReadingIn.model_validate(reading)
