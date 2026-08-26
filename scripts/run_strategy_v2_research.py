#!/usr/bin/env python3
"""Build the sealed KRW-BTC strategy-v2 research artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from bithumb_coin_trader.data import load_candles_csv
from bithumb_coin_trader.strategy_v2_research import assert_finite_report, build_strategy_v2_report


DEFAULT_DAILY = Path("data/krw-btc-1d-2026-08-24-2400.csv")
DEFAULT_MINUTE = Path("data/krw-btc-30m-2026-08-24-100002-raw.csv")
DEFAULT_OUTPUT = Path(".omx/specs/autoresearch-strategy-v2/result.json")
DEFAULT_REPORT = Path("reports/krw-btc-strategy-v2-research-2026-08-25.json")


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", type=Path, default=DEFAULT_DAILY)
    parser.add_argument("--minute", type=Path, default=DEFAULT_MINUTE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    report = build_strategy_v2_report(load_candles_csv(args.daily), load_candles_csv(args.minute))
    assert_finite_report(report)
    write_json(args.output, report)
    write_json(args.report, report)
    selection = report["selection"]
    print(f"strategy-v2 artifact: {args.output}")
    print(f"research candidate: {selection['research_candidate']}; live selection: cash; promotion: forbidden")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
