"""Derive the degradation simulator's parameters from the real IMS and C-MAPSS data.

python -m scripts.calibrate_simulator            # writes data/processed/sim_calibration.json
"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.core.config import settings
from app.ml.sim_calibration import calibrate, save_calibration

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_OUT = BACKEND / "data" / "processed" / "sim_calibration.json"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features-dir", type=Path, default=BACKEND / "data" / "processed" / "ims")
    parser.add_argument("--cmapss-dir", type=Path, default=BACKEND / "data" / "raw" / "cmapss")
    parser.add_argument("--subset", default=settings.rul_model_subset)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    save_calibration(calibrate(args.features_dir, args.cmapss_dir, args.subset), args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
