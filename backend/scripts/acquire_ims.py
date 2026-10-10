"""Acquisition and profiling CLI for the NASA/IMS bearing run-to-failure dataset.

The official archive is nested three deep: a zip containing `IMS.7z`, which contains one
RAR per test (`1st_test.rar`, `2nd_test.rar`, `3rd_test.rar`) plus a readme PDF. This CLI
downloads (or accepts) the zip, unpacks every layer, normalises the test directories to
`1st_test/`, `2nd_test/`, `3rd_test/` under the output dir, then optionally profiles them.

RAR extraction uses a libarchive `bsdtar` (bundled with Windows 10+ as `tar.exe`; available
as `bsdtar` on Linux/macOS). The 7z layer uses `py7zr`.

    python -m scripts.acquire_ims --profile
    python -m scripts.acquire_ims --local-archive "path/to/4. Bearings.zip" --profile
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import httpx

from app.ml.ims_profile import (
    CITATION,
    DATASET_NAME,
    SOURCE,
    ImsProfile,
    iter_snapshot_paths,
    profile_test_directory,
    sha256_file,
    write_summaries_csv,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_URL = "https://phm-datasets.s3.amazonaws.com/NASA/4.+Bearings.zip"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "ims"
TEST_SETS: tuple[str, ...] = ("1st_test", "2nd_test", "3rd_test")
README_NAME = "ims_readme.pdf"


class ExtractionError(RuntimeError):
    """Raised when the archive cannot be unpacked with the tools available."""


def find_bsdtar() -> str:
    """Locate a libarchive tar that can read RAR files."""
    candidates = ["bsdtar", "tar", r"C:\Windows\System32\tar.exe"]
    for candidate in candidates:
        exe = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if not exe:
            continue
        try:
            out = subprocess.run(
                [exe, "--version"], capture_output=True, text=True, timeout=20, check=False
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if "libarchive" in out.lower():
            return exe
    raise ExtractionError(
        "No RAR-capable tar (bsdtar/libarchive) found. On Windows 10+ use the built-in "
        "tar.exe; elsewhere install libarchive-tools, or extract the .rar files yourself "
        "and pass --data-dir."
    )


def extract_rar(rar_path: Path, dest: Path) -> None:
    """Unpack one RAR into `dest` using bsdtar."""
    dest.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [find_bsdtar(), "-xf", str(rar_path), "-C", str(dest)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ExtractionError(f"Failed to extract {rar_path.name}: {result.stderr.strip()[:300]}")


def _largest_snapshot_dir(root: Path) -> Path:
    """The directory under `root` holding the most timestamp-named snapshot files.

    Handles archives that wrap the data in extra folders (the 3rd test ships as
    `4th_test/txt/`).
    """
    best, best_count = root, -1
    for directory in [root, *[p for p in root.rglob("*") if p.is_dir()]]:
        paths, _ = iter_snapshot_paths(directory)
        if len(paths) > best_count:
            best, best_count = directory, len(paths)
    if best_count <= 0:
        raise ExtractionError(f"No snapshot files found after extracting into {root}")
    return best


def unpack_archive(archive_zip: Path, output_dir: Path) -> list[Path]:
    """Unpack zip -> 7z -> rar into `output_dir/<test>/`; returns the three test dirs."""
    import py7zr

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir, prefix="_unpack_") as tmp_name:
        tmp = Path(tmp_name)
        with zipfile.ZipFile(archive_zip) as zf:
            members = [n for n in zf.namelist() if n.lower().endswith(".7z")]
            if not members:
                raise ExtractionError(f"No .7z inside {archive_zip.name}: {zf.namelist()}")
            zf.extract(members[0], tmp)
        seven_zip = tmp / members[0]
        with py7zr.SevenZipFile(seven_zip) as sz:
            sz.extractall(tmp / "seven")

        readme = next((tmp / "seven").glob("*.pdf"), None)
        if readme is not None:
            shutil.copyfile(readme, output_dir / README_NAME)

        result: list[Path] = []
        for test_name in TEST_SETS:
            rar = tmp / "seven" / f"{test_name}.rar"
            if not rar.is_file():
                raise ExtractionError(f"{rar.name} missing from IMS.7z")
            raw_dir = tmp / "rar" / test_name
            extract_rar(rar, raw_dir)
            source = _largest_snapshot_dir(raw_dir)
            target = output_dir / test_name
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(source), str(target))
            result.append(target)
            logger.info("Extracted %s (%d files)", test_name, len(iter_snapshot_paths(target)[0]))
    return result


def download_archive(dest: Path, url: str = DEFAULT_URL, timeout_seconds: float = 120.0) -> Path:
    """Stream the (~1 GB) archive to `dest`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading IMS archive from %s (about 1 GB)...", url)
    with httpx.stream("GET", url, follow_redirects=True, timeout=timeout_seconds) as response:
        response.raise_for_status()
        with dest.open("wb") as handle:
            for chunk in response.iter_bytes(1 << 20):
                handle.write(chunk)
    return dest


