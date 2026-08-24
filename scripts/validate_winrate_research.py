#!/usr/bin/env python3
"""Independently recompute and validate the selective win-rate artifact.

This validator does not call the report builder, its metric helpers, or its
gate helpers. It regenerates signals from raw candles and runs Backtester
directly before applying separately coded metrics and gates.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from hashlib import sha256
import importlib
import inspect
import json
from math import ceil, sqrt
import os
from pathlib import Path
import plistlib
import re
import subprocess
from typing import Any, Callable, Mapping, Sequence

from bithumb_coin_trader.backtest import BacktestResult, Backtester
from bithumb_coin_trader.config import TradingSettings
from bithumb_coin_trader.data import dataset_manifest, load_candles_csv
from bithumb_coin_trader.models import Candle, Signal
from bithumb_coin_trader.winrate_research import WinRateResearchConfig, candidate_registry


DEFAULT_INPUT = Path("data/krw-btc-30m-2026-08-24-winrate.csv")
DEFAULT_REPORT = Path(".omx/specs/autoresearch-winrate70/result.json")
DEFAULT_MIRROR = Path("reports/krw-btc-winrate70-research-2026-08-24.json")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = (
    REPOSITORY_ROOT
    / ".omx/specs/autoresearch-winrate70/holdout-ledger.json"
)
DEFAULT_OUTPUT = Path(".omx/specs/autoresearch-winrate70/validation.json")
SOURCE_DELTA = timedelta(minutes=30)
CandidateFactory = Callable[[], Any]


class ValidationError(ValueError):
    pass


def _reject_constant(value: str) -> None:
    raise ValidationError(f"non-finite JSON constant is forbidden: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValidationError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def load_strict_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ValidationError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ValidationError("research artifact must be a JSON object")
    return value


def _validate_impl(
    *,
    input_path: Path,
    report_path: Path,
    mirror_path: Path,
    ledger_path: Path = DEFAULT_LEDGER,
    config: WinRateResearchConfig | None = None,
    factories: Mapping[str, CandidateFactory] | None = None,
    families: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    issues: list[str] = []
    report = load_strict_json(report_path)
    selected = config or WinRateResearchConfig()
    if factories is None or families is None:
        default_factories, default_families = candidate_registry()
        factories = default_factories if factories is None else factories
        families = default_families if families is None else families
    if set(factories) != set(families):
        raise ValidationError("candidate names and family labels differ")

    candles = tuple(load_candles_csv(input_path))
    expected = _independent_recompute(candles, selected, factories, families, report)
    _compare_report(report, expected, issues)
    _validate_holdout_ledger(report, report_path, ledger_path, issues)

    try:
        if report_path.read_bytes() != mirror_path.read_bytes():
            issues.append("public report mirror is not byte-identical to the completion result")
    except OSError as exc:
        issues.append(f"public report mirror is unreadable: {exc}")

    live_evidence = _live_entry_off_evidence()
    if not all(
        live_evidence[key]
        for key in ("installed_plist_off", "installed_wrapper_off", "launchctl_off")
    ):
        issues.append("installed daemon does not prove BITHUMB_NEW_ENTRIES=false")

    return {
        "schema_version": 1,
        "status": "passed" if not issues else "failed",
        "passed": not issues,
        "summary": (
            "raw-candle metrics/gates recomputation and live-entry lock passed"
            if not issues
            else "research validation failed"
        ),
        "issues": issues,
        "report": str(report_path),
        "report_sha256": _file_sha256(report_path),
        "dataset_sha256": expected["dataset"]["sha256"],
        "candidate_manifest_sha256": expected["candidate_manifest"]["sha256"],
        "protocol_sha256": _canonical_hash(expected["protocol"]),
        "validator_sha256": _file_sha256(Path(__file__)),
        "metrics_and_gates_recomputed_independently": not any(
            "differs" in issue for issue in issues
        ),
        "shared_execution_engine_sha256": expected["protocol"][
            "execution_engine"
        ]["source_sha256"],
        "live_new_entries_off": live_evidence,
        "automatic_promotion": "forbidden",
    }


def validate(
    *,
    input_path: Path,
    report_path: Path,
    mirror_path: Path,
    ledger_path: Path = DEFAULT_LEDGER,
    config: WinRateResearchConfig | None = None,
    factories: Mapping[str, CandidateFactory] | None = None,
    families: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return a failed result, rather than an exception, for malformed evidence."""

    try:
        return _validate_impl(
            input_path=input_path,
            report_path=report_path,
            mirror_path=mirror_path,
            ledger_path=ledger_path,
            config=config,
            factories=factories,
            families=families,
        )
    except (
        ValidationError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        IndexError,
    ) as exc:
        return {
            "schema_version": 1,
            "status": "failed",
            "passed": False,
            "summary": "research validation could not complete",
            "issues": [str(exc)],
            "report": str(report_path),
            "report_sha256": _file_sha256(report_path),
            "metrics_and_gates_recomputed_independently": False,
            "automatic_promotion": "forbidden",
        }


