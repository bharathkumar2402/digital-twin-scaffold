"""Unit tests for C-MAPSS acquisition, validation, and profiling."""

import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from app.ml.cmapss_profile import (
    COLUMNS,
    count_cycle_gaps,
    count_operating_conditions,
    load_and_profile_cmapss,
    parse_cmapss_table,
    parse_rul_vector,
    profile_subset,
    required_files,
)
from scripts.acquire_cmapss import DEFAULT_OUTPUT_DIR, download_cmapss, extract_cmapss


def _row(unit: int, cycle: int, s1: float = 0.0, s3: float = 100.0, sensor: float = 1.0) -> str:
    values = [unit, cycle, s1, 0.0, s3, *([sensor + cycle * 0.01] * 21)]
    return " ".join(str(v) for v in values)


def _table(units: dict[int, int], **kw: float) -> str:
    return "\n".join(_row(u, c, **kw) for u, n in units.items() for c in range(1, n + 1))


def test_parse_rejects_ragged_row() -> None:
    with pytest.raises(ValueError, match="expected 26 columns, got 25"):
        parse_cmapss_table(_row(1, 1) + "\n" + " ".join(["1"] * 25), "bad.txt")


def test_parse_rejects_non_numeric_and_nan() -> None:
    with pytest.raises(ValueError, match="non-numeric"):
        parse_cmapss_table(_row(1, 1).replace("100.0", "abc"))
    with pytest.raises(ValueError, match="NaN or infinite"):
        parse_cmapss_table(_row(1, 1).replace("100.0", "nan"))


def test_parse_rejects_empty_file() -> None:
    with pytest.raises(ValueError, match="no data rows"):
        parse_cmapss_table("\n  \n")


def test_parse_rul_rejects_negative_and_empty() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        parse_rul_vector("5\n-1\n")
    with pytest.raises(ValueError, match="no RUL values"):
        parse_rul_vector("")
    assert parse_rul_vector("5\n10\n").tolist() == [5.0, 10.0]


def test_rul_length_must_match_test_engine_count() -> None:
    train = parse_cmapss_table(_table({1: 5, 2: 6}))
    test = parse_cmapss_table(_table({1: 3, 2: 3}))
    with pytest.raises(ValueError, match="RUL file has 3 entries but test file has 2"):
        profile_subset("FDXXX", train, test, np.array([1.0, 2.0, 3.0]))


def test_cycle_gaps_detected() -> None:
    good = parse_cmapss_table(_table({1: 4}))
    assert count_cycle_gaps(good) == 0
    gappy = parse_cmapss_table("\n".join(_row(1, c) for c in (1, 2, 4)))
    assert count_cycle_gaps(gappy) == 1


def test_operating_conditions_ignore_noise() -> None:
    noisy = "\n".join(
        [_row(1, 1, s1=0.0001), _row(1, 2, s1=-0.0002), _row(1, 3, s1=20.0001, s3=60.0)]
    )
    assert count_operating_conditions(parse_cmapss_table(noisy)) == 2


def test_constant_sensor_flagged_and_stats_computed() -> None:
    # sensor_1 varies with cycle in _row; override one sensor column to be constant.
    lines = []
    for c in range(1, 6):
        parts = _row(1, c).split()
        parts[5] = "518.67"
        lines.append(" ".join(parts))
    train = parse_cmapss_table("\n".join(lines))
    test = parse_cmapss_table(_table({1: 3}))
    profile = profile_subset("FDXXX", train, test, np.array([10.0]))
    assert profile.constant_sensors == ["sensor_1"]
    assert len(profile.sensor_stats) == 21
    assert profile.train_life_max == 5
    assert profile.test_rul_median == 10.0


def test_profile_serializes_to_json() -> None:
    train = parse_cmapss_table(_table({1: 5, 2: 7}))
    test = parse_cmapss_table(_table({1: 3}))
    profile = profile_subset("FDXXX", train, test, np.array([4.0]))
    assert json.loads(json.dumps(profile.to_dict()))["train_units"] == 2


def test_missing_files_raise_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Missing C-MAPSS files"):
        load_and_profile_cmapss(tmp_path)


def _nested_archive(files: dict[str, str]) -> bytes:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as zf:
        zf.writestr("6. Turbofan/CMAPSSData.zip", inner.getvalue())
    return outer.getvalue()


def test_extract_handles_nested_zip(tmp_path: Path) -> None:
    files = {name: "x" for name in required_files()}
    paths = extract_cmapss(_nested_archive(files), tmp_path)
    assert len(paths) == 12
    assert (tmp_path / "train_FD001.txt").read_text() == "x"


def test_extract_rejects_incomplete_archive(tmp_path: Path) -> None:
    files = {name: "x" for name in required_files()[:-1]}
    with pytest.raises(ValueError, match="missing required C-MAPSS files"):
        extract_cmapss(_nested_archive(files), tmp_path)


def test_download_skips_when_files_present(tmp_path: Path) -> None:
    for name in required_files():
        (tmp_path / name).write_text("x")
    # An unreachable URL proves no network call is made.
    assert download_cmapss(tmp_path, url="http://invalid.invalid/none.zip") == tmp_path


_real_data = pytest.mark.skipif(
    not all((DEFAULT_OUTPUT_DIR / f).is_file() for f in required_files()),
    reason="C-MAPSS not downloaded; run `python -m scripts.acquire_cmapss`",
)


@_real_data
def test_real_cmapss_matches_published_dimensions() -> None:
    """Row/engine counts must match the published C-MAPSS documentation."""
    profile = load_and_profile_cmapss(DEFAULT_OUTPUT_DIR)
    by_name = {s.name: s for s in profile.subsets}
    assert [(s.train_units, s.train_rows) for s in profile.subsets] == [
        (100, 20631),
        (260, 53759),
        (100, 24720),
        (249, 61249),
    ]
    assert (by_name["FD001"].operating_conditions, by_name["FD002"].operating_conditions) == (1, 6)
    assert by_name["FD003"].operating_conditions == 1
    assert by_name["FD004"].operating_conditions == 6
    assert all(s.cycle_gap_units == 0 for s in profile.subsets)
    assert len(COLUMNS) == 26
