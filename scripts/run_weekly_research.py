#!/usr/bin/env python3
"""Run the isolated validator-gated weekly Bithumb research job."""

from __future__ import annotations

from pathlib import Path

from bithumb_coin_trader.weekly_research import run_weekly_research


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    result = run_weekly_research(project_root)
    print(f"weekly research: {result.status}; candidate={result.research_candidate}; promote=false")
    return 0 if result.status in {"completed", "skipped_duplicate"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
