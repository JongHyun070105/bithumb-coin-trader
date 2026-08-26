"""Deterministic, fail-closed research for the next KRW-BTC strategy.

This module deliberately has no exchange-order or credential dependency.  It
uses a complete daily dataset for the new candidate lane and retains the old
30-minute lane only as a post-fix development audit.  The final daily segment
is identified and hashed, but never evaluated here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from typing import Any, Mapping, Sequence

from .backtest import BacktestResult, Backtester
from .config import TradingSettings
from .daily_strategy_candidates import daily_candidate_factories
from .data import dataset_manifest
from .models import Candle, Signal
from .research_statistics import (
    as_serializable,
    cscv_probability_backtest_overfitting,
    white_reality_check,
)
from .risk import RiskLimits
from .strategy import (
    CompletedIntervalStrategy,
    DonchianBreakoutParameters,
    DonchianBreakoutStrategy,
)


DAILY_DELTA = timedelta(days=1)
MINUTE_DELTA = timedelta(minutes=30)
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class StrategyV2Config:
    daily_historical_count: int = 2_400
    daily_development_count: int = 2_220
    daily_initial_train_count: int = 1_020
    daily_test_count: int = 200
    daily_fold_count: int = 6
    daily_sealed_holdout_count: int = 180
    minute_historical_count: int = 100_000
    minute_development_count: int = 96_000
    minute_initial_train_count: int = 48_000
    prior_non_cash_trials: int = 52

    def __post_init__(self) -> None:
        values = asdict(self)
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values.values()):
            raise ValueError("strategy-v2 counts must be positive integers")
        if self.daily_development_count + self.daily_sealed_holdout_count != self.daily_historical_count:
            raise ValueError("daily development and holdout counts must cover history")
        if self.daily_initial_train_count + self.daily_test_count * self.daily_fold_count != self.daily_development_count:
            raise ValueError("daily folds must exactly cover development")
        if self.minute_development_count > self.minute_historical_count:
            raise ValueError("minute development exceeds history")


def research_settings(cost_multiplier: int = 1) -> TradingSettings:
    if cost_multiplier not in (1, 2, 3):
        raise ValueError("cost multiplier must be 1, 2, or 3")
    return TradingSettings(
        initial_capital_krw=100_000,
        fee_rate=0.0025 * cost_multiplier,
        slippage_bps=5.0 * cost_multiplier,
        allocation_fraction=0.30,
        minimum_order_krw=5_000,
        maximum_order_krw=60_000,
        maximum_daily_entries=4,
        cash_reserve_krw=33_000,
    )


def live_aligned_risk_limits() -> RiskLimits:
    return RiskLimits(
        minimum_order_krw=5_000,
        maximum_order_krw=60_000,
        maximum_daily_loss_fraction=0.05,
        maximum_drawdown_fraction=0.10,
        maximum_daily_entries=4,
        short_execution_enabled=False,
    )


def build_strategy_v2_report(
    daily_candles: Sequence[Candle],
    minute_candles: Sequence[Candle],
    *,
    generated_at: datetime | None = None,
    config: StrategyV2Config | None = None,
) -> dict[str, Any]:
    selected = config or StrategyV2Config()
    daily = _tail_sample(daily_candles, selected.daily_historical_count, DAILY_DELTA, exact=True)
    aligned_minute = tuple(
        candle
        for candle in minute_candles
        if candle.timestamp.minute % 30 == 0
        and candle.timestamp.second == 0
        and candle.timestamp.microsecond == 0
    )
    minute = _tail_sample(aligned_minute, selected.minute_historical_count, MINUTE_DELTA, exact=False)
    daily_development = daily[: selected.daily_development_count]
    daily_holdout = daily[selected.daily_development_count :]
    minute_development = minute[: selected.minute_development_count]

    factories = daily_candidate_factories()
    daily_rows: list[dict[str, Any]] = []
    return_rows: dict[str, tuple[float, ...]] = {}
    prefix_audits: dict[str, dict[str, Any]] = {}
    for name in sorted(factories):
        strategy = factories[name]()
        signals = tuple(Signal(value) for value in strategy.generate(daily_development))
        _validate_long_flat(signals, len(daily_development))
        prefix_audits[name] = _prefix_audit(strategy, daily_development, signals)
        costs: dict[str, Any] = {}
        base_result: BacktestResult | None = None
        for multiplier in (1, 2, 3):
            result, backtester, source = _evaluate_daily(
                daily_development,
                signals,
                selected,
                cost_multiplier=multiplier,
                risk_aligned=False,
            )
            if multiplier == 1:
                base_result = result
                metrics = _metrics(result)
                metrics["folds"] = _folds(backtester, result, source, selected)
                costs["base"] = metrics
            else:
                costs[f"cost_x{multiplier}"] = _metrics(result)
        assert base_result is not None
        risk_result, _, _ = _evaluate_daily(
            daily_development,
            signals,
            selected,
            cost_multiplier=1,
            risk_aligned=True,
        )
        costs["live_entry_gate_aligned"] = _metrics(risk_result)
        returns = _curve_returns(base_result.equity_curve)
        return_rows[name] = returns
        daily_rows.append(
            {
                "name": name,
                "required_history_bars": strategy.required_history_bars,
                **costs,
            }
        )

    active_returns = {
        name: values
        for name, values in return_rows.items()
        if name != "daily_buy_hold_benchmark"
    }
    trial_count = selected.prior_non_cash_trials + len(active_returns)
    statistics: dict[str, Any] = {
        "white_reality_check_vs_cash": as_serializable(
            white_reality_check(active_returns, iterations=2_000)
        ),
        "cscv_pbo": as_serializable(
            cscv_probability_backtest_overfitting(active_returns, blocks=8)
        ),
        "deflated_sharpe": {
            "status": "unavailable",
            "reason": "The immutable return/Sharpe ledger for the 52 prior non-cash trials is unavailable; reporting a probability from only current candidates would understate selection bias.",
        },
        "trial_count": trial_count,
        "prior_non_cash_trials": selected.prior_non_cash_trials,
    }
    ranked = sorted(
        (row for row in daily_rows if row["name"] != "daily_buy_hold_benchmark"),
        key=lambda row: (
            _risk_adjusted_score(row["base"]),
            row["cost_x3"]["total_return"],
            row["name"],
        ),
        reverse=True,
    )
    research_candidate = ranked[0]["name"] if ranked else "cash"
    minute_audit = _minute_development_audit(minute_development, selected)
    manifest = _source_manifest(factories)
    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    return {
        "schema_version": 2,
        "status": "research_only",
        "generated_at": generated.isoformat(),
        "mission": "Find a robust Bithumb KRW-BTC LONG/FLAT candidate without touching live execution or opening new holdouts.",
        "datasets": {
            "daily_full": _manifest(daily),
            "daily_development": _manifest(daily_development),
            "daily_sealed_holdout": {
                **_manifest(daily_holdout),
                "opened": False,
                "evaluated_candidates": [],
                "results": [],
            },
            "minute_full": _manifest(minute),
            "minute_development": _manifest(minute_development),
        },
        "protocol": {
            "daily_lane": {
                "purpose": "new frozen candidates",
                "closed_bar_signals": True,
                "fills": "next daily open",
                "decision_frequency": "completed KST Sunday only",
                "expanding_oos_folds": selected.daily_fold_count,
                "fold_days": selected.daily_test_count,
                "cost_multipliers": [1, 2, 3],
            },
            "minute_lane": {
                "purpose": "post-fix development audit only",
                "candidate": "profit_donchian_4h_70_30",
                "old_holdout_reuse": "forbidden",
            },
            "execution": asdict(research_settings()),
            "live_entry_gate_limits": asdict(live_aligned_risk_limits()),
            "selection_rule": "rank non-benchmark candidates by base Calmar-like score, then triple-cost return; never promote from reused development data",
        },
        "source_manifest": manifest,
        "daily_candidates": daily_rows,
        "daily_prefix_audits": prefix_audits,
        "multiple_testing": statistics,
        "minute_development_audit": minute_audit,
        "selection": {
            "research_candidate": research_candidate,
            "selected_for_live": "cash",
            "can_promote": False,
            "paper_or_live_strategy_changed": False,
            "reason": "All measured returns are development evidence; the final 180 daily candles remain sealed and the previous 30-minute holdout is invalidated/spent.",
        },
        "limitations": [
            "Daily candles do not model intraday spread, queue position, or partial fills.",
            "Fixed cost stresses are conservative scenarios, not exact future fees.",
            "This is one KRW-BTC market and does not establish cross-asset robustness.",
            "Multiple-testing diagnostics reduce but cannot eliminate selection bias.",
            "No live or paper order configuration is changed by this report.",
        ],
    }


def _evaluate_daily(
    candles: Sequence[Candle],
    signals: Sequence[Signal],
    config: StrategyV2Config,
    *,
    cost_multiplier: int,
    risk_aligned: bool,
) -> tuple[BacktestResult, Backtester, Sequence[Candle]]:
    start = config.daily_initial_train_count - 1
    source = candles[start : config.daily_development_count]
    source_signals = signals[start : config.daily_development_count]
    backtester = Backtester(
        research_settings(cost_multiplier),
        allow_short=False,
        expected_interval=DAILY_DELTA,
        risk_limits=live_aligned_risk_limits() if risk_aligned else None,
    )
    return backtester.run(source, source_signals), backtester, source


def _folds(
    backtester: Backtester,
    result: BacktestResult,
    source: Sequence[Candle],
    config: StrategyV2Config,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fold in range(config.daily_fold_count):
        start = fold * config.daily_test_count
        sliced = backtester.slice_result(
            result,
            source,
            start=start,
            end=start + config.daily_test_count,
        )
        rows.append({"fold": fold + 1, **_metrics(sliced)})
    return rows


def _minute_development_audit(
    candles: Sequence[Candle], config: StrategyV2Config
) -> dict[str, Any]:
    strategy = CompletedIntervalStrategy(
        DonchianBreakoutStrategy(DonchianBreakoutParameters(70, 30)),
        source_minutes=30,
        target_minutes=240,
    )
    strategy.name = "profit_donchian_4h_70_30"
    signals = tuple(Signal(value) for value in strategy.generate(candles))
    start = config.minute_initial_train_count - 1
    source = candles[start : config.minute_development_count]
    source_signals = _flat_start(signals[start : config.minute_development_count])
    costs: dict[str, Any] = {}
    for multiplier in (1, 2, 3):
        result = Backtester(
            research_settings(multiplier),
            allow_short=False,
            expected_interval=MINUTE_DELTA,
        ).run(source, source_signals)
        costs["base" if multiplier == 1 else f"cost_x{multiplier}"] = _metrics(result)
    gaps = [
        index
        for index in range(1, len(candles))
        if candles[index].timestamp - candles[index - 1].timestamp != MINUTE_DELTA
    ]
    warmup = 70 * 8
    premature = 0
    for gap in gaps:
        upper = min(len(signals), gap + warmup)
        premature += sum(signal is Signal.LONG for signal in signals[gap:upper])
    return {
        "candidate": strategy.name,
        "evidence_class": "reused_development_post_fix_audit",
        "old_holdout_reuse_forbidden": True,
        "source_gap_count": len(gaps),
        "required_post_gap_warmup_source_bars": warmup,
        "long_signal_bars_inside_warmup": premature,
        **costs,
    }


def _metrics(result: BacktestResult) -> dict[str, Any]:
    closed = tuple(trade for trade in result.trades if not trade.is_final_liquidation)
    wins = tuple(trade.net_pnl for trade in closed if trade.net_pnl > 0)
    losses = tuple(-trade.net_pnl for trade in closed if trade.net_pnl < 0)
    gross_profit = sum(wins)
    gross_loss = sum(losses)
    return {
        "initial_equity_krw": result.initial_equity,
        "final_equity_krw": result.final_equity,
        "total_return": result.total_return,
        "maximum_drawdown": result.max_drawdown,
        "sharpe": result.sharpe,
        "exposure": result.exposure,
        "closed_trade_count": len(closed),
        "forced_final_liquidation_count": sum(trade.is_final_liquidation for trade in result.trades),
        "gap_liquidation_count": sum(trade.is_gap_liquidation for trade in result.trades),
        "win_rate": len(wins) / len(closed) if closed else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "profit_factor_is_infinite": bool(gross_profit and not gross_loss),
        "maximum_single_win_contribution": max(wins, default=0.0) / gross_profit if gross_profit else 0.0,
        "total_fees_krw": result.total_fees,
        "gross_traded_notional_krw": result.gross_traded_notional,
        "turnover": result.turnover,
        "entry_rejection_count": len(result.entry_rejections),
    }


def _prefix_audit(strategy: Any, candles: Sequence[Candle], full: Sequence[Signal]) -> dict[str, Any]:
    checkpoints = sorted(set(range(max(2, strategy.required_history_bars), len(candles) + 1, 97)) | {len(candles)})
    mismatches = 0
    for end in checkpoints:
        prefix = tuple(Signal(value) for value in strategy.generate(candles[:end]))
        mismatches += sum(left is not right for left, right in zip(prefix, full[:end]))
    return {"checkpoint_count": len(checkpoints), "mismatch_count": mismatches, "passed": mismatches == 0}


def _tail_sample(
    candles: Sequence[Candle], count: int, interval: timedelta, *, exact: bool
) -> tuple[Candle, ...]:
    if len(candles) < count:
        raise ValueError(f"dataset requires at least {count} candles")
    sample = tuple(candles[-count:])
    if {candle.market for candle in sample} != {"KRW-BTC"}:
        raise ValueError("strategy-v2 research requires KRW-BTC")
    for index in range(1, len(sample)):
        delta = sample[index].timestamp - sample[index - 1].timestamp
        if delta <= timedelta(0):
            raise ValueError("candles must be strictly chronological")
        if exact and delta != interval:
            raise ValueError("daily research dataset must be gap-free")
    return sample


def _flat_start(signals: Sequence[Signal]) -> tuple[Signal, ...]:
    armed = signals[0] is Signal.FLAT
    result: list[Signal] = []
    for signal in signals:
        if not armed:
            result.append(Signal.FLAT)
            if signal is Signal.FLAT:
                armed = True
        else:
            result.append(signal)
    return tuple(result)


def _validate_long_flat(signals: Sequence[Signal], length: int) -> None:
    if len(signals) != length or any(signal not in (Signal.FLAT, Signal.LONG) for signal in signals):
        raise ValueError("candidate emitted invalid or misaligned signals")


def _curve_returns(curve: Sequence[float]) -> tuple[float, ...]:
    return tuple(curve[index] / curve[index - 1] - 1 for index in range(1, len(curve)))


def _risk_adjusted_score(metrics: Mapping[str, Any]) -> float:
    drawdown = float(metrics["maximum_drawdown"])
    return float(metrics["total_return"]) / max(drawdown, 1e-9)


def _manifest(candles: Sequence[Candle]) -> dict[str, Any]:
    identity = dataset_manifest(candles)
    return {
        "market": identity.market,
        "candle_count": identity.candle_count,
        "start_at": identity.start_at.isoformat() if identity.start_at else None,
        "end_at": identity.end_at.isoformat() if identity.end_at else None,
        "sha256": identity.sha256,
    }


def _source_manifest(factories: Mapping[str, Any]) -> dict[str, Any]:
    paths = (
        Path(__file__),
        REPOSITORY_ROOT / "src/bithumb_coin_trader/backtest.py",
        REPOSITORY_ROOT / "src/bithumb_coin_trader/daily_strategy_candidates.py",
        REPOSITORY_ROOT / "src/bithumb_coin_trader/research_statistics.py",
        REPOSITORY_ROOT / "src/bithumb_coin_trader/strategy.py",
        REPOSITORY_ROOT / "src/bithumb_coin_trader/config.py",
        REPOSITORY_ROOT / "src/bithumb_coin_trader/data.py",
        REPOSITORY_ROOT / "src/bithumb_coin_trader/models.py",
        REPOSITORY_ROOT / "src/bithumb_coin_trader/risk.py",
        REPOSITORY_ROOT / "scripts/run_strategy_v2_research.py",
        REPOSITORY_ROOT / "scripts/validate_strategy_v2_research.py",
    )
    files = [
        {"path": str(path.relative_to(REPOSITORY_ROOT)), "sha256": sha256(path.read_bytes()).hexdigest()}
        for path in paths
    ]
    payload = {
        "candidates": sorted(factories),
        "files": files,
        "research_settings": asdict(research_settings()),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {**payload, "sha256": sha256(encoded).hexdigest()}


def assert_finite_report(value: Any) -> None:
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("research report contains a non-finite number")
    if isinstance(value, Mapping):
        for item in value.values():
            assert_finite_report(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_finite_report(item)
