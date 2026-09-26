"""Data profiling and statistical analysis for industrial datasets.

Provides structured schema inspection, failure class balance calculation,
and continuous sensor range profiling for the AI4I 2020 Predictive Maintenance
dataset (UCI ML Repository) and related real industrial telemetry.
"""

from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class NumericFeatureStats:
    """Statistical distribution summary for a continuous numerical sensor feature."""

    name: str
    unit: str
    count: int
    min: float
    p25: float
    median: float
    p75: float
    max: float
    mean: float
    std: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FailureModeStats:
    """Count and proportion summary for a labeled failure mode."""

    code: str
    name: str
    count: int
    rate_percent: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProductTypeStats:
    """Count and proportion summary for an equipment/product type."""

    type_code: str
    name: str
    count: int
    percent: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DatasetProfile:
    """Comprehensive statistical profile of an industrial predictive maintenance dataset."""

    dataset_name: str
    source: str
    citation: str
    license_name: str
    access_date: str
    total_rows: int
    total_columns: int
    columns: list[str]
    null_counts: dict[str, int]
    machine_failure_count: int
    machine_failure_rate_percent: float
    normal_count: int
    normal_rate_percent: float
    failure_modes: dict[str, FailureModeStats]
    multi_failure_count: int
    unspecified_failure_count: int
    product_types: dict[str, ProductTypeStats]
    features: dict[str, NumericFeatureStats]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_name": self.dataset_name,
            "source": self.source,
            "citation": self.citation,
            "license": self.license_name,
            "access_date": self.access_date,
            "total_rows": self.total_rows,
            "total_columns": self.total_columns,
            "columns": self.columns,
            "null_counts": self.null_counts,
            "machine_failure_count": self.machine_failure_count,
            "machine_failure_rate_percent": round(self.machine_failure_rate_percent, 4),
            "normal_count": self.normal_count,
            "normal_rate_percent": round(self.normal_rate_percent, 4),
            "failure_modes": {k: v.to_dict() for k, v in self.failure_modes.items()},
            "multi_failure_count": self.multi_failure_count,
            "unspecified_failure_count": self.unspecified_failure_count,
            "product_types": {k: v.to_dict() for k, v in self.product_types.items()},
            "features": {k: v.to_dict() for k, v in self.features.items()},
        }

    def format_markdown_profile(self) -> str:
        """Format the data profile as GitHub-flavored Markdown for docs/DATASETS.md."""
        fail_p = f"{self.machine_failure_rate_percent:.2f}%"
        norm_p = f"{self.normal_rate_percent:.2f}%"
        lines: list[str] = [
            f"- **Dataset used:** {self.dataset_name} ({self.source})",
            f"- **Citation:** {self.citation}",
            f"- **License / access:** {self.license_name}",
            f"- **Version / access date:** {self.access_date}",
            (
                f"- **Row count:** {self.total_rows:,} rows across {self.total_columns} columns "
                "(0 missing values across all columns)"
            ),
            "- **Failure class balance:**",
            (f"  - **Total Failures (`Machine failure == 1`):** " 
                f"{self.machine_failure_count:,} ({fail_p})"),
            f"  - **Normal Operation (`Machine failure == 0`):** {self.normal_count:,} ({norm_p})",
            "  - **Individual Failure Modes:**",
        ]

        for code, f_stat in self.failure_modes.items():
            lines.append(
                f"    - `{code}` ({f_stat.name}): {f_stat.count} incidents "
                f"({f_stat.rate_percent:.2f}%)"
            )

        lines.extend(
            [
                (
                    f"  - **Overlapping / Compound Failures:** {self.multi_failure_count} records "
                    "exhibit >1 concurrent failure mode"
                ),
                (
                    f"  - **Unspecified Failures:** {self.unspecified_failure_count} records "
                    "failed without meeting the 5 specific sub-mode thresholds"
                ),
                "- **Product Variant Distribution:**",
            ]
        )

        for p_code, p_stat in self.product_types.items():
            lines.append(
                f"  - `{p_code}` ({p_stat.name}): {p_stat.count:,} units ({p_stat.percent:.2f}%)"
            )

        lines.extend(
            [
                (
                    "- **Feature ranges & operational statistics "
                    "(for simulator calibration in task 3.4):**"
                ),
                "  | Feature | Unit | Min | 25% | Median | 75% | Max | Mean | Std Dev |",
                "  |---|---|---|---|---|---|---|---|---|",
            ]
        )

        for _name, s in self.features.items():
            row_str = (
                f"  | {s.name} | {s.unit} | {s.min:.2f} | {s.p25:.2f} | {s.median:.2f} | "
                f"{s.p75:.2f} | {s.max:.2f} | {s.mean:.2f} | {s.std:.2f} |"
            )
            lines.append(row_str)

        return "\n".join(lines)