def datasets_present(output_dir: Path) -> bool:
    return all(
        (output_dir / t).is_dir() and iter_snapshot_paths(output_dir / t)[0] for t in TEST_SETS
    )


def acquire(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    url: str = DEFAULT_URL,
    local_archive: Path | None = None,
    force: bool = False,
) -> Path:
    """Ensure the three test directories exist under `output_dir`."""
    if datasets_present(output_dir) and not force:
        logger.info("IMS test sets already present in %s. Skipping download.", output_dir)
        return output_dir
    if local_archive is not None:
        archive = local_archive
        if not archive.is_file():
            raise FileNotFoundError(f"--local-archive not found: {archive}")
        unpack_archive(archive, output_dir)
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output_dir, prefix="_ims_dl_") as tmp:
            archive = download_archive(Path(tmp) / "bearings.zip", url)
            unpack_archive(archive, output_dir)
    return output_dir


def profile_dataset(
    data_dir: Path, summaries_dir: Path | None = None, archive: Path | None = None
) -> ImsProfile:
    """Profile all three test sets; optionally write per-file summary CSVs."""
    sets = []
    for test_name in TEST_SETS:
        logger.info("Profiling %s ...", test_name)
        profile, summaries = profile_test_directory(test_name, data_dir / test_name)
        sets.append(profile)
        if summaries_dir is not None:
            write_summaries_csv(summaries_dir / f"{test_name}_summaries.csv", summaries)
    return ImsProfile(
        dataset_name=DATASET_NAME,
        source=SOURCE,
        citation=CITATION,
        test_sets=sets,
        archive_sha256=sha256_file(archive) if archive is not None else None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Acquire and profile the NASA/IMS bearing data.")
    parser.add_argument(
        "--output-dir", "--data-dir", dest="output_dir", type=Path, default=DEFAULT_OUTPUT_DIR
    )
    parser.add_argument("--url", type=str, default=DEFAULT_URL)
    parser.add_argument(
        "--local-archive",
        type=Path,
        default=None,
        help="Use an already-downloaded '4. Bearings.zip' instead of downloading.",
    )
    parser.add_argument("--force", action="store_true", help="Re-extract even if present.")
    parser.add_argument("--profile", action="store_true", help="Print the markdown profile.")
    parser.add_argument("--json-output", type=Path, default=None)
    parser.add_argument(
        "--summaries-dir",
        type=Path,
        default=None,
        help="Write per-file summary CSVs (small) to this directory.",
    )
    args = parser.parse_args()

    try:
        data_dir = acquire(args.output_dir, args.url, args.local_archive, args.force)
        profile = None
        if args.profile or args.json_output or args.summaries_dir:
            profile = profile_dataset(data_dir, args.summaries_dir, args.local_archive)
    except Exception as exc:
        logger.error("Failed to acquire/profile IMS: %s", exc)
        return 1

    if profile is not None:
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(json.dumps(profile.to_dict(), indent=2), encoding="utf-8")
            logger.info("Profile JSON saved to %s", args.json_output)
        if args.profile:
            print("\n" + profile.format_markdown_profile() + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
