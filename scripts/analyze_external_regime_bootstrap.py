#!/usr/bin/env python3
"""Run a reproducible regime-stratified 7-day block bootstrap.

Raw and derived external data must remain in the ignored local research area.
This command reads an explicitly supplied joined CSV/CSV.GZ and writes only the
requested JSON result.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import sys
from typing import TextIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bithumb_coin_trader.research_infra.regime_bootstrap import analyze_records


REQUIRED_COLUMNS = {
    "execution_timestamp_utc",
    "symbol",
    "lastliquidityind",
    "volatility_regime",
    "trend_regime",
    "volume_regime",
    "funding_regime",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def open_csv(path: Path) -> TextIO:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", newline="", encoding="utf-8")
    return path.open("rt", newline="", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reps", type=int, default=5_000)
    parser.add_argument("--seed", type=int, default=20260927)
    args = parser.parse_args()
    if not args.input.is_file():
        parser.error(f"input does not exist: {args.input}")
    if args.output.exists():
        parser.error(f"refusing to overwrite existing output: {args.output}")
    if args.output.resolve() == args.input.resolve():
        parser.error("input and output paths must differ")

    input_hash = sha256_file(args.input)
    with open_csv(args.input) as source:
        reader = csv.DictReader(source)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or ())
        if missing:
            parser.error(f"input is missing required columns: {sorted(missing)}")
        result = analyze_records(
            reader,
            input_sha256=input_hash,
            reps=args.reps,
            seed=args.seed,
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "input_sha256": input_hash,
        "symbol_counts": result["symbol_counts"],
        "strata": len(result["strata"]),
        "replicates": args.reps,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
