"""Validator-gated selective win-rate research for Bithumb spot.

This module is deliberately isolated from credentials and order execution.  It
compares frozen LONG/FLAT strategies on completed public 30-minute candles,
executes every close decision at the next candle open, and keeps a final
holdout sealed unless a strategy first clears every development gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import importlib
import inspect
import json
from math import ceil, isfinite, sqrt
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .backtest import BacktestResult, Backtester, Trade
from .config import TradingSettings
from .data import dataset_manifest
from .models import Candle, Signal
from .research import registered_candidate_factories


SOURCE_DELTA = timedelta(minutes=30)
CandidateFactory = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class WinRateResearchConfig:
    historical_count: int = 45_000
    development_count: int = 41_000
    initial_train_count: int = 17_000
    development_test_count: int = 4_000
    development_fold_count: int = 6
    sealed_holdout_count: int = 4_000
    maximum_holdout_candidates: int = 3
    minimum_development_closed_trades: int = 30
    minimum_development_win_rate: float = 0.70
    minimum_wilson_lower_bound: float = 0.50
    minimum_positive_fold_fraction: float = 0.60
    maximum_drawdown: float = 0.15
    minimum_holdout_closed_trades: int = 10
    minimum_holdout_win_rate: float = 0.60

    def __post_init__(self) -> None:
        integer_values = (
            self.historical_count,
            self.development_count,
            self.initial_train_count,
            self.development_test_count,
            self.development_fold_count,
            self.sealed_holdout_count,
            self.maximum_holdout_candidates,
            self.minimum_development_closed_trades,
            self.minimum_holdout_closed_trades,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in integer_values
        ):
            raise ValueError("research sizes and count gates must be positive integers")
        if self.development_count + self.sealed_holdout_count != self.historical_count:
            raise ValueError("development and holdout counts must cover the sample")
        if (
            self.initial_train_count
            + self.development_test_count * self.development_fold_count
            != self.development_count
        ):
            raise ValueError("development folds must exactly cover the development sample")
        fractions = (
            self.minimum_development_win_rate,
            self.minimum_wilson_lower_bound,
            self.minimum_positive_fold_fraction,
            self.maximum_drawdown,
            self.minimum_holdout_win_rate,
        )
        if any(not isfinite(value) or not 0 < value < 1 for value in fractions):
            raise ValueError("fractional gates must be finite and between zero and one")

    @property
    def minimum_positive_folds(self) -> int:
        return ceil(self.development_fold_count * self.minimum_positive_fold_fraction)

    def development_boundaries(self) -> tuple[tuple[int, int], ...]:
        return tuple(
            (
                self.initial_train_count + fold * self.development_test_count,
                self.initial_train_count + (fold + 1) * self.development_test_count,
            )
            for fold in range(self.development_fold_count)
        )

    def expanding_boundaries(self) -> tuple[tuple[int, int, int, int], ...]:
        return tuple((0, start, start, end) for start, end in self.development_boundaries())


class CashControl:
    name = "cash"

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        return [Signal.FLAT] * len(candles)


def normalized_settings(*, stress: bool = False) -> TradingSettings:
    """Use scale-neutral research capital while preserving 50% allocation."""

    return TradingSettings(
        initial_capital_krw=100_000,
        fee_rate=0.005 if stress else 0.0025,
        slippage_bps=10.0 if stress else 5.0,
        allocation_fraction=0.50,
        minimum_order_krw=1,
        maximum_order_krw=100_000,
        maximum_daily_entries=4,
        cash_reserve_krw=0,
    )


def candidate_registry() -> tuple[dict[str, CandidateFactory], dict[str, str]]:
    """Return frozen factories and their strategy-family labels."""

    factories: dict[str, CandidateFactory] = {"cash": CashControl}
    families: dict[str, str] = {"cash": "cash_control"}

    groups: tuple[tuple[str, str, str], ...] = (
        ("legacy", "bithumb_coin_trader.research", "registered_candidate_factories"),
        ("trend", "bithumb_coin_trader.winrate_trend_candidates", "candidate_factories"),
        (
            "mean_reversion",
            "bithumb_coin_trader.winrate_mean_reversion_candidates",
            "candidate_factories",
        ),
        (
            "volatility",
            "bithumb_coin_trader.winrate_volatility_candidates",
            "candidate_factories",
        ),
        ("meta", "bithumb_coin_trader.winrate_meta_candidates", "candidate_factories"),
        (
            "session",
            "bithumb_coin_trader.winrate_session_candidates",
            "candidate_factories",
        ),
    )
    for family, module_name, function_name in groups:
        if family == "legacy":
            group = registered_candidate_factories()
        else:
            group = getattr(importlib.import_module(module_name), function_name)()
        for name, factory in group.items():
            if name in factories:
                raise ValueError(f"duplicate research candidate name: {name}")
            if not name or not callable(factory):
                raise ValueError("candidate names must be non-empty and factories callable")
            factories[name] = factory
            families[name] = family
    return factories, families


def candidate_manifest(
    factories: Mapping[str, CandidateFactory], families: Mapping[str, str]
) -> dict[str, Any]:
    rows = []
    for name in sorted(factories):
        factory = factories[name]
        strategy = factory()
        module_name = strategy.__class__.__module__
        rows.append(
            {
                "name": name,
                "family": families[name],
                "factory_module": module_name,
                "factory_qualname": getattr(factory, "__qualname__", type(factory).__qualname__),
                "source_sha256": _module_source_hash(module_name),
            }
        )
    payload = {"candidate_count": len(rows), "candidates": rows}
    payload["sha256"] = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def build_report(
    candles: Sequence[Candle],
    *,
    generated_at: datetime | None = None,
    config: WinRateResearchConfig | None = None,
    factories: Mapping[str, CandidateFactory] | None = None,
    families: Mapping[str, str] | None = None,
    evaluate_holdout: bool = False,
) -> dict[str, Any]:
    selected = config or WinRateResearchConfig()
    sample = _sample(candles, selected)
    if factories is None or families is None:
        default_factories, default_families = candidate_registry()
        factories = default_factories if factories is None else factories
        families = default_families if families is None else families
    if set(factories) != set(families):
        raise ValueError("every candidate must have exactly one family label")

    base_settings = normalized_settings()
    stress_settings = normalized_settings(stress=True)
    development_sample = sample[: selected.development_count]
    development_rows: list[dict[str, Any]] = []
    signal_cache: dict[str, tuple[Signal, ...]] = {}

    for name in sorted(factories):
        strategy = factories[name]()
        if getattr(strategy, "name", name) != name:
            raise ValueError(f"candidate factory name mismatch: {name}")
        signals = tuple(
            Signal(value) for value in strategy.generate(development_sample)
        )
        _validate_signals(signals, development_sample)
        signal_cache[name] = signals
        base = _evaluate_window(
            development_sample,
            signals,
            start=selected.initial_train_count,
            end=selected.development_count,
            settings=base_settings,
            fold_size=selected.development_test_count,
        )
        stress = _evaluate_window(
            development_sample,
            signals,
            start=selected.initial_train_count,
            end=selected.development_count,
            settings=stress_settings,
            fold_size=selected.development_test_count,
        )
        gate = evaluate_development_gate(base, stress, selected, is_cash=name == "cash")
        development_rows.append(
            {
                "name": name,
                "family": families[name],
                "base": base,
                "double_cost_stress": stress,
                "gate_evaluation": gate,
            }
        )

    ranked = sorted(
        (
            row
            for row in development_rows
            if row["name"] != "cash" and row["gate_evaluation"]["passed"]
        ),
        key=lambda row: (
            row["double_cost_stress"]["total_return"],
            row["base"]["total_return"],
            row["base"]["wilson_95_lower_bound"],
            row["name"],
        ),
        reverse=True,
    )
    opened_names = (
        [row["name"] for row in ranked[: selected.maximum_holdout_candidates]]
        if evaluate_holdout
        else []
    )
    holdout_rows = []
    for name in opened_names:
        strategy = factories[name]()
        signals = tuple(Signal(value) for value in strategy.generate(sample))
        _validate_signals(signals, sample)
        if signals[: selected.development_count] != signal_cache[name]:
            raise ValueError(
                f"candidate signals are not prefix-stable before holdout: {name}"
            )
        _validate_holdout_prefix_stability(
            factories[name],
            sample,
            signals,
            development_count=selected.development_count,
        )
        base = _evaluate_window(
            sample,
            signals,
            start=selected.development_count,
            end=selected.historical_count,
            settings=base_settings,
            fold_size=None,
        )
        stress = _evaluate_window(
            sample,
            signals,
            start=selected.development_count,
            end=selected.historical_count,
            settings=stress_settings,
            fold_size=None,
        )
        holdout_rows.append(
            {
                "name": name,
                "base": base,
                "double_cost_stress": stress,
                "gate_evaluation": evaluate_holdout_gate(base, stress, selected),
            }
        )

    survivors = [row for row in holdout_rows if row["gate_evaluation"]["passed"]]
    survivors.sort(
        key=lambda row: (
            row["double_cost_stress"]["total_return"],
            row["base"]["total_return"],
            row["name"],
        ),
        reverse=True,
    )
    research_candidate = survivors[0]["name"] if survivors else "cash"
    identity = dataset_manifest(sample)
    manifest = candidate_manifest(factories, families)
    near_misses = sorted(
        (row for row in development_rows if row["name"] != "cash"),
        key=lambda row: (
            sum(row["gate_evaluation"]["checks"].values()),
            row["double_cost_stress"]["total_return"],
            row["base"]["total_return"],
        ),
        reverse=True,
    )[:10]

    return {
        "schema_version": 1,
        "generated_at": (generated_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "status": "RESEARCH_ONLY",
        "dataset": {
            "market": identity.market,
            "candle_count": identity.candle_count,
            "start_at": identity.start_at.isoformat() if identity.start_at else None,
            "end_at": identity.end_at.isoformat() if identity.end_at else None,
            "sha256": identity.sha256,
            "gap_count": _gap_count(sample),
            "source": "Bithumb public completed 30-minute OHLCV",
        },
        "candidate_manifest": manifest,
        "protocol": protocol_manifest(selected),
        "development": {
            "candidate_count": len(development_rows),
            "candidates": development_rows,
            "passed_candidates": [row["name"] for row in ranked],
            "near_misses": [
                {
                    "name": row["name"],
                    "passed_check_count": sum(row["gate_evaluation"]["checks"].values()),
                    "failed_checks": [
                        key
                        for key, passed in row["gate_evaluation"]["checks"].items()
                        if not passed
                    ],
                    "base_return": row["base"]["total_return"],
                    "base_win_rate": row["base"]["win_rate"],
                    "closed_trades": row["base"]["closed_trade_count"],
                }
                for row in near_misses
            ],
        },
        "sealed_holdout": {
            "count": selected.sealed_holdout_count,
            "start_at": sample[selected.development_count].timestamp.isoformat(),
            "end_at": sample[-1].timestamp.isoformat(),
            "opened": bool(opened_names),
            "evaluated_candidates": opened_names,
            "results": holdout_rows,
        },
        "selection": {
            "research_candidate": research_candidate,
            "historical_target_met": research_candidate != "cash",
            "fallback_to_cash": research_candidate == "cash",
            "automatic_promotion": "forbidden",
            "can_promote": False,
            "paper_or_live_strategy_changed": False,
            "requires_prospective_forward_evidence": True,
            "holdout_evaluation_required": bool(ranked) and not evaluate_holdout,
        },
        "external_research": external_research_manifest(),
        "limitations": [
            "A historical win rate cannot guarantee future profit.",
            "The same historical development period was used to compare many frozen "
            "hypotheses, so multiple-testing risk remains.",
            "No point-in-time order-book, spread, news, or private account data was "
            "fabricated.",
            "Backtests cannot prove real fill quality; prospective paper evidence is "
            "still required.",
            "This artifact is incapable of mutating live trading configuration.",
        ],
    }


def protocol_manifest(config: WinRateResearchConfig) -> dict[str, Any]:
    return {
        "market_type": "Bithumb KRW spot LONG/FLAT",
        "signal_observed_at": "completed_30m_close",
        "execution_eligible_at": "next_30m_open",
        "allow_short": False,
        "allow_pyramiding": False,
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
        "normalized_initial_capital_krw": 100_000,
        "allocation_fraction": 0.50,
        "maximum_daily_entries": 4,
        "development": {
            "count": config.development_count,
            "initial_train_count": config.initial_train_count,
            "test_count": config.development_test_count,
            "fold_count": config.development_fold_count,
            "boundaries": [
                {"train": [train_start, train_end], "test": [test_start, test_end]}
                for train_start, train_end, test_start, test_end in config.expanding_boundaries()
            ],
            "prequential_expanding": True,
        },
        "sealed_holdout": {
            "count": config.sealed_holdout_count,
            "maximum_candidates": config.maximum_holdout_candidates,
            "opened_only_after_all_development_gates": True,
            "requires_explicit_open": True,
            "requires_one_time_ledger": True,
        },
        "costs": {
            "base": {"fee_rate_per_fill": 0.0025, "slippage_bps_per_fill": 5.0},
            "double_cost_stress": {
                "fee_rate_per_fill": 0.005,
                "slippage_bps_per_fill": 10.0,
            },
        },
        "development_gates": {
            "win_rate_gte": config.minimum_development_win_rate,
            "closed_trades_gte": config.minimum_development_closed_trades,
            "base_return_gt": 0.0,
            "double_cost_return_gt": 0.0,
            "profit_factor_gt": 1.0,
            "maximum_drawdown_lte": config.maximum_drawdown,
            "positive_folds_gte": config.minimum_positive_folds,
            "wilson_95_lower_bound_gte": config.minimum_wilson_lower_bound,
        },
        "holdout_gates": {
            "win_rate_gte": config.minimum_holdout_win_rate,
            "closed_trades_gte": config.minimum_holdout_closed_trades,
            "base_return_gt": 0.0,
            "double_cost_return_gt": 0.0,
            "profit_factor_gt": 1.0,
            "maximum_drawdown_lte": config.maximum_drawdown,
        },
    }


def evaluate_development_gate(
    base: Mapping[str, Any],
    stress: Mapping[str, Any],
    config: WinRateResearchConfig,
    *,
    is_cash: bool = False,
) -> dict[str, Any]:
    if is_cash:
        checks = {
            "cash_control_is_flat": (
                base["total_return"] == 0.0
                and base["closed_trade_count"] == 0
            )
        }
    else:
        checks = {
            "win_rate_gte": base["win_rate"] >= config.minimum_development_win_rate,
            "closed_trades_gte": (
                base["closed_trade_count"]
                >= config.minimum_development_closed_trades
            ),
            "base_return_gt_cash": base["total_return"] > 0.0,
            "double_cost_return_gt_cash": stress["total_return"] > 0.0,
            "profit_factor_gt_one": _profit_factor_passes(base),
            "maximum_drawdown_lte": (
                base["maximum_drawdown"] <= config.maximum_drawdown
            ),
            "positive_folds_gte": (
                base["positive_fold_count"] >= config.minimum_positive_folds
            ),
            "wilson_95_lower_bound_gte": (
                base["wilson_95_lower_bound"]
                >= config.minimum_wilson_lower_bound
            ),
        }
    return {"checks": checks, "passed": all(checks.values()) and not is_cash}


def evaluate_holdout_gate(
    base: Mapping[str, Any], stress: Mapping[str, Any], config: WinRateResearchConfig
) -> dict[str, Any]:
    checks = {
        "win_rate_gte": base["win_rate"] >= config.minimum_holdout_win_rate,
        "closed_trades_gte": (
            base["closed_trade_count"] >= config.minimum_holdout_closed_trades
        ),
        "base_return_gt_cash": base["total_return"] > 0.0,
        "double_cost_return_gt_cash": stress["total_return"] > 0.0,
        "profit_factor_gt_one": _profit_factor_passes(base),
        "maximum_drawdown_lte": base["maximum_drawdown"] <= config.maximum_drawdown,
    }
    return {"checks": checks, "passed": all(checks.values())}


def wilson_lower_bound(wins: int, total: int, *, z: float = 1.959963984540054) -> float:
    if total <= 0:
        return 0.0
    proportion = wins / total
    denominator = 1.0 + z * z / total
    centre = proportion + z * z / (2.0 * total)
    margin = z * sqrt(
        proportion * (1.0 - proportion) / total
        + z * z / (4.0 * total * total)
    )
    return max(0.0, (centre - margin) / denominator)


def external_research_manifest() -> list[dict[str, str]]:
    return [
        {
            "source": "Freqtrade strategy guide",
            "url": "https://www.freqtrade.io/en/stable/strategy-101/",
            "use": "public strategies require independent backtest and dry-run validation",
        },
        {
            "source": "Freqtrade lookahead analysis",
            "url": "https://docs.freqtrade.io/en/stable/lookahead-analysis/",
            "use": "future-information leakage audit principle",
        },
        {
            "source": "Freqtrade backtesting assumptions",
            "url": "https://docs.freqtrade.io/en/stable/backtesting/",
            "use": "historical fills do not replace prospective dry-run evidence",
        },
        {
            "source": "freqtrade-strategies eff78d3",
            "url": (
                "https://github.com/freqtrade/freqtrade-strategies/tree/"
                "eff78d3ce3456b52c68a4e9a33cc055a56b801ff"
            ),
            "license": "GPL-3.0; concepts reviewed, code not copied",
        },
        {
            "source": "vectorbt 34b6d59",
            "url": "https://github.com/polakowo/vectorbt/tree/34b6d5935e3ea3eccd549e2592bc0f455b8045f5",
            "license": "Apache-2.0 with Commons Clause; concepts reviewed, dependency not added",
        },
        {
            "source": "Bailey et al. backtest overfitting",
            "url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659",
            "use": "multiple-testing and selection-bias warning",
        },
    ]


def _evaluate_window(
    candles: Sequence[Candle],
    signals: Sequence[Signal],
    *,
    start: int,
    end: int,
    settings: TradingSettings,
    fold_size: int | None,
) -> dict[str, Any]:
    if not 1 <= start < end <= len(candles):
        raise ValueError("evaluation window is outside the sample")
    window_candles = candles[start - 1 : end]
    window_signals = _flat_start_signals(signals, start=start, end=end)
    backtester = Backtester(
        settings,
        allow_short=False,
        expected_interval=SOURCE_DELTA,
    )
    result = backtester.run(window_candles, window_signals)
    folds = []
    if fold_size is not None:
        if (end - start) % fold_size:
            raise ValueError("fold size must exactly divide the evaluation window")
        for fold_start in range(0, end - start, fold_size):
            sliced = backtester.slice_result(
                result,
                window_candles,
                start=fold_start,
                end=fold_start + fold_size,
            )
            folds.append(_fold_metrics(sliced, fold_start // fold_size + 1))
    metrics = _result_metrics(result)
    metrics["folds"] = folds
    metrics["positive_fold_count"] = sum(fold["total_return"] > 0 for fold in folds)
    return metrics


def _result_metrics(result: BacktestResult) -> dict[str, Any]:
    closed = tuple(trade for trade in result.trades if not trade.is_final_liquidation)
    wins = sum(trade.net_pnl > 0 for trade in closed)
    losses = sum(trade.net_pnl < 0 for trade in closed)
    gross_profit = sum(trade.net_pnl for trade in closed if trade.net_pnl > 0)
    gross_loss = -sum(trade.net_pnl for trade in closed if trade.net_pnl < 0)
    notional = sum(trade.notional for trade in closed)
    digest = sha256(b"bithumb-coin-trader:winrate-equity:v1\n")
    for point in result.equity_curve:
        digest.update(float(point).hex().encode("ascii"))
        digest.update(b"\n")
    return {
        "initial_equity_krw": result.initial_equity,
        "final_equity_krw": result.final_equity,
        "total_return": result.total_return,
        "maximum_drawdown": result.max_drawdown,
        "sharpe": result.sharpe,
        "exposure": result.exposure,
        "closed_trade_count": len(closed),
        "forced_final_liquidation_count": sum(
            trade.is_final_liquidation for trade in result.trades
        ),
        "win_count": wins,
        "loss_count": losses,
        "win_rate": wins / len(closed) if closed else 0.0,
        "wilson_95_lower_bound": wilson_lower_bound(wins, len(closed)),
        "gross_profit_krw": gross_profit,
        "gross_loss_krw": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "profit_factor_is_infinite": bool(closed and gross_profit > 0 and gross_loss == 0),
        "net_expectancy_per_trade_krw": (
            sum(trade.net_pnl for trade in closed) / len(closed)
            if closed
            else 0.0
        ),
        "net_pnl_over_notional": (
            sum(trade.net_pnl for trade in closed) / notional
            if notional > 0
            else 0.0
        ),
        "equity_evidence": {
            "point_count": len(result.equity_curve),
            "sha256": digest.hexdigest(),
        },
    }


def _fold_metrics(result: BacktestResult, fold: int) -> dict[str, Any]:
    metrics = _result_metrics(result)
    return {
        "fold": fold,
        "initial_equity_krw": metrics["initial_equity_krw"],
        "final_equity_krw": metrics["final_equity_krw"],
        "total_return": metrics["total_return"],
        "maximum_drawdown": metrics["maximum_drawdown"],
        "closed_trade_count": metrics["closed_trade_count"],
        "win_rate": metrics["win_rate"],
        "exposure": metrics["exposure"],
    }


def _profit_factor_passes(metrics: Mapping[str, Any]) -> bool:
    value = metrics["profit_factor"]
    return bool(metrics["profit_factor_is_infinite"] or (value is not None and value > 1.0))


def _flat_start_signals(
    signals: Sequence[Signal], *, start: int, end: int
) -> tuple[Signal, ...]:
    """Do not synthesize an OOS entry from a position opened during warm-up."""

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
    return tuple(normalized)


def _sample(candles: Sequence[Candle], config: WinRateResearchConfig) -> tuple[Candle, ...]:
    if len(candles) != config.historical_count:
        raise ValueError(f"research requires exactly {config.historical_count} completed candles")
    sample = tuple(candles)
    _validate_candles(sample)
    if {candle.market for candle in sample} != {"KRW-BTC"}:
        raise ValueError("research sample must be exactly KRW-BTC")
    return sample


def _validate_candles(candles: Sequence[Candle]) -> None:
    if not candles:
        raise ValueError("research requires candles")
    if any(
        candle.timestamp.second
        or candle.timestamp.microsecond
        or candle.timestamp.minute % 30
        for candle in candles
    ):
        raise ValueError("research requires aligned 30-minute candles")
    if any(
        candles[index].timestamp <= candles[index - 1].timestamp
        for index in range(1, len(candles))
    ):
        raise ValueError("candles must be strictly chronological")


def _validate_signals(signals: Sequence[Signal], candles: Sequence[Candle]) -> None:
    if len(signals) != len(candles):
        raise ValueError("candidate signal count differs from candle count")
    if any(signal not in {Signal.FLAT, Signal.LONG} for signal in signals):
        raise ValueError("win-rate research is LONG/FLAT only")


def _validate_holdout_prefix_stability(
    factory: CandidateFactory,
    candles: Sequence[Candle],
    full_signals: Sequence[Signal],
    *,
    development_count: int,
) -> None:
    """Reject finalists whose earlier signals change when later bars are absent."""

    holdout_count = len(candles) - development_count
    if holdout_count <= 1:
        return
    stride = max(1, holdout_count // 4)
    checkpoints = range(
        development_count + stride,
        len(candles),
        stride,
    )
    for end in checkpoints:
        prefix = candles[:end]
        prefix_signals = tuple(Signal(value) for value in factory().generate(prefix))
        _validate_signals(prefix_signals, prefix)
        if prefix_signals != tuple(full_signals[:end]):
            raise ValueError(
                "candidate signals are not prefix-stable inside the sealed holdout"
            )


def _gap_count(candles: Sequence[Candle]) -> int:
    return sum(
        candles[index].timestamp - candles[index - 1].timestamp != SOURCE_DELTA
        for index in range(1, len(candles))
    )


def _module_source_hash(module_name: str) -> str | None:
    try:
        module = importlib.import_module(module_name)
        path = inspect.getsourcefile(module)
        if path is None:
            return None
        return sha256(Path(path).read_bytes()).hexdigest()
    except (ImportError, OSError, TypeError):
        return None