FAILURE_MODE_NAMES: dict[str, str] = {
    "TWF": "Tool Wear Failure",
    "HDF": "Heat Dissipation Failure",
    "PWF": "Power Failure",
    "OSF": "Overstrain Failure",
    "RNF": "Random Failure",
}

PRODUCT_TYPE_NAMES: dict[str, str] = {
    "L": "Low quality variant (60% target allocation)",
    "M": "Medium quality variant (30% target allocation)",
    "H": "High quality variant (10% target allocation)",
}

FEATURE_CONFIG: list[tuple[str, str, str]] = [
    ("Air temperature [K]", "Air temperature", "K"),
    ("Process temperature [K]", "Process temperature", "K"),
    ("Rotational speed [rpm]", "Rotational speed", "rpm"),
    ("Torque [Nm]", "Torque", "Nm"),
    ("Tool wear [min]", "Tool wear", "min"),
]

DEFAULT_CITATION = (
    "Matzka, S. (2020). Explainable Artificial Intelligence for Predictive "
    "Maintenance Applications. Third International Conference on Industrial "
    "Cyber-Physical Systems (ICPS)."
)


def profile_ai4i2020_data(
    rows: list[dict[str, str]],
    dataset_name: str = "AI4I 2020 Predictive Maintenance Dataset",
    source: str = "UCI Machine Learning Repository (ID: 601)",
    citation: str = DEFAULT_CITATION,
    license_name: str = "Creative Commons Attribution 4.0 International (CC BY 4.0)",
    access_date: str = "September 2026",
) -> DatasetProfile:
    """Compute empirical statistics and distributions from raw AI4I 2020 records."""
    total_rows = len(rows)
    if total_rows == 0:
        raise ValueError("Cannot profile an empty dataset.")

    columns = list(rows[0].keys())

    # Check nulls
    null_counts: dict[str, int] = {col: 0 for col in columns}
    for row in rows:
        for col in columns:
            val = row.get(col, "")
            if val is None or val.strip() == "" or val.lower() == "nan":
                null_counts[col] += 1

    # Class balance
    failures = np.array([int(r["Machine failure"]) for r in rows], dtype=np.int32)
    failure_count = int(np.sum(failures))
    normal_count = total_rows - failure_count
    failure_rate = (failure_count / total_rows) * 100.0
    normal_rate = (normal_count / total_rows) * 100.0

    # Failure modes
    failure_mode_stats: dict[str, FailureModeStats] = {}
    mode_matrix = []
    for code, full_name in FAILURE_MODE_NAMES.items():
        arr = np.array([int(r[code]) for r in rows], dtype=np.int32)
        count = int(np.sum(arr))
        rate = (count / total_rows) * 100.0
        failure_mode_stats[code] = FailureModeStats(
            code=code,
            name=full_name,
            count=count,
            rate_percent=rate,
        )
        mode_matrix.append(arr)

    # Compound & unspecified failure counts
    mode_sum_per_row = np.sum(np.column_stack(mode_matrix), axis=1)
    multi_failure_count = int(np.sum(mode_sum_per_row > 1))
    unspecified_failure_count = int(np.sum((failures == 1) & (mode_sum_per_row == 0)))

    # Product Types
    types = [r["Type"] for r in rows]
    product_type_stats: dict[str, ProductTypeStats] = {}
    for code in ["L", "M", "H"]:
        c = types.count(code)
        pct = (c / total_rows) * 100.0
        product_type_stats[code] = ProductTypeStats(
            type_code=code,
            name=PRODUCT_TYPE_NAMES.get(code, code),
            count=c,
            percent=pct,
        )

    # Numerical feature stats
    feature_stats: dict[str, NumericFeatureStats] = {}
    for raw_col, display_name, unit in FEATURE_CONFIG:
        vals = np.array([float(r[raw_col]) for r in rows], dtype=np.float64)
        p25, p50, p75 = np.percentile(vals, [25, 50, 75])
        feature_stats[raw_col] = NumericFeatureStats(
            name=display_name,
            unit=unit,
            count=total_rows,
            min=float(np.min(vals)),
            p25=float(p25),
            median=float(p50),
            p75=float(p75),
            max=float(np.max(vals)),
            mean=float(np.mean(vals)),
            std=float(np.std(vals)),
        )

    # Derived physical channels:
    # 1. Temperature difference (Process Temp - Air Temp)
    air_temps = np.array([float(r["Air temperature [K]"]) for r in rows], dtype=np.float64)
    proc_temps = np.array([float(r["Process temperature [K]"]) for r in rows], dtype=np.float64)
    temp_diff = proc_temps - air_temps
    td_p25, td_p50, td_p75 = np.percentile(temp_diff, [25, 50, 75])
    feature_stats["Temperature difference [K]"] = NumericFeatureStats(
        name="Temperature difference (Process - Air)",
        unit="K",
        count=total_rows,
        min=float(np.min(temp_diff)),
        p25=float(td_p25),
        median=float(td_p50),
        p75=float(td_p75),
        max=float(np.max(temp_diff)),
        mean=float(np.mean(temp_diff)),
        std=float(np.std(temp_diff)),
    )

    # 2. Power Proxy (Torque [Nm] * Rotational Speed [rad/s]) -> Watts
    torques = np.array([float(r["Torque [Nm]"]) for r in rows], dtype=np.float64)
    rpms = np.array([float(r["Rotational speed [rpm]"]) for r in rows], dtype=np.float64)
    powers_watts = torques * rpms * (2.0 * math.pi / 60.0)
    pw_p25, pw_p50, pw_p75 = np.percentile(powers_watts, [25, 50, 75])
    feature_stats["Power [W]"] = NumericFeatureStats(
        name="Mechanical power (Torque x Speed)",
        unit="W",
        count=total_rows,
        min=float(np.min(powers_watts)),
        p25=float(pw_p25),
        median=float(pw_p50),
        p75=float(pw_p75),
        max=float(np.max(powers_watts)),
        mean=float(np.mean(powers_watts)),
        std=float(np.std(powers_watts)),
    )

    return DatasetProfile(
        dataset_name=dataset_name,
        source=source,
        citation=citation,
        license_name=license_name,
        access_date=access_date,
        total_rows=total_rows,
        total_columns=len(columns),
        columns=columns,
        null_counts=null_counts,
        machine_failure_count=failure_count,
        machine_failure_rate_percent=failure_rate,
        normal_count=normal_count,
        normal_rate_percent=normal_rate,
        failure_modes=failure_mode_stats,
        multi_failure_count=multi_failure_count,
        unspecified_failure_count=unspecified_failure_count,
        product_types=product_type_stats,
        features=feature_stats,
    )


def load_and_profile_ai4i2020(csv_path: Path | str) -> DatasetProfile:
    """Load AI4I 2020 CSV file and extract its complete statistical profile."""
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(f"Dataset CSV not found at: {path}")

    with path.open(mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    return profile_ai4i2020_data(rows)
