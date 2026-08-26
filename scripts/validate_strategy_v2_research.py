#!/usr/bin/env python3
"""Independently recompute and validate the strategy-v2 artifact."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from bithumb_coin_trader.daily_strategy_candidates import daily_candidate_factories
from bithumb_coin_trader.data import dataset_manifest, load_candles_csv
from bithumb_coin_trader.models import Signal
from bithumb_coin_trader.strategy import (
    CompletedIntervalStrategy,
    DonchianBreakoutParameters,
    DonchianBreakoutStrategy,
)
from bithumb_coin_trader.strategy_v2_research import assert_finite_report, build_strategy_v2_report


DEFAULT_DAILY = Path("data/krw-btc-1d-2026-08-24-2400.csv")
DEFAULT_MINUTE = Path("data/krw-btc-30m-2026-08-24-100002-raw.csv")
DEFAULT_REPORT = Path(".omx/specs/autoresearch-strategy-v2/result.json")
DEFAULT_MIRROR = Path("reports/krw-btc-strategy-v2-research-2026-08-25.json")
DEFAULT_OUTPUT = Path(".omx/specs/autoresearch-strategy-v2/validation.json")


def load_strict(path: Path) -> Mapping[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant is forbidden: {value}")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    value = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant, object_pairs_hook=unique)
    if not isinstance(value, Mapping):
        raise ValueError("research artifact must be an object")
    return value


def validate(daily_path: Path, minute_path: Path, report_path: Path, mirror_path: Path) -> dict[str, Any]:
    issues: list[str] = []
    try:
        actual = load_strict(report_path)
        generated_at = datetime.fromisoformat(str(actual["generated_at"]))
        daily_candles = load_candles_csv(daily_path)
        minute_candles = load_candles_csv(minute_path)
        expected = build_strategy_v2_report(
            daily_candles,
            minute_candles,
            generated_at=generated_at,
        )
        assert_finite_report(actual)
        if actual != expected:
            issues.append("artifact differs from deterministic raw-data recomputation")
        issues.extend(_independent_audit(actual, daily_candles, minute_candles))
        if report_path.read_bytes() != mirror_path.read_bytes():
            issues.append("public report mirror is not byte-identical")
        holdout = actual.get("datasets", {}).get("daily_sealed_holdout", {})
        if not isinstance(holdout, Mapping) or holdout.get("opened") is not False or holdout.get("results") != []:
            issues.append("daily holdout is not sealed")
        selection = actual.get("selection", {})
        if not isinstance(selection, Mapping) or selection.get("can_promote") is not False:
            issues.append("automatic promotion is not fail-closed")
        if isinstance(selection, Mapping) and selection.get("paper_or_live_strategy_changed") is not False:
            issues.append("artifact claims a live or paper side effect")
        audits = actual.get("daily_prefix_audits", {})
        if not isinstance(audits, Mapping) or any(not row.get("passed") for row in audits.values() if isinstance(row, Mapping)):
            issues.append("daily prefix/lookahead audit failed")
        minute_audit = actual.get("minute_development_audit", {})
        if not isinstance(minute_audit, Mapping) or minute_audit.get("long_signal_bars_inside_warmup") != 0:
            issues.append("30-minute gap-reset warmup audit failed")
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        issues.append(f"validation could not complete: {exc}")
    return {
        "schema_version": 1,
        "status": "passed" if not issues else "failed",
        "passed": not issues,
        "issues": issues,
        "report": str(report_path),
        "report_sha256": sha256(report_path.read_bytes()).hexdigest() if report_path.exists() else None,
        "raw_data_recomputed": not issues,
        "automatic_promotion": "forbidden",
    }


def _independent_audit(
    report: Mapping[str, Any], daily_candles: Sequence[Any], minute_candles: Sequence[Any]
) -> list[str]:
    """Check critical boundaries without calling the report builder.

    The full deterministic comparison above detects alteration.  These checks
    separately encode the sealed split, candidate population, prefix behavior,
    cost monotonicity, and source dependency contract so a shared builder bug
    cannot make those invariants pass merely by reproducing itself.
    """

    issues: list[str] = []
    daily = tuple(daily_candles[-2_400:])
    if len(daily) != 2_400 or any(
        daily[index].timestamp - daily[index - 1].timestamp != timedelta(days=1)
        for index in range(1, len(daily))
    ):
        return ["independent audit: daily sample is not 2,400 gap-free candles"]
    development = daily[:2_220]
    sealed = daily[2_220:]
    datasets = report.get("datasets")
    if not isinstance(datasets, Mapping):
        return ["independent audit: dataset manifest is missing"]
    for key, candles in (
        ("daily_full", daily),
        ("daily_development", development),
        ("daily_sealed_holdout", sealed),
    ):
        row = datasets.get(key)
        identity = dataset_manifest(candles)
        if not isinstance(row, Mapping) or row.get("candle_count") != identity.candle_count or row.get("sha256") != identity.sha256:
            issues.append(f"independent audit: {key} boundary/hash differs")

    aligned_minute = tuple(
        candle
        for candle in minute_candles
        if candle.timestamp.minute % 30 == 0
        and candle.timestamp.second == 0
        and candle.timestamp.microsecond == 0
    )[-100_000:]
    gaps = sum(
        aligned_minute[index].timestamp - aligned_minute[index - 1].timestamp
        != timedelta(minutes=30)
        for index in range(1, 96_000)
    )
    minute_audit = report.get("minute_development_audit")
    if not isinstance(minute_audit, Mapping) or minute_audit.get("source_gap_count") != gaps:
        issues.append("independent audit: 30-minute gap count differs")
    minute_development = aligned_minute[:96_000]
    gap_indices = [
        index
        for index in range(1, len(minute_development))
        if minute_development[index].timestamp - minute_development[index - 1].timestamp
        != timedelta(minutes=30)
    ]
    independent_strategy = CompletedIntervalStrategy(
        DonchianBreakoutStrategy(DonchianBreakoutParameters(70, 30)),
        source_minutes=30,
        target_minutes=240,
    )
    independent_signals = tuple(
        Signal(value) for value in independent_strategy.generate(minute_development)
    )
    independent_premature = sum(
        signal is Signal.LONG
        for gap in gap_indices
        for signal in independent_signals[gap : min(len(independent_signals), gap + 560)]
    )
    if (
        not isinstance(minute_audit, Mapping)
        or minute_audit.get("required_post_gap_warmup_source_bars") != 560
        or minute_audit.get("long_signal_bars_inside_warmup") != independent_premature
        or independent_premature
    ):
        issues.append("independent audit: 30-minute gap-reset warmup differs or reenters early")

    factories = daily_candidate_factories()
    expected_names = set(factories)
    rows = report.get("daily_candidates")
    if not isinstance(rows, list) or {row.get("name") for row in rows if isinstance(row, Mapping)} != expected_names:
        issues.append("independent audit: frozen daily candidate population differs")
    else:
        for row in rows:
            assert isinstance(row, Mapping)
            name = str(row["name"])
            strategy = factories[name]()
            full = tuple(Signal(value) for value in strategy.generate(development))
            checkpoints = sorted(
                set(range(max(2, strategy.required_history_bars), len(development) + 1, 97))
                | {len(development)}
            )
            mismatch = 0
            for end in checkpoints:
                prefix = tuple(Signal(value) for value in strategy.generate(development[:end]))
                mismatch += sum(left is not right for left, right in zip(prefix, full[:end]))
            audits = report.get("daily_prefix_audits")
            audit = audits.get(name) if isinstance(audits, Mapping) else None
            if not isinstance(audit, Mapping) or audit.get("mismatch_count") != mismatch or mismatch:
                issues.append(f"independent audit: prefix mismatch for {name}")
            base = row.get("base")
            cost2 = row.get("cost_x2")
            cost3 = row.get("cost_x3")
            if not all(isinstance(item, Mapping) for item in (base, cost2, cost3)):
                issues.append(f"independent audit: cost rows missing for {name}")
                continue
            assert isinstance(base, Mapping) and isinstance(cost2, Mapping) and isinstance(cost3, Mapping)
            if not (
                float(base["final_equity_krw"])
                >= float(cost2["final_equity_krw"])
                >= float(cost3["final_equity_krw"])
            ):
                issues.append(f"independent audit: higher costs improve equity for {name}")
            if not (
                float(base["total_fees_krw"])
                <= float(cost2["total_fees_krw"])
                <= float(cost3["total_fees_krw"])
            ):
                issues.append(f"independent audit: fee ledger is not monotone for {name}")

    statistics = report.get("multiple_testing")
    dsr = statistics.get("deflated_sharpe") if isinstance(statistics, Mapping) else None
    if not isinstance(dsr, Mapping) or dsr.get("status") != "unavailable":
        issues.append("independent audit: DSR must remain unavailable without prior trial returns")

    manifest = report.get("source_manifest")
    files = manifest.get("files") if isinstance(manifest, Mapping) else None
    recorded_paths = {
        item.get("path") for item in files if isinstance(item, Mapping)
    } if isinstance(files, list) else set()
    required_paths = {
        "src/bithumb_coin_trader/strategy_v2_research.py",
        "src/bithumb_coin_trader/backtest.py",
        "src/bithumb_coin_trader/daily_strategy_candidates.py",
        "src/bithumb_coin_trader/research_statistics.py",
        "src/bithumb_coin_trader/strategy.py",
        "src/bithumb_coin_trader/config.py",
        "src/bithumb_coin_trader/data.py",
        "src/bithumb_coin_trader/models.py",
        "src/bithumb_coin_trader/risk.py",
        "scripts/run_strategy_v2_research.py",
        "scripts/validate_strategy_v2_research.py",
    }
    if not required_paths.issubset(recorded_paths):
        issues.append("independent audit: source manifest omits result-affecting files")
    return issues


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", type=Path, default=DEFAULT_DAILY)
    parser.add_argument("--minute", type=Path, default=DEFAULT_MINUTE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--mirror", type=Path, default=DEFAULT_MIRROR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    result = validate(args.daily, args.minute, args.report, args.mirror)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(f"strategy-v2 validation: {result['status']}")
    for issue in result["issues"]:
        print(f"- {issue}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
