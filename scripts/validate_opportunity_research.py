#!/usr/bin/env python3
"""Recompute and validate the profit-first research artifact."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from bithumb_coin_trader.data import load_candles_csv
from bithumb_coin_trader.backtest import Backtester
from bithumb_coin_trader.models import Signal
from bithumb_coin_trader.opportunity_research import (
    OpportunityResearchConfig,
    _sample,
    build_report,
    candidate_registry,
)
from bithumb_coin_trader.winrate_research import normalized_settings


DEFAULT_INPUT = Path("data/krw-btc-30m-2026-08-24-100002-raw.csv")
DEFAULT_REPORT = Path(".omx/specs/autoresearch-opportunity/result.json")
DEFAULT_MIRROR = Path("reports/krw-btc-opportunity-research-2026-08-25.json")
DEFAULT_OUTPUT = Path(".omx/specs/autoresearch-opportunity/validation.json")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = REPOSITORY_ROOT / ".omx/specs/autoresearch-opportunity/holdout-ledger.json"
DEFAULT_ADDENDUM = REPOSITORY_ROOT / "reports/krw-btc-opportunity-post-selection-audit-2026-08-25.json"


def _load(path: Path) -> Mapping[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant is forbidden: {value}")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    value = json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=reject_constant,
        object_pairs_hook=unique_object,
    )
    if not isinstance(value, Mapping):
        raise ValueError("research artifact must be a JSON object")
    return value


def validate(input_path: Path, report_path: Path, mirror_path: Path) -> dict[str, Any]:
    issues: list[str] = []
    try:
        actual = _load(report_path)
        generated_at = datetime.fromisoformat(str(actual["generated_at"]))
        raw_candles = load_candles_csv(input_path)
        report_sha256 = sha256(report_path.read_bytes()).hexdigest()
        addendum = _load(DEFAULT_ADDENDUM) if DEFAULT_ADDENDUM.exists() else None
        audit_applies = (
            isinstance(addendum, Mapping)
            and addendum.get("original_report_sha256") == report_sha256
        )
        holdout = actual.get("sealed_holdout")
        opened = isinstance(holdout, Mapping) and holdout.get("opened") is True
        expected = build_report(
            raw_candles,
            generated_at=generated_at,
        )
        for key in (
            "schema_version",
            "status",
            "mission",
            "dataset",
            "development",
            "deferred_hypotheses",
        ):
            if key == "development" and audit_applies:
                # The immutable historical development result was explicitly
                # invalidated.  Current code is expected to differ after the
                # gap-reset repair; the remediation replay below is the active
                # evidence instead of silently rewriting the old artifact.
                continue
            if actual.get(key) != expected.get(key):
                issues.append(f"development artifact field differs: {key}")
        expected_protocol = dict(expected["protocol"])
        expected_protocol["holdout"] = dict(expected_protocol["holdout"])
        expected_protocol["holdout"]["opened"] = opened
        expected_limitations = list(expected["limitations"])
        if opened:
            expected_limitations[4] = (
                "The sealed 4,000-candle holdout was opened once for one finalist."
            )
        actual_manifest = actual.get("candidate_manifest")
        expected_manifest = expected.get("candidate_manifest")
        if not isinstance(actual_manifest, Mapping) or not isinstance(expected_manifest, Mapping):
            issues.append("candidate manifest is malformed")
        else:
            def identity_rows(manifest: Mapping[str, Any]) -> list[tuple[Any, Any, Any]]:
                rows = manifest.get("candidates")
                if not isinstance(rows, list):
                    return []
                return [
                    (row.get("name"), row.get("family"), row.get("class"))
                    for row in rows
                    if isinstance(row, Mapping)
                ]

            if identity_rows(actual_manifest) != identity_rows(expected_manifest):
                issues.append("candidate identities differ from current registry")
        if report_path.read_bytes() != mirror_path.read_bytes():
            issues.append("public report mirror is not byte-identical")
        if not isinstance(holdout, Mapping):
            issues.append("sealed holdout record is malformed")
        elif opened:
            ledger = _load(DEFAULT_LEDGER)
            if ledger.get("state") != "opened":
                issues.append("holdout ledger is not opened")
            if ledger.get("evaluated_candidates") != holdout.get("evaluated_candidates"):
                issues.append("holdout ledger candidate differs from report")
            if ledger.get("holdout_sha256") != holdout.get("sha256"):
                issues.append("holdout ledger hash differs from report")
            if ledger.get("dataset_sha256") != actual["dataset"]["sha256"]:
                issues.append("holdout ledger dataset hash differs from report")
            if ledger.get("candidate_manifest_sha256") != actual["candidate_manifest"]["sha256"]:
                issues.append("holdout ledger candidate manifest differs from report")
            if ledger.get("report_sha256") != report_sha256:
                issues.append("holdout ledger report hash differs from artifact")
            gap_audit = _independent_gap_audit(raw_candles)
            metadata_matches = (
                actual.get("protocol") == expected_protocol
                and actual.get("limitations") == expected_limitations
            )
            if audit_applies:
                assert isinstance(addendum, Mapping)
                ledger_sha256 = sha256(DEFAULT_LEDGER.read_bytes()).hexdigest()
                if addendum.get("original_holdout_ledger_sha256") != ledger_sha256:
                    issues.append("post-selection audit ledger hash differs")
                actual_protocol = actual.get("protocol")
                protocol_sha256 = sha256(
                    json.dumps(
                        actual_protocol,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                if addendum.get("original_protocol_sha256") != protocol_sha256:
                    issues.append("post-selection audit protocol hash differs")
                expected_protocol_binding = {
                    "present": "protocol_sha256" in ledger,
                    "impact": (
                        "The historical ledger did not commit protocol_sha256 before opening, "
                        "so the holdout evidence remains invalidated."
                    ),
                }
                if addendum.get("original_ledger_protocol_binding") != expected_protocol_binding:
                    issues.append("post-selection audit omits original ledger protocol gap")
                assert isinstance(expected_manifest, Mapping)
                if addendum.get("current_source_manifest_sha256") != expected_manifest.get("sha256"):
                    issues.append("post-selection audit current source manifest differs")
                if addendum.get("current_remediation_gap_audit") != gap_audit:
                    issues.append("post-selection remediation audit differs from independent replay")
                if not metadata_matches and addendum.get("metadata_corrections") != _metadata_corrections(
                    actual, expected_protocol, expected_limitations
                ):
                    issues.append("post-selection metadata corrections are incomplete")
                if (
                    addendum.get("passed") is not False
                    or addendum.get("status") != "invalidated"
                    or addendum.get("selection_override") != "cash"
                    or addendum.get("holdout_reuse_forbidden") is not True
                ):
                    issues.append("post-selection audit is not fail-closed")
            else:
                if not metadata_matches:
                    issues.append("opened artifact metadata differs from current protocol")
                if gap_audit["premature_reentry_count"]:
                    issues.append("interval strategy reenters before post-gap warmup")
        elif holdout.get("evaluated_candidates") or holdout.get("results"):
            issues.append("unopened holdout contains evaluation results")
        elif actual.get("protocol") != expected_protocol or actual.get("limitations") != expected_limitations:
            issues.append("sealed artifact metadata differs from current protocol")
        selection = actual.get("selection")
        if not isinstance(selection, Mapping) or selection.get("can_promote") is not False:
            issues.append("artifact does not fail closed on automatic promotion")
        if isinstance(selection, Mapping) and selection.get("paper_or_live_strategy_changed") is not False:
            issues.append("research artifact claims a strategy side effect")
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        issues.append(f"validation could not complete: {exc}")
    return {
        "schema_version": 1,
        "status": "passed" if not issues else "failed",
        "passed": not issues,
        "issues": issues,
        "report": str(report_path),
        "report_sha256": (
            sha256(report_path.read_bytes()).hexdigest() if report_path.exists() else None
        ),
        "raw_candles_recomputed": not issues,
        "holdout_ledger_valid": not issues,
        "post_selection_audit_valid": not issues,
        "automatic_promotion": "forbidden",
    }


def _metadata_corrections(
    actual: Mapping[str, Any],
    expected_protocol: Mapping[str, Any],
    expected_limitations: Sequence[Any],
) -> list[dict[str, Any]]:
    corrections: list[dict[str, Any]] = []
    actual_protocol = actual.get("protocol")
    actual_holdout = actual_protocol.get("holdout") if isinstance(actual_protocol, Mapping) else None
    expected_holdout = expected_protocol.get("holdout")
    recorded_opened = actual_holdout.get("opened") if isinstance(actual_holdout, Mapping) else None
    corrected_opened = expected_holdout.get("opened") if isinstance(expected_holdout, Mapping) else None
    if recorded_opened != corrected_opened:
        corrections.append(
            {
                "json_path": "protocol.holdout.opened",
                "recorded_value": recorded_opened,
                "corrected_value": corrected_opened,
            }
        )
    actual_limitations = actual.get("limitations")
    recorded_limitation = (
        actual_limitations[4]
        if isinstance(actual_limitations, list) and len(actual_limitations) > 4
        else None
    )
    corrected_limitation = expected_limitations[4]
    if recorded_limitation != corrected_limitation:
        corrections.append(
            {
                "json_path": "limitations[4]",
                "recorded_value": recorded_limitation,
                "corrected_value": corrected_limitation,
            }
        )
    return corrections


def _independent_gap_audit(raw_candles: Sequence[Any]) -> dict[str, int]:
    config = OpportunityResearchConfig()
    full_sample, _ = _sample(raw_candles, config)
    sample = full_sample[: config.development_count]
    factory = candidate_registry()[0]["profit_donchian_4h_70_30"]
    signals = tuple(Signal(value) for value in factory().generate(sample))
    gaps = [
        index
        for index in range(config.initial_train_count, len(sample))
        if sample[index].timestamp - sample[index - 1].timestamp != timedelta(minutes=30)
    ]
    warmup = 70 * 8
    affected_windows = 0
    premature_entries = 0
    shortest: int | None = None
    for gap in gaps:
        rearmed = False
        previous = Signal.FLAT
        local: list[int] = []
        for index in range(gap, min(len(signals), gap + warmup)):
            if not rearmed:
                if signals[index] is Signal.FLAT:
                    rearmed = True
                    previous = Signal.FLAT
                continue
            if previous is Signal.FLAT and signals[index] is Signal.LONG:
                local.append(index - gap)
            previous = signals[index]
        if local:
            affected_windows += 1
            premature_entries += len(local)
            local_minimum = min(local)
            shortest = local_minimum if shortest is None else min(shortest, local_minimum)
    result = Backtester(
        normalized_settings(),
        allow_short=False,
        expected_interval=timedelta(minutes=30),
    ).run(sample, signals)
    gap_position_failures = sum(
        result.position_curve[index] is not Signal.FLAT for index in gaps
    )
    return {
        "source_gap_count": len(gaps),
        "required_warmup_source_bars": warmup,
        "gap_windows_with_premature_reentry": affected_windows,
        "premature_reentry_count": premature_entries,
        "shortest_reentry_distance_bars": shortest or 0,
        "backtester_gap_position_flat_failures": gap_position_failures,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--mirror", type=Path, default=DEFAULT_MIRROR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    result = validate(args.input, args.report, args.mirror)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
