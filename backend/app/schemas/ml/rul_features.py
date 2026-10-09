import math

import numpy as np
from pydantic import BaseModel, Field, field_validator, model_validator

from app.ml.cmapss_profile import COLUMNS, SENSOR_COLUMNS


class RulSnapshot(BaseModel):
    """One cycle (sensor snapshot) of an asset, as supplied to the RUL model at serving time."""

    cycle: int = Field(ge=1)
    op_setting_1: float
    op_setting_2: float
    op_setting_3: float
    sensors: dict[str, float]

    @field_validator("op_setting_1", "op_setting_2", "op_setting_3")
    @classmethod
    def _settings_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("operating settings must be finite")
        return value

    @model_validator(mode="after")
    def _sensors_complete_and_finite(self) -> "RulSnapshot":
        missing = [s for s in SENSOR_COLUMNS if s not in self.sensors]
        extra = [s for s in self.sensors if s not in SENSOR_COLUMNS]
        if missing or extra:
            raise ValueError(
                f"sensors must be exactly sensor_1..sensor_21 (missing={missing}, unknown={extra})"
            )
        if not all(math.isfinite(v) for v in self.sensors.values()):
            raise ValueError("sensor values must be finite (not NaN or infinite)")
        return self


class RulWindowIn(BaseModel):
    """A trailing window of consecutive cycles for one asset, oldest first."""

    snapshots: list[RulSnapshot] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _cycles_strictly_increasing(self) -> "RulWindowIn":
        cycles = [s.cycle for s in self.snapshots]
        if any(b <= a for a, b in zip(cycles, cycles[1:], strict=False)):
            raise ValueError("snapshot cycles must be strictly increasing")
        return self

    def to_table(self) -> np.ndarray:
        """Convert to the (n, 26) layout used by `window_features` (unit column = 1)."""
        rows = [
            [1.0, float(s.cycle), s.op_setting_1, s.op_setting_2, s.op_setting_3]
            + [s.sensors[c] for c in SENSOR_COLUMNS]
            for s in self.snapshots
        ]
        table = np.asarray(rows, dtype=np.float64)
        assert table.shape[1] == len(COLUMNS)
        return table