def _compare_report(
    report: Mapping[str, Any], expected: Mapping[str, Any], issues: list[str]
) -> None:
    required = {
        "schema_version", "generated_at", "status", "dataset", "candidate_manifest",
        "protocol", "development", "sealed_holdout", "selection",
        "external_research", "limitations",
    }
    missing = sorted(required.difference(report))
    if missing:
        issues.append(f"missing top-level fields: {', '.join(missing)}")
    try:
        stamp = datetime.fromisoformat(str(report["generated_at"]))
        if stamp.tzinfo is None:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        issues.append("generated_at is not a timezone-aware ISO timestamp")
    if report.get("schema_version") != 1 or report.get("status") != "RESEARCH_ONLY":
        issues.append("report schema or RESEARCH_ONLY status is invalid")
    messages = {
        "dataset": "dataset identity or gap evidence differs from raw candles",
        "candidate_manifest": "candidate manifest or source hash differs from current code",
        "protocol": "research protocol or protocol hash input differs from frozen contract",
        "development": "development candidate metrics, gates, ranking, or near-misses differ",
        "sealed_holdout": "holdout opening, selection, metrics, or gates differ",
        "selection": "research selection or promotion boundary differs",
        "external_research": "external research evidence differs from the frozen manifest",
        "limitations": "research limitations differ from the frozen safety disclosure",
    }
    for key, message in messages.items():
        if report.get(key) != expected[key]:
            issues.append(message)


def _validate_holdout_ledger(
    report: Mapping[str, Any], report_path: Path, ledger_path: Path, issues: list[str]
) -> None:
    holdout = report.get("sealed_holdout")
    if not isinstance(holdout, Mapping):
        return
    if not holdout.get("opened"):
        if os.path.lexists(ledger_path):
            issues.append("holdout ledger exists while report claims holdout is unopened")
        if holdout.get("evaluated_candidates") or holdout.get("results"):
            issues.append("unopened holdout contains evaluated candidates or results")
        return
    try:
        ledger = load_strict_json(ledger_path)
    except ValidationError as exc:
        issues.append(f"opened holdout lacks a valid one-time ledger: {exc}")
        return
    candidates = holdout.get("evaluated_candidates")
    if not isinstance(candidates, list) or not candidates:
        issues.append("opened holdout has no evaluated candidates")
        return
    if ledger.get("evaluated_candidates") != candidates:
        issues.append("holdout ledger candidate list differs from report")
    if ledger.get("finalists") != candidates:
        issues.append("holdout ledger finalist reservation differs from report")
    dataset = report.get("dataset")
    manifest = report.get("candidate_manifest")
    protocol = report.get("protocol")
    if not isinstance(dataset, Mapping) or not isinstance(manifest, Mapping) or not isinstance(protocol, Mapping):
        issues.append("opened holdout report has malformed integrity manifests")
        return
    if ledger.get("dataset_sha256") != dataset.get("sha256"):
        issues.append("holdout ledger dataset hash differs from report")
    if ledger.get("candidate_manifest_sha256") != manifest.get("sha256"):
        issues.append("holdout ledger candidate manifest hash differs from report")
    if ledger.get("protocol_sha256") != _canonical_hash(protocol):
        issues.append("holdout ledger protocol hash differs from report")
    if ledger.get("state") != "opened":
        issues.append("holdout ledger is not in opened state")
    if ledger.get("report_sha256") != _file_sha256(report_path):
        issues.append("holdout ledger report hash differs from exact report bytes")
    for field in ("created_at", "opened_at"):
        try:
            stamp = datetime.fromisoformat(str(ledger[field]))
            if stamp.tzinfo is None:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            issues.append(f"holdout ledger {field} is not timezone-aware")


