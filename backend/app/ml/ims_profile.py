"""Streaming data profiling for the NASA/IMS bearing run-to-failure dataset.

The IMS dataset (Lee et al., University of Cincinnati / NSF I/UCR IMS Center, 2007) holds
three run-to-failure tests. Each test is a directory of snapshot files named by timestamp
(`YYYY.MM.DD.HH.MM.SS`); every file is a ~1 second vibration recording sampled at 20 kHz,
stored as tab-separated text with one column per accelerometer channel.

The full dataset is several GB, so this module never holds more than one snapshot in memory:
each file is reduced to a few per-channel summary numbers (RMS, kurtosis, peak, ...) as it
is read, and the profile is computed from those summaries.
"""

from __future__ import annotations

import hashlib
import warnings
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

DATASET_NAME = "NASA/IMS Bearing Run-to-Failure Dataset"
SOURCE = (
    "NASA Prognostics Center of Excellence (PCoE) data repository (IMS Center, Univ. of Cincinnati)"
)
CITATION = (
    "Lee, J., Qiu, H., Yu, G., Lin, J., & Rexnord Technical Services (2007). "
    "IMS, University of Cincinnati. Bearing Data Set. NASA Ames Prognostics Data Repository, "
    "NASA Ames Research Center, Moffett Field, CA."
)

TIMESTAMP_FORMAT = "%Y.%m.%d.%H.%M.%S"
HEALTHY_FRACTION = 0.10  # first/last fraction of files used for start/end comparisons
GAP_FACTOR = 3.0  # an interval this many times the median is reported as a gap

SUMMARY_COLUMNS = ("mean", "std", "rms", "peak", "kurtosis")


def parse_snapshot_timestamp(name: str) -> datetime:
    """Parse a snapshot filename like `2003.10.22.12.06.24`."""
    return datetime.strptime(name, TIMESTAMP_FORMAT)


def _parse_fast(text: str, lines: list[str], n_channels: int) -> np.ndarray | None:
    """Vectorised parse; returns None unless every row is complete and every value finite."""
    if any(len(ln.split()) != n_channels for ln in lines):
        return None  # ragged rows whose token counts happen to cancel out must not slip through
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            flat = np.fromstring(text, dtype=np.float64, sep=" ")
    except ValueError:  # newer numpy raises instead of returning a partial parse
        return None
    if flat.size != len(lines) * n_channels or not np.isfinite(flat).all():
        return None
    return flat.reshape(len(lines), n_channels)


def parse_snapshot_text(text: str, source: str = "<memory>") -> np.ndarray:
    """Parse one snapshot into an (n_samples, n_channels) float array, strictly validated."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError(f"{source}: snapshot is empty")
    n_channels = len(lines[0].split())
    fast = _parse_fast(text, lines, n_channels)
    if fast is not None:
        return fast
    # Slow path: locate and describe the exact problem.
    values: list[float] = []
    for line_no, line in enumerate(lines, start=1):
        parts = line.split()
        if len(parts) != n_channels:
            raise ValueError(f"{source}:{line_no}: expected {n_channels} columns, got {len(parts)}")
        try:
            values.extend(float(p) for p in parts)
        except ValueError as exc:
            raise ValueError(f"{source}:{line_no}: non-numeric value ({exc})") from exc
    arr = np.asarray(values, dtype=np.float64).reshape(len(lines), n_channels)
    if not np.isfinite(arr).all():
        raise ValueError(f"{source}: contains NaN or infinite values")
    return arr


def summarize_snapshot(signal: np.ndarray) -> dict[str, np.ndarray]:
    """Per-channel summary statistics (each an array of length n_channels)."""
    if signal.ndim != 2 or signal.shape[0] < 2:
        raise ValueError(f"expected an (n_samples>=2, n_channels) array, got {signal.shape}")
    mean = signal.mean(axis=0)
    centered = signal - mean
    var = (centered**2).mean(axis=0)
    std = np.sqrt(var)
    with np.errstate(divide="ignore", invalid="ignore"):
        kurt = np.where(var > 0, (centered**4).mean(axis=0) / var**2, 0.0)
    return {
        "mean": mean,
        "std": std,
        "rms": np.sqrt((signal**2).mean(axis=0)),
        "peak": np.abs(signal).max(axis=0),
        "kurtosis": kurt,  # Pearson kurtosis: 3.0 for a Gaussian signal
    }


@dataclass(frozen=True)
class SnapshotSummary:
    name: str
    timestamp: datetime
    n_samples: int
    n_channels: int
    stats: dict[str, list[float]]  # stat name -> per-channel values


def iter_snapshot_paths(directory: Path) -> tuple[list[Path], list[str]]:
    """Snapshot files in a test directory sorted by timestamp, plus names that don't parse."""
    good: list[tuple[datetime, Path]] = []
    bad: list[str] = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        try:
            good.append((parse_snapshot_timestamp(path.name), path))
        except ValueError:
            bad.append(path.name)
    return [p for _, p in sorted(good)], sorted(bad)


def iter_summaries(paths: list[Path], errors: dict[str, str]) -> Iterator[SnapshotSummary]:
    """Stream snapshots one at a time; unreadable files are recorded in `errors`, not raised."""
    for path in paths:
        try:
            signal = parse_snapshot_text(path.read_text(), str(path))
            stats = {k: v.tolist() for k, v in summarize_snapshot(signal).items()}
        except ValueError as exc:
            errors[path.name] = str(exc)
            continue
        yield SnapshotSummary(
            name=path.name,
            timestamp=parse_snapshot_timestamp(path.name),
            n_samples=int(signal.shape[0]),
            n_channels=int(signal.shape[1]),
            stats=stats,
        )


