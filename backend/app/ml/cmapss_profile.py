"""Data profiling for the NASA C-MAPSS turbofan run-to-failure dataset.

C-MAPSS (Saxena & Goebel, 2008) has four subsets (FD001-FD004). Each ships a train
file of run-to-failure trajectories, a test file of trajectories truncated before
failure, and a RUL file giving the true remaining useful life at each test engine's
last cycle. This module validates those files and computes the profile that task 3b.2
(RUL feature pipeline) and task 3b.3 (RUL training) are grounded in.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from app.ml.dataset_profile import NumericFeatureStats

SUBSETS: tuple[str, ...] = ("FD001", "FD002", "FD003", "FD004")
N_SENSORS = 21
COLUMNS: tuple[str, ...] = (
    "unit",
    "cycle",
    "op_setting_1",
    "op_setting_2",
    "op_setting_3",
    *(f"sensor_{i}" for i in range(1, N_SENSORS + 1)),
)
SENSOR_COLUMNS: tuple[str, ...] = COLUMNS[5:]
CONSTANT_STD_THRESHOLD = 1e-9

DATASET_NAME = "NASA C-MAPSS Turbofan Engine Degradation Simulation"
SOURCE = "NASA Ames Prognostics Data Repository"
CITATION = (
    "Saxena, A., Goebel, K., Simon, D., & Eklund, N. (2008). Damage Propagation Modeling "
    "for Aircraft Engine Run-to-Failure Simulation. International Conference on "
    "Prognostics and Health Management (PHM08)."
)


def train_file(subset: str) -> str:
    return f"train_{subset}.txt"


def test_file(subset: str) -> str:
    return f"test_{subset}.txt"


def rul_file(subset: str) -> str:
    return f"RUL_{subset}.txt"


def required_files() -> list[str]:
    return [f(s) for s in SUBSETS for f in (train_file, test_file, rul_file)]


def parse_cmapss_table(text: str, source: str = "<memory>") -> np.ndarray:
    """Parse a whitespace-delimited C-MAPSS table; reject ragged rows and non-finite values."""
    rows: list[list[float]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != len(COLUMNS):
            raise ValueError(
                f"{source}:{line_no}: expected {len(COLUMNS)} columns, got {len(parts)}"
            )
        try:
            rows.append([float(p) for p in parts])
        except ValueError as exc:
            raise ValueError(f"{source}:{line_no}: non-numeric value ({exc})") from exc
    if not rows:
        raise ValueError(f"{source}: file contains no data rows")
    table = np.asarray(rows, dtype=np.float64)
    if not np.isfinite(table).all():
        raise ValueError(f"{source}: contains NaN or infinite values")
    return table


def parse_rul_vector(text: str, source: str = "<memory>") -> np.ndarray:
    """Parse a RUL file: one non-negative number per line."""
    try:
        values = [float(x) for x in text.split()]
    except ValueError as exc:
        raise ValueError(f"{source}: non-numeric RUL value ({exc})") from exc
    if not values:
        raise ValueError(f"{source}: file contains no RUL values")
    arr = np.asarray(values, dtype=np.float64)
    if not np.isfinite(arr).all() or (arr < 0).any():
        raise ValueError(f"{source}: RUL values must be finite and non-negative")
    return arr


def count_cycle_gaps(table: np.ndarray) -> int:
    """Count engines whose cycle numbers are not exactly 1..n without gaps or repeats."""
    units = table[:, 0].astype(int)
    cycles = table[:, 1].astype(int)
    bad = 0
    for unit in np.unique(units):
        c = cycles[units == unit]
        if not np.array_equal(c, np.arange(1, len(c) + 1)):
            bad += 1
    return bad


def count_operating_conditions(table: np.ndarray) -> int:
    """Count distinct operating-condition clusters.

    Settings 1 (altitude) and 3 (throttle) are rounded to integers to remove noise.
    Setting 2 (Mach) is excluded: it is fully determined by setting 1, and its noisy
    values straddle a rounding boundary and would split one condition into two.
    """
    settings = np.round(table[:, [2, 4]], decimals=0)
    return len({tuple(row) for row in settings.tolist()})


def _cycle_lengths(table: np.ndarray) -> np.ndarray:
    units = table[:, 0].astype(int)
    _, counts = np.unique(units, return_counts=True)
    return counts


def _stats(name: str, values: np.ndarray) -> NumericFeatureStats:
    return NumericFeatureStats(
        name=name,
        unit="",
        count=int(values.size),
        min=float(values.min()),
        p25=float(np.percentile(values, 25)),
        median=float(np.median(values)),
        p75=float(np.percentile(values, 75)),
        max=float(values.max()),
        mean=float(values.mean()),
        std=float(values.std()),
    )


@dataclass
class SubsetProfile:
    """Profile of one C-MAPSS subset (train + test + RUL)."""

    name: str
    train_units: int
    train_rows: int
    test_units: int
    test_rows: int
    train_life_min: int
    train_life_median: float
    train_life_max: int
    test_rul_min: float
    test_rul_median: float
    test_rul_max: float
    operating_conditions: int
    cycle_gap_units: int
    constant_sensors: list[str]
    sensor_stats: list[NumericFeatureStats] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CmapssProfile:
    """Profile of the full C-MAPSS dataset with file checksums for later change detection."""

    dataset_name: str
    source: str
    citation: str
    subsets: list[SubsetProfile]
    sha256: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def format_markdown_profile(self) -> str:
        lines = [
            f"**Dataset:** {self.dataset_name} ({self.source})",
            "",
            "| Subset | Train units | Train rows | Test units | Op. conditions "
            "| Train life min/median/max | Test RUL min/median/max | Constant sensors |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for s in self.subsets:
            const = ", ".join(s.constant_sensors) or "none"
            lines.append(
                f"| {s.name} | {s.train_units} | {s.train_rows} | {s.test_units} "
                f"| {s.operating_conditions} "
                f"| {s.train_life_min}/{s.train_life_median:g}/{s.train_life_max} "
                f"| {s.test_rul_min:g}/{s.test_rul_median:g}/{s.test_rul_max:g} | {const} |"
            )
        return "\n".join(lines)


def profile_subset(
    name: str, train: np.ndarray, test: np.ndarray, rul: np.ndarray
) -> SubsetProfile:
    """Profile one subset, validating that the test and RUL files agree."""
    test_units = int(np.unique(test[:, 0]).size)
    if rul.size != test_units:
        raise ValueError(
            f"{name}: RUL file has {rul.size} entries but test file has {test_units} engines"
        )
    lives = _cycle_lengths(train)
    sensor_idx = {c: i for i, c in enumerate(COLUMNS)}
    constant = [
        c for c in SENSOR_COLUMNS if float(train[:, sensor_idx[c]].std()) < CONSTANT_STD_THRESHOLD
    ]
    return SubsetProfile(
        name=name,
        train_units=int(lives.size),
        train_rows=int(train.shape[0]),
        test_units=test_units,
        test_rows=int(test.shape[0]),
        train_life_min=int(lives.min()),
        train_life_median=float(np.median(lives)),
        train_life_max=int(lives.max()),
        test_rul_min=float(rul.min()),
        test_rul_median=float(np.median(rul)),
        test_rul_max=float(rul.max()),
        operating_conditions=count_operating_conditions(train),
        cycle_gap_units=count_cycle_gaps(train) + count_cycle_gaps(test),
        constant_sensors=constant,
        sensor_stats=[_stats(c, train[:, sensor_idx[c]]) for c in SENSOR_COLUMNS],
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_and_profile_cmapss(data_dir: Path | str) -> CmapssProfile:
    """Load all twelve C-MAPSS files from `data_dir` and profile them."""
    root = Path(data_dir)
    missing = [f for f in required_files() if not (root / f).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing C-MAPSS files in {root}: {missing}")

    subsets: list[SubsetProfile] = []
    for name in SUBSETS:
        tr, te, ru = root / train_file(name), root / test_file(name), root / rul_file(name)
        subsets.append(
            profile_subset(
                name,
                parse_cmapss_table(tr.read_text(), str(tr)),
                parse_cmapss_table(te.read_text(), str(te)),
                parse_rul_vector(ru.read_text(), str(ru)),
            )
        )
    return CmapssProfile(
        dataset_name=DATASET_NAME,
        source=SOURCE,
        citation=CITATION,
        subsets=subsets,
        sha256={f: _sha256(root / f) for f in required_files()},
    )