def _independent_recompute(
    candles: Sequence[Candle], config: WinRateResearchConfig,
    factories: Mapping[str, CandidateFactory], families: Mapping[str, str],
    report: Mapping[str, Any],
) -> dict[str, Any]:
    if len(candles) != config.historical_count:
        raise ValidationError(f"supplied CSV must contain exactly {config.historical_count} candles")
    if not candles or {candle.market for candle in candles} != {"KRW-BTC"}:
        raise ValidationError("research data must be exactly KRW-BTC")
    if any(c.timestamp.second or c.timestamp.microsecond or c.timestamp.minute % 30 for c in candles):
        raise ValidationError("research candles are not aligned to 30 minutes")
    if any(candles[i].timestamp <= candles[i - 1].timestamp for i in range(1, len(candles))):
        raise ValidationError("research candles are not strictly chronological")

    base_settings = _settings(False)
    stress_settings = _settings(True)
    rows: list[dict[str, Any]] = []
    development_candles = candles[: config.development_count]
    signals_by_name: dict[str, tuple[Signal, ...]] = {}
    for name in sorted(factories):
        strategy = factories[name]()
        if getattr(strategy, "name", name) != name:
            raise ValidationError(f"candidate factory name mismatch: {name}")
        signals = tuple(Signal(value) for value in strategy.generate(development_candles))
        if len(signals) != len(development_candles):
            raise ValidationError(f"candidate signal count differs: {name}")
        if any(signal not in {Signal.FLAT, Signal.LONG} for signal in signals):
            raise ValidationError(f"candidate is not LONG/FLAT: {name}")
        signals_by_name[name] = signals
        base = _window_metrics(development_candles, signals, config.initial_train_count, config.development_count, base_settings, config.development_test_count)
        stress = _window_metrics(development_candles, signals, config.initial_train_count, config.development_count, stress_settings, config.development_test_count)
        rows.append({
            "name": name, "family": families[name], "base": base,
            "double_cost_stress": stress,
            "gate_evaluation": _development_gate(base, stress, config, name == "cash"),
        })
    ranked = sorted(
        (row for row in rows if row["name"] != "cash" and row["gate_evaluation"]["passed"]),
        key=lambda row: (row["double_cost_stress"]["total_return"], row["base"]["total_return"], row["base"]["wilson_95_lower_bound"], row["name"]),
        reverse=True,
    )
    eligible = [row["name"] for row in ranked[: config.maximum_holdout_candidates]]
    reported_holdout = report.get("sealed_holdout", {})
    opened = bool(isinstance(reported_holdout, Mapping) and reported_holdout.get("opened"))
    opened_names = eligible if opened else []
    if opened and not opened_names:
        raise ValidationError("holdout cannot be opened without development finalists")
    holdout_rows: list[dict[str, Any]] = []
    for name in opened_names:
        full_signals = tuple(Signal(value) for value in factories[name]().generate(candles))
        if full_signals[: config.development_count] != signals_by_name[name]:
            raise ValidationError(f"candidate signals are not prefix-stable: {name}")
        holdout_count = config.historical_count - config.development_count
        stride = max(1, holdout_count // 4)
        for end_at in range(config.development_count + stride, len(candles), stride):
            prefix_signals = tuple(
                Signal(value) for value in factories[name]().generate(candles[:end_at])
            )
            if len(prefix_signals) != end_at or prefix_signals != full_signals[:end_at]:
                raise ValidationError(
                    f"candidate signals are not prefix-stable inside holdout: {name}"
                )
        base = _window_metrics(candles, full_signals, config.development_count, config.historical_count, base_settings, None)
        stress = _window_metrics(candles, full_signals, config.development_count, config.historical_count, stress_settings, None)
        holdout_rows.append({
            "name": name, "base": base, "double_cost_stress": stress,
            "gate_evaluation": _holdout_gate(base, stress, config),
        })
    survivors = sorted(
        (row for row in holdout_rows if row["gate_evaluation"]["passed"]),
        key=lambda row: (row["double_cost_stress"]["total_return"], row["base"]["total_return"], row["name"]),
        reverse=True,
    )
    chosen = survivors[0]["name"] if survivors else "cash"
    near = sorted(
        (row for row in rows if row["name"] != "cash"),
        key=lambda row: (sum(row["gate_evaluation"]["checks"].values()), row["double_cost_stress"]["total_return"], row["base"]["total_return"]),
        reverse=True,
    )[:10]
    identity = dataset_manifest(candles)
    return {
        "dataset": {
            "market": identity.market, "candle_count": identity.candle_count,
            "start_at": identity.start_at.isoformat() if identity.start_at else None,
            "end_at": identity.end_at.isoformat() if identity.end_at else None,
            "sha256": identity.sha256,
            "gap_count": sum(candles[i].timestamp - candles[i - 1].timestamp != SOURCE_DELTA for i in range(1, len(candles))),
            "source": "Bithumb public completed 30-minute OHLCV",
        },
        "candidate_manifest": _candidate_manifest(factories, families),
        "protocol": _protocol(config),
        "development": {
            "candidate_count": len(rows), "candidates": rows,
            "passed_candidates": [row["name"] for row in ranked],
            "near_misses": [{
                "name": row["name"],
                "passed_check_count": sum(row["gate_evaluation"]["checks"].values()),
                "failed_checks": [key for key, passed in row["gate_evaluation"]["checks"].items() if not passed],
                "base_return": row["base"]["total_return"],
                "base_win_rate": row["base"]["win_rate"],
                "closed_trades": row["base"]["closed_trade_count"],
            } for row in near],
        },
        "sealed_holdout": {
            "count": config.sealed_holdout_count,
            "start_at": candles[config.development_count].timestamp.isoformat(),
            "end_at": candles[-1].timestamp.isoformat(),
            "opened": opened,
            "evaluated_candidates": opened_names,
            "results": holdout_rows,
        },
        "selection": {
            "research_candidate": chosen,
            "historical_target_met": chosen != "cash",
            "fallback_to_cash": chosen == "cash",
            "automatic_promotion": "forbidden", "can_promote": False,
            "paper_or_live_strategy_changed": False,
            "requires_prospective_forward_evidence": True,
            "holdout_evaluation_required": bool(ranked) and not opened,
        },
        "external_research": _external_research(),
        "limitations": _limitations(),
    }


def _settings(stress: bool) -> TradingSettings:
    return TradingSettings(
        initial_capital_krw=100_000, fee_rate=0.005 if stress else 0.0025,
        slippage_bps=10.0 if stress else 5.0, allocation_fraction=0.50,
        minimum_order_krw=1, maximum_order_krw=100_000,
        maximum_daily_entries=4, cash_reserve_krw=0,
    )


def _window_metrics(
    candles: Sequence[Candle], signals: Sequence[Signal], start: int, end: int,
    settings: TradingSettings, fold_size: int | None,
) -> dict[str, Any]:
    source = signals[start - 1 : end]
    armed = source[0] is Signal.FLAT
    normalized: list[Signal] = []
    for signal in source:
        if not armed:
            normalized.append(Signal.FLAT)
            if signal is Signal.FLAT:
                armed = True
        else:
            normalized.append(signal)
    window = candles[start - 1 : end]
    tester = Backtester(settings, allow_short=False, expected_interval=SOURCE_DELTA)
    result = tester.run(window, normalized)
    folds: list[dict[str, Any]] = []
    if fold_size is not None:
        if (end - start) % fold_size:
            raise ValidationError("fold size does not exactly divide evaluation window")
        for offset in range(0, end - start, fold_size):
            values = _metrics(tester.slice_result(result, window, start=offset, end=offset + fold_size))
            folds.append({
                "fold": offset // fold_size + 1,
                "initial_equity_krw": values["initial_equity_krw"],
                "final_equity_krw": values["final_equity_krw"],
                "total_return": values["total_return"],
                "maximum_drawdown": values["maximum_drawdown"],
                "closed_trade_count": values["closed_trade_count"],
                "win_rate": values["win_rate"], "exposure": values["exposure"],
            })
    values = _metrics(result)
    values["folds"] = folds
    values["positive_fold_count"] = sum(fold["total_return"] > 0 for fold in folds)
    return values


def _metrics(result: BacktestResult) -> dict[str, Any]:
    trades = tuple(t for t in result.trades if not t.is_final_liquidation)
    wins = sum(t.net_pnl > 0 for t in trades)
    losses = sum(t.net_pnl < 0 for t in trades)
    profit = sum(t.net_pnl for t in trades if t.net_pnl > 0)
    loss = -sum(t.net_pnl for t in trades if t.net_pnl < 0)
    net = sum(t.net_pnl for t in trades)
    notional = sum(t.notional for t in trades)
    digest = sha256(b"bithumb-coin-trader:winrate-equity:v1\n")
    for point in result.equity_curve:
        digest.update(float(point).hex().encode("ascii"))
        digest.update(b"\n")
    return {
        "initial_equity_krw": result.initial_equity, "final_equity_krw": result.final_equity,
        "total_return": result.total_return, "maximum_drawdown": result.max_drawdown,
        "sharpe": result.sharpe, "exposure": result.exposure,
        "closed_trade_count": len(trades),
        "forced_final_liquidation_count": sum(t.is_final_liquidation for t in result.trades),
        "win_count": wins, "loss_count": losses,
        "win_rate": wins / len(trades) if trades else 0.0,
        "wilson_95_lower_bound": _wilson(wins, len(trades)),
        "gross_profit_krw": profit, "gross_loss_krw": loss,
        "profit_factor": profit / loss if loss > 0 else None,
        "profit_factor_is_infinite": bool(trades and profit > 0 and loss == 0),
        "net_expectancy_per_trade_krw": net / len(trades) if trades else 0.0,
        "net_pnl_over_notional": net / notional if notional > 0 else 0.0,
        "equity_evidence": {"point_count": len(result.equity_curve), "sha256": digest.hexdigest()},
    }


def _wilson(wins: int, total: int) -> float:
    if total <= 0:
        return 0.0
    z = 1.959963984540054
    p = wins / total
    denominator = 1.0 + z * z / total
    centre = p + z * z / (2.0 * total)
    margin = z * sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total))
    return max(0.0, (centre - margin) / denominator)


