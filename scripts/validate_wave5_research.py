#!/usr/bin/env python3
"""Validate a Wave 5 artifact without trusting its reported gate decisions."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from math import isclose, isfinite
from pathlib import Path
from typing import Any, Mapping, Sequence

from bithumb_coin_trader.data import dataset_manifest, load_candles_csv
from bithumb_coin_trader.wave5 import (
    WAVE5_CANDIDATE_NAMES,
    WAVE5_RUNNABLE_BTC_CANDIDATES,
    Wave5Config,
    wave5_candidate_manifest,
    wave5_candidate_manifest_hash,
)


DEFAULT_REPORT = Path(".omx/specs/autoresearch-wave5/result.json")
DEFAULT_DATA = Path("data/krw-btc-30m-2026-08-14-wave4.csv")
DEFAULT_OUTPUT = Path(".omx/specs/autoresearch-wave5/validation.json")
ABS_TOLERANCE = 1e-9


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(float(value))
    )


def _close(left: object, right: float) -> bool:
    return _number(left) and isclose(float(left), right, rel_tol=1e-9, abs_tol=ABS_TOLERANCE)


def _maximum_drawdown(curve: Sequence[float]) -> float:
    peak = curve[0]
    result = 0.0
    for value in curve:
        peak = max(peak, value)
        result = max(result, (peak - value) / peak if peak > 0 else 0.0)
    return result


def _validate_manifest(payload: Mapping[str, Any], issues: list[str]) -> None:
    supplied = _mapping(payload.get("candidate_manifest"))
    if supplied is None:
        issues.append("candidate manifest is missing")
        return
    canonical = dict(supplied)
    declared_hash = canonical.pop("sha256", None)
    candidate_count = canonical.pop("candidate_count", None)
    expected = wave5_candidate_manifest()
    if canonical != expected:
        issues.append("candidate manifest differs from the Wave 5 runtime contract")
    if declared_hash != wave5_candidate_manifest_hash(expected):
        issues.append("candidate manifest SHA-256 is invalid")
    if candidate_count != len(WAVE5_CANDIDATE_NAMES):
        issues.append("candidate manifest count is invalid")


def _validate_dataset(
    payload: Mapping[str, Any],
    issues: list[str],
    expected_dataset: Mapping[str, Any] | None,
) -> None:
    dataset = _mapping(payload.get("dataset"))
    if dataset is None:
        issues.append("dataset identity is missing")
        return
    if dataset.get("markets") != ["KRW-BTC"] or dataset.get("market_count") != 1:
        issues.append("Wave 5 artifact must truthfully identify the BTC-only dataset")
    if dataset.get("candle_count") != Wave5Config().historical_count:
        issues.append("dataset candle count is invalid")
    digest = dataset.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        issues.append("dataset SHA-256 is invalid")
    if expected_dataset is not None:
        for key in ("candle_count", "start_at", "end_at", "sha256"):
            if dataset.get(key) != expected_dataset.get(key):
                issues.append(f"dataset {key} differs from the supplied CSV")

    availability = _mapping(payload.get("candidate_availability"))
    unavailable = _mapping(availability.get("unavailable")) if availability else None
    cross = _mapping(unavailable.get("cross_sectional_momentum")) if unavailable else None
    if (
        not availability
        or availability.get("runnable") != list(WAVE5_RUNNABLE_BTC_CANDIDATES)
        or not cross
        or cross.get("observed_market_count") != 1
        or cross.get("result", "missing") is not None
    ):
        issues.append("cross-sectional momentum unavailability is not recorded correctly")


def _validate_metric(
    metric: object,
    *,
    label: str,
    expected_boundaries: Sequence[tuple[int, int, int, int]],
    issues: list[str],
) -> Mapping[str, Any] | None:
    value = _mapping(metric)
    if value is None:
        issues.append(f"{label} metrics are missing")
        return None
    evidence = _mapping(value.get("oos_equity_evidence"))
    expected_points = Wave5Config().test_size * Wave5Config().fold_count + 1
    if evidence is None or evidence.get("point_count") != expected_points:
        issues.append(f"{label} OOS equity evidence is invalid")
        return value
    initial = evidence.get("initial_equity_krw")
    final = evidence.get("final_equity_krw")
    digest = evidence.get("sha256")
    if (
        not _number(initial)
        or float(initial) <= 0
        or not _number(final)
        or float(final) <= 0
        or not isinstance(digest, str)
        or len(digest) != 64
    ):
        issues.append(f"{label} OOS equity evidence is invalid")
        return value
    expected_return = float(final) / float(initial) - 1.0
    if not _close(value.get("compounded_return"), expected_return):
        issues.append(f"{label} compounded return does not match its endpoints")
    if not _number(value.get("maximum_drawdown")) or not 0 <= float(
        value["maximum_drawdown"]
    ) <= 1:
        issues.append(f"{label} drawdown is invalid")
    folds = value.get("folds")
    if not isinstance(folds, list) or len(folds) != len(expected_boundaries):
        issues.append(f"{label} folds are missing")
        return value
    for index, (fold, boundary) in enumerate(zip(folds, expected_boundaries)):
        if not isinstance(fold, Mapping):
            issues.append(f"{label} fold {index + 1} is invalid")
            continue
        train_start, train_end, test_start, test_end = boundary
        if (
            fold.get("fold") != index + 1
            or fold.get("train") != [train_start, train_end]
            or fold.get("test") != [test_start, test_end]
        ):
            issues.append(f"{label} fold {index + 1} boundary is invalid")
        fold_initial = fold.get("initial_equity_krw")
        fold_final = fold.get("final_equity_krw")
        if not _number(fold_initial) or not _number(fold_final) or float(fold_initial) <= 0:
            issues.append(f"{label} fold {index + 1} endpoint is invalid")
            continue
        if index == 0 and not _close(fold_initial, float(initial)):
            issues.append(f"{label} first fold does not match initial equity")
        if index == len(folds) - 1 and not _close(fold_final, float(final)):
            issues.append(f"{label} last fold does not match final equity")
        if index and not _close(
            fold_initial, float(folds[index - 1]["final_equity_krw"])
        ):
            issues.append(f"{label} fold {index + 1} equity is discontinuous")
        fold_return = float(fold_final) / float(fold_initial) - 1.0
        if not _close(fold.get("total_return"), fold_return):
            issues.append(f"{label} fold {index + 1} return is invalid")
    return value


def _expected_gate(
    base: Mapping[str, Any], stress: Mapping[str, Any]
) -> tuple[dict[str, bool], dict[str, float | int]]:
    config = Wave5Config()
    folds = base["folds"]
    positive = sum(float(fold["total_return"]) > 0 for fold in folds)
    closed = int(base["closed_trade_count"])
    checks = {
        "base_return_gt_cash": float(base["compounded_return"]) > 0.0,
        "double_cost_return_gt_cash": float(stress["compounded_return"]) > 0.0,
        "maximum_drawdown_lte": float(base["maximum_drawdown"]) <= config.maximum_drawdown,
        "positive_base_folds_gte": positive >= config.minimum_positive_folds,
        "closed_trades_gte": closed >= config.minimum_closed_trades,
    }
    actual = {
        "base_return": float(base["compounded_return"]),
        "double_cost_return": float(stress["compounded_return"]),
        "maximum_drawdown": float(base["maximum_drawdown"]),
        "positive_base_folds": positive,
        "closed_trades": closed,
    }
    return checks, actual


def _validate_candidates(payload: Mapping[str, Any], issues: list[str]) -> list[str]:
    rows = payload.get("candidates")
    if not isinstance(rows, list):
        issues.append("candidate results are missing")
        return []
    names = [row.get("name") for row in rows if isinstance(row, Mapping)]
    if names != list(WAVE5_RUNNABLE_BTC_CANDIDATES):
        issues.append("runnable candidate result set is invalid")
        return []
    expected_boundaries = Wave5Config().boundaries()
    eligible: list[tuple[str, float, float]] = []
    for row in rows:
        assert isinstance(row, Mapping)
        name = str(row["name"])
        base = _validate_metric(
            row.get("base"), label=f"{name} base", expected_boundaries=expected_boundaries, issues=issues
        )
        stress = _validate_metric(
            row.get("double_cost_stress"),
            label=f"{name} stress",
            expected_boundaries=expected_boundaries,
            issues=issues,
        )
        gate = _mapping(row.get("gate_evaluation"))
        if base is None or stress is None or gate is None:
            issues.append(f"{name} gate evaluation is missing")
            continue
        try:
            checks, actual = _expected_gate(base, stress)
        except (KeyError, TypeError, ValueError):
            issues.append(f"{name} gate inputs are invalid")
            continue
        if (
            gate.get("checks") != checks
            or gate.get("actual") != actual
            or gate.get("passed") != all(checks.values())
        ):
            issues.append(f"{name} gate evaluation is inconsistent")
        if name == "cash":
            if not _close(base.get("compounded_return"), 0.0) or int(
                base.get("trade_count", -1)
            ) != 0:
                issues.append("cash control is not flat")
        elif all(checks.values()):
            eligible.append(
                (name, float(stress["compounded_return"]), float(base["compounded_return"]))
            )
    eligible.sort(key=lambda item: (item[1], item[2], item[0]), reverse=True)
    return [item[0] for item in eligible]


def _validate_selection(
    payload: Mapping[str, Any], eligible: Sequence[str], issues: list[str]
) -> None:
    selection = _mapping(payload.get("selection"))
    expected = eligible[0] if eligible else "cash"
    if selection is None or selection.get("research_candidate") != expected:
        issues.append("selection does not fail to cash or choose the strongest eligible candidate")
        return
    if (
        selection.get("fallback_to_cash") is not (expected == "cash")
        or selection.get("automatic_promotion") != "forbidden"
        or selection.get("can_promote") is not False
        or selection.get("paper_or_live_strategy_changed") is not False
    ):
        issues.append("automatic promotion boundary is invalid")


def validate_report(
    payload: Mapping[str, Any], *, expected_dataset: Mapping[str, Any] | None = None
) -> list[str]:
    issues: list[str] = []
    if payload.get("schema_version") != 5 or payload.get("status") != "RESEARCH_ONLY":
        issues.append("report schema or research-only status is invalid")
    _validate_manifest(payload, issues)
    _validate_dataset(payload, issues, expected_dataset)
    geometry = _mapping(payload.get("validation_geometry"))
    expected_geometry = [
        {"train": [a, b], "test": [c, d]}
        for a, b, c, d in Wave5Config().boundaries()
    ]
    if (
        geometry is None
        or geometry.get("expanding") is not True
        or geometry.get("boundaries") != expected_geometry
    ):
        issues.append("validation geometry is invalid")
    if payload.get("costs") != wave5_candidate_manifest()["costs"]:
        issues.append("base or double-cost stress assumptions are invalid")
    eligible = _validate_candidates(payload, issues)
    _validate_selection(payload, eligible, issues)
    return issues


def _dataset_identity(path: Path) -> dict[str, Any]:
    candles = load_candles_csv(path)[-Wave5Config().historical_count :]
    identity = dataset_manifest(candles)
    return {
        "candle_count": identity.candle_count,
        "start_at": identity.start_at.isoformat() if identity.start_at else None,
        "end_at": identity.end_at.isoformat() if identity.end_at else None,
        "sha256": identity.sha256,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    raw = args.report.read_bytes()
    payload = json.loads(raw)
    issues = validate_report(payload, expected_dataset=_dataset_identity(args.data))
    if not issues:
        try:
            import run_wave5_research as runner

            generated_at = payload.get("generated_at")
            if not isinstance(generated_at, str):
                raise ValueError("generated_at is missing")
            from datetime import datetime

            expected = runner.build_report(
                load_candles_csv(args.data),
                generated_at=datetime.fromisoformat(generated_at.replace("Z", "+00:00")),
            )
            if payload != expected:
                issues.append("report differs from a deterministic rerun on the supplied CSV")
        except (ImportError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            issues.append(f"could not reproduce Wave 5 report: {exc}")
    artifact = {
        "schema_version": 1,
        "report": str(args.report),
        "report_sha256": sha256(raw).hexdigest(),
        "validator": "scripts/validate_wave5_research.py",
        "status": "PASS" if not issues else "FAIL",
        "issues": issues,
        "automatic_promotion": "forbidden",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if issues:
        for issue in issues:
            print(f"FAIL: {issue}")
        return 1
    print(f"PASS: {args.report}")
    print(f"validator artifact: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
