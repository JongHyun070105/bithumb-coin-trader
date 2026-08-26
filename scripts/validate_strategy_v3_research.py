#!/usr/bin/env python3
"""Validate Strategy V3 by raw-data replay and independent invariants."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from bithumb_coin_trader.data import dataset_manifest, load_candles_csv
from bithumb_coin_trader.strategy_v3_candidates import strategy_v3_candidate_factories
from bithumb_coin_trader.strategy_v3_research import assert_finite, build_strategy_v3_report


DEFAULT_INPUT = Path("data/krw-btc-1d-2026-08-24-2400.csv")
DEFAULT_REPORT = Path(".omx/specs/autoresearch-strategy-v3/result.json")
DEFAULT_MIRROR = Path("reports/krw-btc-strategy-v3-research-2026-08-25.json")
DEFAULT_OUTPUT = Path(".omx/specs/autoresearch-strategy-v3/validation.json")


def _load(path: Path) -> Mapping[str, Any]:
    def reject(value: str) -> None:
        raise ValueError(f"non-finite JSON value: {value}")
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    value = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject, object_pairs_hook=unique)
    if not isinstance(value, Mapping):
        raise ValueError("V3 artifact must be an object")
    return value


def validate(input_path: Path, report_path: Path, mirror_path: Path) -> dict[str, Any]:
    issues: list[str] = []
    try:
        actual = _load(report_path)
        candles = load_candles_csv(input_path)
        expected = build_strategy_v3_report(candles, generated_at=datetime.fromisoformat(str(actual["generated_at"])))
        assert_finite(actual)
        if actual != expected:
            issues.append("artifact differs from deterministic raw-data replay")
        if report_path.read_bytes() != mirror_path.read_bytes():
            issues.append("public mirror is not byte-identical")
        issues.extend(_independent_invariants(actual, candles))
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


def _independent_invariants(report: Mapping[str, Any], candles: Sequence[Any]) -> list[str]:
    issues: list[str] = []
    sample = tuple(candles[-2_400:])
    if len(sample) != 2_400 or any(sample[i].timestamp - sample[i-1].timestamp != timedelta(days=1) for i in range(1, len(sample))):
        return ["independent audit: daily sample boundary invalid"]
    development, sealed = sample[:2_220], sample[2_220:]
    dataset = report.get("dataset")
    holdout = dataset.get("sealed_holdout") if isinstance(dataset, Mapping) else None
    sealed_identity = dataset_manifest(sealed)
    if not isinstance(holdout, Mapping) or holdout.get("sha256") != sealed_identity.sha256 or holdout.get("opened") is not False or holdout.get("results") != []:
        issues.append("independent audit: sealed holdout boundary or state differs")
    rows = report.get("direct_development_diagnostics")
    expected_names = set(strategy_v3_candidate_factories())
    if not isinstance(rows, list) or {row.get("name") for row in rows if isinstance(row, Mapping)} != expected_names:
        issues.append("independent audit: candidate population differs")
    elif isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            base, x2, x3 = row.get("base"), row.get("cost_x2"), row.get("cost_x3")
            if not all(isinstance(value, Mapping) for value in (base, x2, x3)):
                issues.append(f"independent audit: missing costs for {row.get('name')}")
                continue
            assert isinstance(base, Mapping) and isinstance(x2, Mapping) and isinstance(x3, Mapping)
            if not float(base["final_equity_krw"]) >= float(x2["final_equity_krw"]) >= float(x3["final_equity_krw"]):
                issues.append(f"independent audit: cost monotonicity failed for {row.get('name')}")
            if float(base["minimum_cash_krw"]) < 5_000 - 1e-6 or float(base["minimum_base_quantity"]) < 0:
                issues.append(f"independent audit: ledger invariant failed for {row.get('name')}")
    audits = report.get("prefix_audits")
    if not isinstance(audits, Mapping) or set(audits) != expected_names or any(not row.get("passed") for row in audits.values() if isinstance(row, Mapping)):
        issues.append("independent audit: prefix audit failed")
    nested = report.get("nested_outer")
    if not isinstance(nested, Mapping) or len(nested.get("selections", [])) != 3 or len(nested.get("folds", [])) != 3:
        issues.append("independent audit: nested outer structure differs")
    else:
        for selection in nested["selections"]:
            if selection["test_start"] < selection["train_end_exclusive"] or selection["test_end_exclusive"] - selection["test_start"] != 300:
                issues.append("independent audit: outer boundary leaks or has wrong length")
    selection = report.get("selection")
    if not isinstance(selection, Mapping) or selection.get("can_promote") is not False or selection.get("paper_or_live_strategy_changed") is not False:
        issues.append("independent audit: automatic promotion is not fail-closed")
    return issues


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--mirror", type=Path, default=DEFAULT_MIRROR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    result = validate(args.input, args.report, args.mirror)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(f"strategy-v3 validation: {result['status']}")
    for issue in result["issues"]:
        print(f"- {issue}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