def _development_gate(base: Mapping[str, Any], stress: Mapping[str, Any], config: WinRateResearchConfig, is_cash: bool) -> dict[str, Any]:
    if is_cash:
        checks = {"cash_control_is_flat": base["total_return"] == 0.0 and base["closed_trade_count"] == 0}
    else:
        checks = {
            "win_rate_gte": base["win_rate"] >= config.minimum_development_win_rate,
            "closed_trades_gte": base["closed_trade_count"] >= config.minimum_development_closed_trades,
            "base_return_gt_cash": base["total_return"] > 0.0,
            "double_cost_return_gt_cash": stress["total_return"] > 0.0,
            "profit_factor_gt_one": _profit_factor_passes(base),
            "maximum_drawdown_lte": base["maximum_drawdown"] <= config.maximum_drawdown,
            "positive_folds_gte": base["positive_fold_count"] >= ceil(config.development_fold_count * config.minimum_positive_fold_fraction),
            "wilson_95_lower_bound_gte": base["wilson_95_lower_bound"] >= config.minimum_wilson_lower_bound,
        }
    return {"checks": checks, "passed": all(checks.values()) and not is_cash}


def _holdout_gate(base: Mapping[str, Any], stress: Mapping[str, Any], config: WinRateResearchConfig) -> dict[str, Any]:
    checks = {
        "win_rate_gte": base["win_rate"] >= config.minimum_holdout_win_rate,
        "closed_trades_gte": base["closed_trade_count"] >= config.minimum_holdout_closed_trades,
        "base_return_gt_cash": base["total_return"] > 0.0,
        "double_cost_return_gt_cash": stress["total_return"] > 0.0,
        "profit_factor_gt_one": _profit_factor_passes(base),
        "maximum_drawdown_lte": base["maximum_drawdown"] <= config.maximum_drawdown,
    }
    return {"checks": checks, "passed": all(checks.values())}


