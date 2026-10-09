"""Acquisition and profiling CLI for the NASA C-MAPSS turbofan dataset.

Downloads the official archive, extracts the twelve FD001-FD004 train/test/RUL files
(the archive nests a second zip), and optionally prints/saves the data profile.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import zipfile
from pathlib import Path

import httpx

from app.ml.cmapss_profile import CmapssProfile, load_and_profile_cmapss, required_files

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_URL = (
    "https://phm-datasets.s3.amazonaws.com/NASA/"
    "6.+Turbofan+Engine+Degradation+Simulation+Data+Set.zip"
)
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "cmapss"


def _extract_members(archive: bytes, wanted: set[str], out: dict[str, bytes]) -> None:
    """Collect wanted files from a zip, recursing into nested zips."""
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        for member in zf.namelist():
            base = Path(member).name
            if base in wanted:
                out[base] = zf.read(member)
            elif base.lower().endswith(".zip"):
                _extract_members(zf.read(member), wanted, out)


def extract_cmapss(archive: bytes, output_dir: Path) -> list[Path]:
    """Extract the required C-MAPSS files from `archive` into `output_dir`."""
    wanted = set(required_files())
    found: dict[str, bytes] = {}
    _extract_members(archive, wanted, found)
    missing = sorted(wanted - found.keys())
    if missing:
        raise ValueError(f"Archive is missing required C-MAPSS files: {missing}")
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, content in found.items():
        path = output_dir / name
        path.write_bytes(content)
        paths.append(path)
    return paths


def download_cmapss(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    url: str = DEFAULT_URL,
    force: bool = False,
    timeout_seconds: float = 120.0,
) -> Path:
    """Download and extract C-MAPSS unless all files already exist."""
    if not force and all((output_dir / f).is_file() for f in required_files()):
        logger.info("C-MAPSS files already present in %s. Skipping download.", output_dir)
        return output_dir
    logger.info("Downloading C-MAPSS archive from: %s", url)
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
    paths = extract_cmapss(response.content, output_dir)
    logger.info("Extracted %d files to %s", len(paths), output_dir)
    return output_dir


def acquire_and_profile(
    output_dir: Path = DEFAULT_OUTPUT_DIR, url: str = DEFAULT_URL, force: bool = False
) -> CmapssProfile:
    return load_and_profile_cmapss(download_cmapss(output_dir, url, force))


def main() -> int:
    parser = argparse.ArgumentParser(description="Acquire and profile NASA C-MAPSS.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--url", type=str, default=DEFAULT_URL)
    parser.add_argument("--force", action="store_true", help="Re-download even if present.")
    parser.add_argument("--profile", action="store_true", help="Print the markdown profile.")
    parser.add_argument("--json-output", type=Path, default=None)
    args = parser.parse_args()

    try:
        profile = acquire_and_profile(args.output_dir, args.url, args.force)
    except Exception as exc:
        logger.error("Failed to acquire/profile C-MAPSS: %s", exc)
        return 1

    logger.info("Profiled %d subsets.", len(profile.subsets))
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(profile.to_dict(), indent=2), encoding="utf-8")
        logger.info("Profile JSON saved to %s", args.json_output)
    if args.profile:
        print("\n" + profile.format_markdown_profile() + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
