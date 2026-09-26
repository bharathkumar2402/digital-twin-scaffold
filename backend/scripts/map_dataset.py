"""Dataset-to-schema mapping CLI.

Reads the raw AI4I 2020 dataset and transforms observations into structured
`sensor_readings` and `risk_scores` representations according to the project schema.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from app.services.ml.dataset_mapping import (
    extract_all_risk_scores,
    extract_all_sensor_readings,
    map_ai4i2020_dataset,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_CSV_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "raw" / "ai4i2020.csv"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Transform raw AI4I 2020 dataset onto sensor_readings and risk_scores schemas."
    )
    parser.add_argument(
        "--csv-path",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help="Path to ai4i2020.csv dataset.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit on number of rows to transform.",
    )
    parser.add_argument(
        "--export-json",
        type=Path,
        default=None,
        help="Optional destination path to export mapped records as JSON.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=60,
        help="Synthetic chronological interval in seconds between observations.",
    )

    args = parser.parse_args()

    if not args.csv_path.is_file():
        logger.error("Dataset CSV not found at: %s", args.csv_path)
        return 1

    logger.info("Loading and mapping dataset from %s...", args.csv_path)
    records = map_ai4i2020_dataset(
        csv_path=args.csv_path,
        interval_seconds=args.interval_seconds,
    )

    if args.limit is not None and args.limit > 0:
        records = records[: args.limit]

    sensor_readings = extract_all_sensor_readings(records)
    risk_scores = extract_all_risk_scores(records)
    failure_count = sum(1 for r in records if r.is_failure)

    logger.info("Successfully mapped %d machine observation records.", len(records))
    logger.info("Total generated sensor readings: %d", len(sensor_readings))
    logger.info("Total generated risk score records: %d", len(risk_scores))
    logger.info(
        "Failure distribution: %d failures (%.2f%%), %d normal (%.2f%%)",
        failure_count,
        (failure_count / len(records)) * 100.0 if records else 0.0,
        len(records) - failure_count,
        ((len(records) - failure_count) / len(records)) * 100.0 if records else 0.0,
    )

    if args.export_json:
        args.export_json.parent.mkdir(parents=True, exist_ok=True)
        export_data = {
            "record_count": len(records),
            "sensor_reading_count": len(sensor_readings),
            "risk_score_count": len(risk_scores),
            "sample_records": [r.to_dict() for r in records[:5]],
        }
        args.export_json.write_text(json.dumps(export_data, indent=2), encoding="utf-8")
        logger.info("Sample exported to %s", args.export_json)

    return 0


if __name__ == "__main__":
    sys.exit(main())