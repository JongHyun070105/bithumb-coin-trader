#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from bithumb_coin_trader.redundancy_soak import run_accelerated_soak


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local accelerated Bithumb redundancy soak")
    parser.add_argument("--virtual-seconds", type=int, default=7_200)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(run_accelerated_soak(virtual_seconds=args.virtual_seconds))
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