def _profit_factor_passes(values: Mapping[str, Any]) -> bool:
    factor = values["profit_factor"]
    return bool(values["profit_factor_is_infinite"] or (factor is not None and factor > 1.0))


def _candidate_manifest(factories: Mapping[str, CandidateFactory], families: Mapping[str, str]) -> dict[str, Any]:
    rows = []
    for name in sorted(factories):
        factory = factories[name]
        module_name = factory().__class__.__module__
        rows.append({
            "name": name, "family": families[name], "factory_module": module_name,
            "factory_qualname": getattr(factory, "__qualname__", type(factory).__qualname__),
            "source_sha256": _module_source_hash(module_name),
        })
    payload: dict[str, Any] = {"candidate_count": len(rows), "candidates": rows}
    payload["sha256"] = _canonical_hash(payload)
    return payload


def _protocol(config: WinRateResearchConfig) -> dict[str, Any]:
    boundaries = []
    for fold in range(config.development_fold_count):
        start = config.initial_train_count + fold * config.development_test_count
        boundaries.append({"train": [0, start], "test": [start, start + config.development_test_count]})
    positive = ceil(config.development_fold_count * config.minimum_positive_fold_fraction)
    return {
        "market_type": "Bithumb KRW spot LONG/FLAT",
        "signal_observed_at": "completed_30m_close", "execution_eligible_at": "next_30m_open",
        "allow_short": False, "allow_pyramiding": False,
        "evaluation_window_start": "flat_until_first_post_boundary_flat_to_long_transition",
        "gap_execution_policy": "force_flat_at_first_observed_post_gap_open",
        "execution_engine": {
            "module": "bithumb_coin_trader.backtest",
            "source_sha256": _module_source_hash("bithumb_coin_trader.backtest"),
            "validation_scope": (
                "shared fill engine; metrics, gates, and ranking independently "
                "recomputed"
            ),
        },
        "normalized_initial_capital_krw": 100_000, "allocation_fraction": 0.50,
        "maximum_daily_entries": 4,
        "development": {
            "count": config.development_count, "initial_train_count": config.initial_train_count,
            "test_count": config.development_test_count, "fold_count": config.development_fold_count,
            "boundaries": boundaries, "prequential_expanding": True,
        },
        "sealed_holdout": {
            "count": config.sealed_holdout_count, "maximum_candidates": config.maximum_holdout_candidates,
            "opened_only_after_all_development_gates": True,
            "requires_explicit_open": True,
            "requires_one_time_ledger": True,
        },
        "costs": {
            "base": {"fee_rate_per_fill": 0.0025, "slippage_bps_per_fill": 5.0},
            "double_cost_stress": {"fee_rate_per_fill": 0.005, "slippage_bps_per_fill": 10.0},
        },
        "development_gates": {
            "win_rate_gte": config.minimum_development_win_rate,
            "closed_trades_gte": config.minimum_development_closed_trades,
            "base_return_gt": 0.0, "double_cost_return_gt": 0.0, "profit_factor_gt": 1.0,
            "maximum_drawdown_lte": config.maximum_drawdown, "positive_folds_gte": positive,
            "wilson_95_lower_bound_gte": config.minimum_wilson_lower_bound,
        },
        "holdout_gates": {
            "win_rate_gte": config.minimum_holdout_win_rate,
            "closed_trades_gte": config.minimum_holdout_closed_trades,
            "base_return_gt": 0.0, "double_cost_return_gt": 0.0, "profit_factor_gt": 1.0,
            "maximum_drawdown_lte": config.maximum_drawdown,
        },
    }


