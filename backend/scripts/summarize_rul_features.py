"""Print a summary of the RUL feature matrices for a C-MAPSS subset (manual check for 3b.2)."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

from app.ml.cmapss_profile import (
    parse_cmapss_table,
    parse_rul_vector,
    rul_file,
    test_file,
    train_file,
)
from app.ml.rul_dataset import build_test_dataset, build_train_dataset
from app.ml.rul_features import fit_config
from scripts.acquire_cmapss import DEFAULT_OUTPUT_DIR


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize RUL features for a C-MAPSS subset.")
    parser.add_argument("--subset", default="FD001", choices=["FD001", "FD002", "FD003", "FD004"])
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    root: Path = args.data_dir
    train = parse_cmapss_table((root / train_file(args.subset)).read_text())
    test = parse_cmapss_table((root / test_file(args.subset)).read_text())
    rul = parse_rul_vector((root / rul_file(args.subset)).read_text())

    started = time.perf_counter()
    cfg = fit_config(train)
    tr = build_train_dataset(train, cfg)
    te = build_test_dataset(test, rul, cfg)
    elapsed = time.perf_counter() - started

    print(f"Subset {args.subset}  (built in {elapsed:.1f}s)")
    print(f"  kept sensors      : {len(cfg.sensors)}  {list(cfg.sensors)}")
    print(f"  operating regimes : {len(cfg.regime_centers)}  {list(cfg.regime_centers)}")
    print(f"  features          : {len(cfg.feature_names)}")
    print(f"  train matrix      : {tr.X.shape}  engines={np.unique(tr.units).size}")
    print(f"  train labels      : capped max={tr.y.max():.0f}  uncapped max={tr.y_true.max():.0f}")
    test_range = f"{te.y.min():.0f}/{te.y.max():.0f}"
    print(f"  test matrix       : {te.X.shape}  labels(capped) min/max={test_range}")
    print(f"  any NaN/inf       : {not (np.isfinite(tr.X).all() and np.isfinite(te.X).all())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
