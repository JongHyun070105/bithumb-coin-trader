#!/usr/bin/env python3
"""Run the bounded public Binance connectivity diagnostic."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Sequence

from bithumb_coin_trader.binance_diagnostic import (
    BINANCE_PORT,
    OFFICIAL_BINANCE_PORTS,
    run_diagnostic,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--port", type=int, choices=OFFICIAL_BINANCE_PORTS, default=BINANCE_PORT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    report = asyncio.run(run_diagnostic(timeout=args.timeout, port=args.port))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["all_symbol_handshakes_passed"] and report["production_combined_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
