"""Unit tests for IMS bearing snapshot parsing, summaries, and profiling."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from app.ml.ims_profile import (
    SnapshotSummary,
    iter_snapshot_paths,
    parse_snapshot_text,
    parse_snapshot_timestamp,
    profile_summaries,
    profile_test_directory,
    sha256_file,
    summarize_snapshot,
    write_summaries_csv,
)

START = datetime(2003, 10, 22, 12, 6, 24)


def _name(t: datetime) -> str:
    return t.strftime("%Y.%m.%d.%H.%M.%S")


def _text(signal: np.ndarray) -> str:
    return "\n".join("\t".join(f"{v:.6f}" for v in row) for row in signal)


def _write_set(
    directory: Path, n_files: int, channels: int = 4, interval_min: int = 10, samples: int = 200
) -> None:
    """Healthy noise that grows louder and spikier toward the end of the run."""
    rng = np.random.default_rng(0)
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(n_files):
        scale = 0.1 + 0.4 * (i / max(1, n_files - 1)) ** 3
        signal = rng.normal(0, scale, (samples, channels))
        if i > 0.9 * n_files:
            signal[::25] *= 8  # impulses -> high kurtosis
        name = _name(START + timedelta(minutes=interval_min * i))
        (directory / name).write_text(_text(signal))


def test_parse_snapshot_ok_and_rejects_bad_input() -> None:
    arr = parse_snapshot_text("1\t2\n3\t4\n")
    assert arr.shape == (2, 2)
    with pytest.raises(ValueError, match="expected 2 columns, got 3"):
        parse_snapshot_text("1\t2\n3\t4\t5\n", "f")
    with pytest.raises(ValueError, match="non-numeric"):
        parse_snapshot_text("1\tx\n")
    # Ragged rows whose token counts cancel out (2 + 3 + 1 == 3 * 2) must still be rejected.
    with pytest.raises(ValueError, match="expected 2 columns, got 3"):
        parse_snapshot_text("1\t2\n3\t4\t5\n6\n")
    with pytest.raises(ValueError, match="NaN or infinite"):
        parse_snapshot_text("1\tnan\n")
    with pytest.raises(ValueError, match="empty"):
        parse_snapshot_text("\n \n")


def test_summaries_match_hand_computed_values() -> None:
    sig = np.array([[1.0], [-1.0], [1.0], [-1.0]])
    s = summarize_snapshot(sig)
    assert s["rms"][0] == pytest.approx(1.0)
    assert s["mean"][0] == pytest.approx(0.0)
    assert s["peak"][0] == pytest.approx(1.0)
    assert s["kurtosis"][0] == pytest.approx(1.0)  # a +/-1 square wave: m4/m2^2 = 1


def test_kurtosis_of_gaussian_noise_is_about_three_and_impulses_raise_it() -> None:
    rng = np.random.default_rng(1)
    noise = rng.normal(0, 1, (200_000, 1))
    assert summarize_snapshot(noise)["kurtosis"][0] == pytest.approx(3.0, abs=0.1)
    spiky = noise.copy()
    spiky[::1000] *= 20
    assert summarize_snapshot(spiky)["kurtosis"][0] > 6.0


def test_constant_signal_has_zero_kurtosis_not_nan() -> None:
    s = summarize_snapshot(np.full((10, 2), 3.0))
    assert np.isfinite(s["kurtosis"]).all()
    assert s["std"].tolist() == [0.0, 0.0]


def test_timestamp_parsing_and_sorted_listing(tmp_path: Path) -> None:
    assert parse_snapshot_timestamp("2003.10.22.12.06.24") == START
    for t in (START + timedelta(minutes=20), START, START + timedelta(minutes=10)):
        (tmp_path / _name(t)).write_text("1\t2\n")
    (tmp_path / "notes.txt").write_text("x")
    paths, bad = iter_snapshot_paths(tmp_path)
    assert [p.name for p in paths] == [_name(START + timedelta(minutes=m)) for m in (0, 10, 20)]
    assert bad == ["notes.txt"]


def test_profile_of_synthetic_degrading_test_set(tmp_path: Path) -> None:
    _write_set(tmp_path / "set", n_files=60)
    profile, summaries = profile_test_directory("synthetic", tmp_path / "set")
    assert (profile.n_files, profile.n_channels) == (60, 4)
    assert profile.samples_per_file_min == profile.samples_per_file_max == 200
    assert profile.median_interval_minutes == pytest.approx(10.0)
    assert profile.gap_count == 0
    assert profile.duration_days == pytest.approx(59 * 10 / 1440)
    assert len(summaries) == 60
    for ch in profile.channels:
        assert ch.rms_ratio > 2.0  # vibration grows over the run
        assert ch.kurtosis_max > ch.kurtosis_start


def test_gaps_are_detected(tmp_path: Path) -> None:
    d = tmp_path / "set"
    _write_set(d, n_files=20)
    victims = sorted(d.iterdir())[8:12]
    for v in victims:
        v.unlink()
    profile, _ = profile_test_directory("gappy", d)
    assert profile.gap_count == 1
    assert profile.largest_gap_minutes == pytest.approx(50.0)


def test_unreadable_files_are_reported_not_fatal(tmp_path: Path) -> None:
    d = tmp_path / "set"
    _write_set(d, n_files=10)
    bad_name = _name(START + timedelta(minutes=5))
    (d / bad_name).write_text("1\t2\n3\n")  # ragged
    profile, _ = profile_test_directory("bad", d)
    assert profile.n_files == 10
    assert bad_name in profile.unreadable_files
    assert "expected 2 columns" in profile.unreadable_files[bad_name]


def test_inconsistent_channel_counts_are_flagged() -> None:
    def summ(i: int, channels: int) -> SnapshotSummary:
        stats = {k: [1.0] * channels for k in ("mean", "std", "rms", "peak", "kurtosis")}
        return SnapshotSummary(
            name=f"f{i}",
            timestamp=START + timedelta(minutes=10 * i),
            n_samples=100,
            n_channels=channels,
            stats=stats,
        )

    summaries = [summ(i, 4) for i in range(9)] + [summ(9, 8)]
    profile = profile_summaries("mixed", summaries, {}, [])
    assert profile.n_channels == 4
    assert profile.inconsistent_channel_files == ["f9"]
    assert profile.n_files == 9


def test_empty_directory_raises(tmp_path: Path) -> None:
    (tmp_path / "set").mkdir()
    with pytest.raises(ValueError, match="no readable snapshot files"):
        profile_test_directory("empty", tmp_path / "set")


def test_profile_serializes_and_csv_roundtrip(tmp_path: Path) -> None:
    _write_set(tmp_path / "set", n_files=12, channels=2)
    profile, summaries = profile_test_directory("s", tmp_path / "set")
    assert json.loads(json.dumps(profile.to_dict()))["n_files"] == 12
    csv_path = tmp_path / "out" / "summaries.csv"
    write_summaries_csv(csv_path, summaries)
    lines = csv_path.read_text().splitlines()
    assert len(lines) == 13
    assert lines[0].startswith("file,timestamp,n_samples,mean_ch1,mean_ch2")


def test_sha256_file(tmp_path: Path) -> None:
    f = tmp_path / "a.bin"
    f.write_bytes(b"abc")
    assert sha256_file(f) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