@dataclass
class ChannelDegradation:
    """How one channel's vibration level and impulsiveness change over the test."""

    channel: int
    rms_start: float
    rms_end: float
    rms_ratio: float
    kurtosis_start: float
    kurtosis_end: float
    kurtosis_max: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TestSetProfile:
    name: str
    n_files: int
    n_channels: int
    samples_per_file_min: int
    samples_per_file_max: int
    start: str
    end: str
    duration_days: float
    median_interval_minutes: float
    gap_count: int
    largest_gap_minutes: float
    unreadable_files: dict[str, str]
    unparsable_names: list[str]
    inconsistent_channel_files: list[str]
    channels: list[ChannelDegradation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImsProfile:
    dataset_name: str
    source: str
    citation: str
    test_sets: list[TestSetProfile]
    archive_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def format_markdown_profile(self) -> str:
        lines = [
            f"**Dataset:** {self.dataset_name}",
            "",
            "| Test set | Files | Channels | Samples/file | Span (days) | Median interval (min) "
            "| Gaps | Unreadable |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for s in self.test_sets:
            samples = (
                f"{s.samples_per_file_min}"
                if s.samples_per_file_min == s.samples_per_file_max
                else f"{s.samples_per_file_min}-{s.samples_per_file_max}"
            )
            lines.append(
                f"| {s.name} | {s.n_files} | {s.n_channels} | {samples} | {s.duration_days:.1f} "
                f"| {s.median_interval_minutes:g} | {s.gap_count} | {len(s.unreadable_files)} |"
            )
        lines += [
            "",
            "| Test set | Channel | RMS start | RMS end | RMS ratio "
            "| Kurtosis start | Kurtosis max |",
            "|---|---|---|---|---|---|---|",
        ]
        for s in self.test_sets:
            for c in s.channels:
                lines.append(
                    f"| {s.name} | {c.channel} | {c.rms_start:.4f} | {c.rms_end:.4f} "
                    f"| {c.rms_ratio:.2f} | {c.kurtosis_start:.2f} | {c.kurtosis_max:.1f} |"
                )
        return "\n".join(lines)


def _window(n: int) -> int:
    return max(1, int(round(n * HEALTHY_FRACTION)))


def profile_summaries(
    name: str,
    summaries: list[SnapshotSummary],
    errors: dict[str, str],
    unparsable_names: list[str],
) -> TestSetProfile:
    """Build a test-set profile from per-snapshot summaries (already sorted by time)."""
    if not summaries:
        raise ValueError(f"{name}: no readable snapshot files")
    channel_counts = [s.n_channels for s in summaries]
    n_channels = int(np.bincount(channel_counts).argmax())  # most common channel count
    inconsistent = [s.name for s in summaries if s.n_channels != n_channels]
    usable = [s for s in summaries if s.n_channels == n_channels]

    times = np.array([s.timestamp.timestamp() for s in usable])
    intervals = np.diff(times) / 60.0
    median_interval = float(np.median(intervals)) if intervals.size else 0.0
    gaps = intervals[intervals > GAP_FACTOR * median_interval] if intervals.size else intervals
    samples = [s.n_samples for s in usable]

    w = _window(len(usable))
    channels: list[ChannelDegradation] = []
    for ch in range(n_channels):
        rms = np.array([s.stats["rms"][ch] for s in usable])
        kurt = np.array([s.stats["kurtosis"][ch] for s in usable])
        rms_start = float(np.median(rms[:w]))
        rms_end = float(np.median(rms[-w:]))
        channels.append(
            ChannelDegradation(
                channel=ch + 1,
                rms_start=rms_start,
                rms_end=rms_end,
                rms_ratio=rms_end / rms_start if rms_start > 0 else float("inf"),
                kurtosis_start=float(np.median(kurt[:w])),
                kurtosis_end=float(np.median(kurt[-w:])),
                kurtosis_max=float(kurt.max()),
            )
        )
    return TestSetProfile(
        name=name,
        n_files=len(usable),
        n_channels=n_channels,
        samples_per_file_min=min(samples),
        samples_per_file_max=max(samples),
        start=usable[0].timestamp.isoformat(),
        end=usable[-1].timestamp.isoformat(),
        duration_days=float((times[-1] - times[0]) / 86400.0),
        median_interval_minutes=median_interval,
        gap_count=int(gaps.size),
        largest_gap_minutes=float(gaps.max()) if gaps.size else 0.0,
        unreadable_files=errors,
        unparsable_names=unparsable_names,
        inconsistent_channel_files=inconsistent,
        channels=channels,
    )


def profile_test_directory(
    name: str, directory: Path
) -> tuple[TestSetProfile, list[SnapshotSummary]]:
    """Stream every snapshot in `directory`; return its profile and per-file summaries."""
    paths, unparsable = iter_snapshot_paths(directory)
    errors: dict[str, str] = {}
    summaries = list(iter_summaries(paths, errors))
    return profile_summaries(name, summaries, errors, unparsable), summaries


def write_summaries_csv(path: Path, summaries: list[SnapshotSummary]) -> None:
    """Persist the small per-file summaries (not the raw signals) for later tasks."""
    n_channels = summaries[0].n_channels if summaries else 0
    header = ["file", "timestamp", "n_samples"] + [
        f"{stat}_ch{ch + 1}" for stat in SUMMARY_COLUMNS for ch in range(n_channels)
    ]
    rows = [",".join(header)]
    for s in summaries:
        if s.n_channels != n_channels:
            continue
        values = [
            f"{s.stats[stat][ch]:.8g}" for stat in SUMMARY_COLUMNS for ch in range(n_channels)
        ]
        rows.append(",".join([s.name, s.timestamp.isoformat(), str(s.n_samples), *values]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()
