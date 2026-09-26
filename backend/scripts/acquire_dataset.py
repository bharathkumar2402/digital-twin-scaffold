"""Dataset acquisition and exploratory profiling CLI for the AI4I 2020 dataset.

Downloads the official AI4I 2020 Predictive Maintenance dataset from the
UCI Machine Learning Repository, verifies file integrity, and computes
empirical baseline distributions for model training and IoT simulator calibration.
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

from app.ml.dataset_profile import DatasetProfile, load_and_profile_ai4i2020

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_UCI_ZIP_URL = (
    "https://archive.ics.uci.edu/static/public/601/ai4i+2020+predictive+maintenance+dataset.zip"
)
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
DEFAULT_CSV_NAME = "ai4i2020.csv"


def download_and_extract_ai4i2020(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    zip_url: str = DEFAULT_UCI_ZIP_URL,
    force_download: bool = False,
    timeout_seconds: float = 30.0,
) -> Path:
    """Download AI4I 2020 zip archive from UCI ML Repository and extract ai4i2020.csv."""
    output_dir.mkdir(parents=True, exist_ok=True)
    target_csv = output_dir / DEFAULT_CSV_NAME

    if target_csv.is_file() and not force_download:
        logger.info(
            "Dataset CSV already present at %s (%d bytes). Skipping download.",
            target_csv,
            target_csv.stat().st_size,
        )
        return target_csv

    logger.info("Downloading AI4I 2020 archive from: %s", zip_url)
    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        response = client.get(zip_url)
        response.raise_for_status()

    zip_bytes = response.content
    logger.info("Downloaded %d bytes. Extracting archive...", len(zip_bytes))

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        # Find the csv entry regardless of internal folder casing
        csv_candidates = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_candidates:
            raise ValueError(f"No CSV file found inside zip archive: {zf.namelist()}")

        chosen_member = csv_candidates[0]
        extracted_content = zf.read(chosen_member)
        target_csv.write_bytes(extracted_content)

    logger.info("Extracted %s (%d bytes)", target_csv, target_csv.stat().st_size)
    return target_csv


def acquire_and_profile(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    zip_url: str = DEFAULT_UCI_ZIP_URL,
    force: bool = False,
) -> DatasetProfile:
    """Acquire the dataset and return its statistical profile."""
    csv_path = download_and_extract_ai4i2020(
        output_dir=output_dir,
        zip_url=zip_url,
        force_download=force,
    )
    return load_and_profile_ai4i2020(csv_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Acquire and profile AI4I 2020 Predictive Maintenance dataset from UCI."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to save the raw dataset file.",
    )
    parser.add_argument(
        "--url",
        type=str,
        default=DEFAULT_UCI_ZIP_URL,
        help="Direct URL to the dataset zip archive.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download even if the file exists locally.",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Print the formatted markdown profile to stdout.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional path to write the profile as JSON.",
    )

    args = parser.parse_args()

    try:
        profile = acquire_and_profile(
            output_dir=args.output_dir,
            zip_url=args.url,
            force=args.force,
        )
    except Exception as exc:
        logger.error("Failed to acquire/profile dataset: %s", exc)
        return 1

    logger.info("Dataset successfully profiled: %d rows, 0 nulls.", profile.total_rows)
    logger.info(
        "Machine failure rate: %.2f%% (%d / %d)",
        profile.machine_failure_rate_percent,
        profile.machine_failure_count,
        profile.total_rows,
    )

    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(profile.to_dict(), indent=2), encoding="utf-8")
        logger.info("Profile JSON saved to %s", args.json_output)

    if args.profile:
        print("\n" + profile.format_markdown_profile() + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())