def _module_source_hash(module_name: str) -> str | None:
    try:
        source_path = inspect.getsourcefile(importlib.import_module(module_name))
        return (
            sha256(Path(source_path).read_bytes()).hexdigest()
            if source_path
            else None
        )
    except (ImportError, OSError, TypeError):
        return None


def _external_research() -> list[dict[str, str]]:
    return [
        {"source": "Freqtrade strategy guide", "url": "https://www.freqtrade.io/en/stable/strategy-101/", "use": "public strategies require independent backtest and dry-run validation"},
        {"source": "Freqtrade lookahead analysis", "url": "https://docs.freqtrade.io/en/stable/lookahead-analysis/", "use": "future-information leakage audit principle"},
        {"source": "Freqtrade backtesting assumptions", "url": "https://docs.freqtrade.io/en/stable/backtesting/", "use": "historical fills do not replace prospective dry-run evidence"},
        {"source": "freqtrade-strategies eff78d3", "url": "https://github.com/freqtrade/freqtrade-strategies/tree/eff78d3ce3456b52c68a4e9a33cc055a56b801ff", "license": "GPL-3.0; concepts reviewed, code not copied"},
        {"source": "vectorbt 34b6d59", "url": "https://github.com/polakowo/vectorbt/tree/34b6d5935e3ea3eccd549e2592bc0f455b8045f5", "license": "Apache-2.0 with Commons Clause; concepts reviewed, dependency not added"},
        {"source": "Bailey et al. backtest overfitting", "url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659", "use": "multiple-testing and selection-bias warning"},
    ]


