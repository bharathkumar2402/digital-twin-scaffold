"""Tests for IMS archive unpacking and acquisition logic (the RAR step itself is faked)."""

import csv
import json
import subprocess
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import py7zr
import pytest

from scripts import acquire_ims
from scripts.acquire_ims import (
    TEST_SETS,
    ExtractionError,
    acquire,
    datasets_present,
    find_bsdtar,
    unpack_archive,
)

START = datetime(2003, 10, 22, 12, 6, 24)


def _snapshot_files(directory: Path, n: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        name = (START + timedelta(minutes=10 * i)).strftime("%Y.%m.%d.%H.%M.%S")
        (directory / name).write_text("1\t2\n3\t4\n")


def _make_archive(
    tmp_path: Path, members: tuple[str, ...] = TEST_SETS, with_7z: bool = True
) -> Path:
    """Build a zip -> 7z -> (dummy .rar files + readme) archive like the official one."""
    inner = tmp_path / "inner"
    inner.mkdir()
    for test in members:
        (inner / f"{test}.rar").write_bytes(b"rar-bytes-" + test.encode())
    (inner / "Readme Document for IMS Bearing Data.pdf").write_bytes(b"%PDF-fake")
    seven = tmp_path / "IMS.7z"
    # No compression anywhere: py7zr's LZMA encoder allocation fails intermittently under
    # memory pressure on Windows, and these fixture bytes do not need compressing.
    with py7zr.SevenZipFile(seven, "w", filters=[{"id": py7zr.FILTER_COPY}]) as sz:
        sz.set_encoded_header_mode(False)
        for item in sorted(inner.iterdir()):
            sz.write(item, arcname=item.name)
    archive = tmp_path / "4. Bearings.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        if with_7z:
            zf.write(seven, "4. Bearings/IMS.7z")
        else:
            zf.writestr("4. Bearings/readme.txt", "no archive here")
    return archive


@pytest.fixture
def fake_rar(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the RAR extractor; mimic the 3rd test shipping inside `4th_test/txt/`."""
    seen: list[str] = []

    def fake_extract(rar_path: Path, dest: Path) -> None:
        seen.append(rar_path.name)
        test = rar_path.stem
        if test == "3rd_test":
            _snapshot_files(dest / "4th_test" / "txt", 7)
            _snapshot_files(dest / "decoy", 2)
        else:
            _snapshot_files(dest / test, 5)

    monkeypatch.setattr(acquire_ims, "extract_rar", fake_extract)
    return seen


def test_unpack_normalises_layout_and_copies_readme(tmp_path: Path, fake_rar: list[str]) -> None:
    out = tmp_path / "out"
    dirs = unpack_archive(_make_archive(tmp_path), out)
    assert [d.name for d in dirs] == list(TEST_SETS)
    assert fake_rar == ["1st_test.rar", "2nd_test.rar", "3rd_test.rar"]
    assert len(list((out / "1st_test").iterdir())) == 5
    # the nested 4th_test/txt folder (7 files) is chosen over the decoy folder (2 files)
    assert len(list((out / "3rd_test").iterdir())) == 7
    assert (out / "ims_readme.pdf").read_bytes() == b"%PDF-fake"
    assert not [p for p in out.iterdir() if p.name.startswith("_unpack_")]  # temp cleaned up
    assert datasets_present(out)


def test_unpack_rejects_zip_without_7z(tmp_path: Path, fake_rar: list[str]) -> None:
    with pytest.raises(ExtractionError, match="No .7z inside"):
        unpack_archive(_make_archive(tmp_path, with_7z=False), tmp_path / "out")


def test_unpack_rejects_missing_test_archive(tmp_path: Path, fake_rar: list[str]) -> None:
    archive = _make_archive(tmp_path, members=("1st_test", "2nd_test"))
    with pytest.raises(ExtractionError, match="3rd_test.rar missing"):
        unpack_archive(archive, tmp_path / "out")


def test_unpack_rejects_rar_with_no_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def empty_extract(rar_path: Path, dest: Path) -> None:
        (dest / "junk").mkdir(parents=True)
        (dest / "junk" / "readme.txt").write_text("x")

    monkeypatch.setattr(acquire_ims, "extract_rar", empty_extract)
    with pytest.raises(ExtractionError, match="No snapshot files found"):
        unpack_archive(_make_archive(tmp_path), tmp_path / "out")


def test_acquire_skips_when_present_and_validates_local_archive(tmp_path: Path) -> None:
    for test in TEST_SETS:
        _snapshot_files(tmp_path / test, 3)
    # an unreachable URL proves no network call is made
    assert acquire(tmp_path, url="http://invalid.invalid/x.zip") == tmp_path
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="--local-archive not found"):
        acquire(empty, local_archive=tmp_path / "nope.zip")


def test_datasets_present_requires_all_three_nonempty(tmp_path: Path) -> None:
    _snapshot_files(tmp_path / "1st_test", 2)
    _snapshot_files(tmp_path / "2nd_test", 2)
    (tmp_path / "3rd_test").mkdir()
    assert not datasets_present(tmp_path)
    _snapshot_files(tmp_path / "3rd_test", 1)
    assert datasets_present(tmp_path)


def test_find_bsdtar_requires_libarchive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acquire_ims.shutil, "which", lambda name: f"/fake/{name}")

    def gnu_tar(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(stdout="tar (GNU tar) 1.35")

    monkeypatch.setattr(subprocess, "run", gnu_tar)
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    with pytest.raises(ExtractionError, match="No RAR-capable tar"):
        find_bsdtar()

    def bsd_tar(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(stdout="bsdtar 3.8.8 - libarchive 3.8.8")

    monkeypatch.setattr(subprocess, "run", bsd_tar)
    assert find_bsdtar() == "/fake/bsdtar"


# ---- real data (summaries are committed; raw files are optional) --------------------------

SUMMARIES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "processed" / "ims"
_have_summaries = pytest.mark.skipif(
    not all((SUMMARIES_DIR / f"{t}_summaries.csv").is_file() for t in TEST_SETS),
    reason="IMS summaries missing; run `python -m scripts.acquire_ims --summaries-dir ...`",
)


def _load_summary(test: str) -> tuple[list[str], list[datetime], dict[str, np.ndarray]]:
    with (SUMMARIES_DIR / f"{test}_summaries.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    columns = {
        key: np.array([float(r[key]) for r in rows])
        for key in rows[0]
        if key not in ("file", "timestamp")
    }
    return (
        [r["file"] for r in rows],
        [datetime.fromisoformat(r["timestamp"]) for r in rows],
        columns,
    )


@_have_summaries
def test_real_ims_matches_nasa_readme_for_sets_1_and_2() -> None:
    """File counts, channel counts and recording windows must match NASA's readme."""
    files1, ts1, cols1 = _load_summary("1st_test")
    assert len(files1) == 2156
    assert ts1[0] == datetime(2003, 10, 22, 12, 6, 24)
    assert ts1[-1] == datetime(2003, 11, 25, 23, 39, 56)
    assert sum(k.startswith("rms_ch") for k in cols1) == 8
    files2, ts2, cols2 = _load_summary("2nd_test")
    assert len(files2) == 984
    assert ts2[0] == datetime(2004, 2, 12, 10, 32, 39)
    assert ts2[-1] == datetime(2004, 2, 19, 6, 22, 39)
    assert sum(k.startswith("rms_ch") for k in cols2) == 4
    assert (cols1["n_samples"] == 20480).all()


@_have_summaries
def test_real_ims_set3_matches_readme_prefix_and_extends_beyond_it() -> None:
    """The archive's 3rd test is longer than the readme's 4,448 files; the prefix matches."""
    files, ts, cols = _load_summary("3rd_test")
    assert len(files) == 6324
    assert ts[0] == datetime(2004, 3, 4, 9, 27, 46)
    assert ts[4447] == datetime(2004, 4, 4, 19, 1, 57)  # the readme's stated last file
    # The bearing-3 outer-race failure signature only appears after the readme's end.
    kurt3 = cols["kurtosis_ch3"]
    assert kurt3[:4448].max() < 6.0
    assert kurt3[4448:].max() > 10.0


@_have_summaries
def test_real_ims_failure_signatures_are_on_the_documented_bearings() -> None:
    _, _, c1 = _load_summary("1st_test")
    # set 1: inner race defect in bearing 3 (ch5/6), roller element defect in bearing 4 (ch7/8)
    assert c1["kurtosis_ch5"].max() > 20 and c1["kurtosis_ch7"].max() > 20
    assert c1["kurtosis_ch1"].max() < 10 and c1["kurtosis_ch3"].max() < 10
    _, _, c2 = _load_summary("2nd_test")
    # set 2: outer race failure in bearing 1 (ch1) - largest RMS growth of the four
    growth = [c2[f"rms_ch{c}"][-50:].mean() / c2[f"rms_ch{c}"][:50].mean() for c in range(1, 5)]
    assert int(np.argmax(growth)) == 0
    _, _, c3 = _load_summary("3rd_test")
    # set 3: outer race failure in bearing 3 (ch3)
    assert c3["kurtosis_ch3"].max() > 10
    assert max(c3[f"kurtosis_ch{c}"].max() for c in (1, 2, 4)) < 10


@_have_summaries
def test_committed_profile_json_has_no_unreadable_files() -> None:
    profile = json.loads((SUMMARIES_DIR / "profile.json").read_text())
    assert [s["name"] for s in profile["test_sets"]] == list(TEST_SETS)
    assert all(not s["unreadable_files"] for s in profile["test_sets"])
    assert all(
        s["samples_per_file_min"] == s["samples_per_file_max"] == 20480
        for s in profile["test_sets"]
    )
    assert profile["archive_sha256"] is None or len(profile["archive_sha256"]) == 64


_raw_dir = Path(__file__).resolve().parent.parent.parent / "data" / "raw" / "ims"
_have_raw = pytest.mark.skipif(not datasets_present(_raw_dir), reason="raw IMS data not extracted")


@_have_raw
def test_real_raw_snapshots_parse_with_expected_shape() -> None:
    from app.ml.ims_profile import iter_snapshot_paths, parse_snapshot_text

    for test, channels in (("1st_test", 8), ("2nd_test", 4), ("3rd_test", 4)):
        paths, bad = iter_snapshot_paths(_raw_dir / test)
        assert bad == []
        signal = parse_snapshot_text(paths[0].read_text(), str(paths[0]))
        assert signal.shape == (20480, channels)
