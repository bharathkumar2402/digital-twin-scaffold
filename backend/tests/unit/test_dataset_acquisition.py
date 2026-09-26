"""Unit tests for dataset acquisition, integrity verification, and data profiling."""

from pathlib import Path

import pytest

from app.ml.dataset_profile import (
    load_and_profile_ai4i2020,
    profile_ai4i2020_data,
)
from scripts.acquire_dataset import (
    DEFAULT_CSV_NAME,
    download_and_extract_ai4i2020,
)

RAW_DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "raw" / DEFAULT_CSV_NAME


def test_ai4i2020_csv_presence_and_row_count() -> None:
    """Assert raw AI4I 2020 CSV file exists and contains exactly 10,000 observations."""
    assert RAW_DATA_PATH.is_file(), f"Dataset file not found at: {RAW_DATA_PATH}"
    profile = load_and_profile_ai4i2020(RAW_DATA_PATH)
    assert profile.total_rows == 10000
    assert profile.total_columns == 14


def test_ai4i2020_schema_and_zero_nulls() -> None:
    """Verify standard UCI column naming and assert zero missing or NaN entries."""
    profile = load_and_profile_ai4i2020(RAW_DATA_PATH)
    expected_cols = [
        "UDI",
        "Product ID",
        "Type",
        "Air temperature [K]",
        "Process temperature [K]",
        "Rotational speed [rpm]",
        "Torque [Nm]",
        "Tool wear [min]",
        "Machine failure",
        "TWF",
        "HDF",
        "PWF",
        "OSF",
        "RNF",
    ]
    for col in expected_cols:
        assert col in profile.columns, f"Expected column {col} missing from schema"
        assert profile.null_counts[col] == 0, f"Column {col} has {profile.null_counts[col]} nulls"


def test_ai4i2020_class_balance_and_failure_modes() -> None:
    """Verify exact target failure counts and individual failure mode breakdown."""
    profile = load_and_profile_ai4i2020(RAW_DATA_PATH)

    # 339 failures out of 10,000 (3.39%)
    assert profile.machine_failure_count == 339
    assert profile.normal_count == 9661
    assert round(profile.machine_failure_rate_percent, 2) == 3.39

    # Individual modes
    assert profile.failure_modes["TWF"].count == 46
    assert profile.failure_modes["HDF"].count == 115
    assert profile.failure_modes["PWF"].count == 95
    assert profile.failure_modes["OSF"].count == 98
    assert profile.failure_modes["RNF"].count == 19

    # Compound and unspecified failures
    assert profile.multi_failure_count == 24
    assert profile.unspecified_failure_count == 9

    # Product Types (60% L, 30% M, 10% H target distribution)
    assert profile.product_types["L"].count == 6000
    assert profile.product_types["M"].count == 2997
    assert profile.product_types["H"].count == 1003


def test_ai4i2020_sensor_feature_ranges() -> None:
    """Assert physical plausibility and bounds for continuous sensor measurements."""
    profile = load_and_profile_ai4i2020(RAW_DATA_PATH)

    air = profile.features["Air temperature [K]"]
    proc = profile.features["Process temperature [K]"]
    speed = profile.features["Rotational speed [rpm]"]
    torque = profile.features["Torque [Nm]"]
    wear = profile.features["Tool wear [min]"]
    tdiff = profile.features["Temperature difference [K]"]
    power = profile.features["Power [W]"]

    # Air temperature [K] (approx 295.3 - 304.5 K)
    assert 295.0 <= air.min <= 296.0
    assert 304.0 <= air.max <= 305.0
    assert 299.5 <= air.mean <= 300.5

    # Process temperature [K] (approx 305.7 - 313.8 K)
    assert 305.0 <= proc.min <= 306.0
    assert 313.0 <= proc.max <= 314.0

    # Temperature difference must always be positive (Process > Air in operating machinery)
    assert tdiff.min >= 7.0
    assert tdiff.max <= 13.0

    # Rotational speed [rpm]
    assert 1100 <= speed.min <= 1200
    assert 2800 <= speed.max <= 2900

    # Torque [Nm]
    assert 3.0 <= torque.min <= 4.0
    assert 75.0 <= torque.max <= 77.0

    # Tool wear [min]
    assert wear.min == 0.0
    assert 250.0 <= wear.max <= 255.0

    # Power [W]
    assert 1100.0 <= power.min <= 1200.0
    assert 10400.0 <= power.max <= 10600.0


def test_profile_empty_dataset_raises_error() -> None:
    """Ensure profiling an empty dataset raises ValueError."""
    with pytest.raises(ValueError, match="Cannot profile an empty dataset"):
        profile_ai4i2020_data([])


def test_profile_to_dict_and_markdown() -> None:
    """Ensure profile serialization to dict and markdown generation contain expected sections."""
    profile = load_and_profile_ai4i2020(RAW_DATA_PATH)
    p_dict = profile.to_dict()

    assert p_dict["total_rows"] == 10000
    assert p_dict["machine_failure_count"] == 339
    assert "TWF" in p_dict["failure_modes"]
    assert "Power [W]" in p_dict["features"]

    md = profile.format_markdown_profile()
    assert "- **Dataset used:**" in md
    assert "- **Row count:** 10,000 rows" in md
    assert "339 (3.39%)" in md
    assert "Heat Dissipation Failure" in md
    assert "| Air temperature |" in md


def test_download_and_extract_skips_when_file_exists(tmp_path: Path) -> None:
    """Verify downloader reuses existing target file without redownloading if force is False."""
    fake_csv = tmp_path / DEFAULT_CSV_NAME
    fake_csv.write_text("header1,header2\n1,2\n", encoding="utf-8")

    result = download_and_extract_ai4i2020(
        output_dir=tmp_path,
        zip_url="http://invalid.url.that.should.not.be.called",
        force_download=False,
    )

    assert result == fake_csv
    assert fake_csv.read_text(encoding="utf-8") == "header1,header2\n1,2\n"