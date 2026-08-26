#!/usr/bin/env python3
"""Run the pre-registered Strategy V3 development-only research."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from bithumb_coin_trader.data import load_candles_csv
from bithumb_coin_trader.strategy_v3_research import assert_finite, build_strategy_v3_report


DEFAULT_INPUT = Path("data/krw-btc-1d-2026-08-24-2400.csv")
DEFAULT_OUTPUT = Path(".omx/specs/autoresearch-strategy-v3/result.json")
DEFAULT_REPORT = Path("reports/krw-btc-strategy-v3-research-2026-08-25.json")


def _write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    report = build_strategy_v3_report(load_candles_csv(args.input))
    assert_finite(report)
    _write(args.output, report)
    _write(args.report, report)
    print(f"strategy-v3 artifact: {args.output}")
    print(f"nested selection: {report['nested_outer']['selected_candidate']}; finalist: {report['selection']['research_finalist']}; live: cash")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
