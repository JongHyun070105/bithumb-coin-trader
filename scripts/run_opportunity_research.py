#!/usr/bin/env python3
"""Run profit-first research while keeping the final 4,000 candles sealed."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Mapping, Any, Sequence

from bithumb_coin_trader.data import load_candles_csv
from bithumb_coin_trader.opportunity_research import (
    HoldoutLedgerExistsError,
    build_report,
    open_holdout_once,
)


DEFAULT_INPUT = Path("data/krw-btc-30m-2026-08-24-100002-raw.csv")
DEFAULT_OUTPUT = Path(".omx/specs/autoresearch-opportunity/result.json")
DEFAULT_REPORT = Path("reports/krw-btc-opportunity-research-2026-08-25.json")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = REPOSITORY_ROOT / ".omx/specs/autoresearch-opportunity/holdout-ledger.json"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--open-holdout", action="store_true")
    args = parser.parse_args(argv)
    if os.path.lexists(DEFAULT_LEDGER):
        raise HoldoutLedgerExistsError(
            f"research run refused because holdout ledger exists at {DEFAULT_LEDGER}"
        )
    candles = load_candles_csv(args.input)
    report = build_report(candles)
    opening_ledger: dict[str, Any] | None = None
    if args.open_holdout and report["development"]["finalists"]:
        generated_at = datetime.fromisoformat(str(report["generated_at"]))
        report, opening_ledger = open_holdout_once(
            candles,
            DEFAULT_LEDGER,
            generated_at=generated_at,
        )
    _write_json(args.output, report)
    _write_json(args.report, report)
    if opening_ledger is not None:
        opening_ledger.update(
            {
                "state": "opened",
                "opened_at": datetime.now(UTC).isoformat(),
                "evaluated_candidates": report["sealed_holdout"]["evaluated_candidates"],
                "report_sha256": sha256(args.output.read_bytes()).hexdigest(),
            }
        )
        _write_json(DEFAULT_LEDGER, opening_ledger)
    print(f"opportunity artifact: {args.output}")
    print(
        "candidate: "
        f"{report['selection']['research_candidate']}; "
        f"finalist found: {report['selection']['historical_finalist_found']}; "
        "automatic promotion: forbidden"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