def _limitations() -> list[str]:
    return [
        "A historical win rate cannot guarantee future profit.",
        "The same historical development period was used to compare many frozen hypotheses, so multiple-testing risk remains.",
        "No point-in-time order-book, spread, news, or private account data was fabricated.",
        "Backtests cannot prove real fill quality; prospective paper evidence is still required.",
        "This artifact is incapable of mutating live trading configuration.",
    ]


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _live_entry_off_evidence() -> dict[str, Any]:
    home = Path.home()
    plist_path = home / "Library/LaunchAgents/com.bithumb.coin.trader.plist"
    wrapper_path = home / "Library/Application Support/BithumbCoinTrader/scripts/run_daemon_macos.sh"
    plist_off = False
    try:
        with plist_path.open("rb") as handle:
            environment = plistlib.load(handle).get("EnvironmentVariables", {})
        plist_off = str(environment.get("BITHUMB_NEW_ENTRIES", "")).lower() == "false"
    except (OSError, plistlib.InvalidFileException, AttributeError):
        pass
    wrapper_off = False
    try:
        assignments = re.findall(r"^\s*export\s+BITHUMB_NEW_ENTRIES\s*=\s*([^\s#]+)", wrapper_path.read_text(encoding="utf-8"), flags=re.MULTILINE)
        wrapper_off = bool(assignments) and assignments[-1].strip("'\"").lower() == "false"
    except (OSError, UnicodeDecodeError):
        pass
    launchctl_off = False
    try:
        completed = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/com.bithumb.coin.trader"],
            check=False, capture_output=True, text=True, timeout=5.0,
        )
        launchctl_off = bool(re.search(r"^\s*BITHUMB_NEW_ENTRIES\s*=>\s*false\s*$", completed.stdout, flags=re.MULTILINE | re.IGNORECASE))
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "installed_plist_off": plist_off, "installed_wrapper_off": wrapper_off,
        "launchctl_off": launchctl_off, "installed_plist_sha256": _file_sha256(plist_path),
        "installed_wrapper_sha256": _file_sha256(wrapper_path),
    }


def _file_sha256(path: Path) -> str | None:
    try:
        return sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--mirror", type=Path, default=DEFAULT_MIRROR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        result = validate(
            input_path=args.input,
            report_path=args.report,
            mirror_path=args.mirror,
            ledger_path=DEFAULT_LEDGER,
        )
    except (
        ValidationError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        IndexError,
    ) as exc:
        result = {"schema_version": 1, "status": "failed", "passed": False, "summary": "research validation could not run", "issues": [str(exc)], "automatic_promotion": "forbidden"}
    _write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
