"""Research-only replay of the live BTC entry proxy and exit policies.

This module is deliberately isolated from credentials, account state, order
books, and order execution.  Decisions use completed 30-minute candles and are
filled at the following bar open.  It can produce historical evidence, but it
cannot promote or mutate paper/live trading policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from hashlib import sha256
import json
from math import isfinite, sqrt
from statistics import fmean, median, pstdev
from typing import Any, Mapping, Sequence

from .config import TradingSettings
from .data import dataset_manifest
from .indicators import (
    bollinger_bands,
    institutional_displacement_signals,
    macd,
    simple_moving_average,
    wilder_rsi,
)
from .models import Candle
from .wave5 import Wave5Config


SOURCE_DELTA = timedelta(minutes=30)
KST = timezone(timedelta(hours=9))
LIVE_ENTRY_LOOKBACK = 100
ENTRY_CONFIDENCE_THRESHOLD = 75.0
MAX_DAILY_ENTRIES = 4


@dataclass(frozen=True, slots=True)
class ExitPolicy:
    name: str
    stop_loss: float = 0.018
    take_profit: float = 0.038
    partial_take_profit: float | None = None
    partial_fraction: float = 0.50
    breakeven_activate: float | None = None
    breakeven_buffer: float = 0.003
    trailing_activate: float | None = None
    trailing_distance: float | None = None
    timecut: timedelta | None = None
    timecut_band: float | None = None

    def __post_init__(self) -> None:
        fractions = (
            self.stop_loss,
            self.take_profit,
            self.partial_fraction,
            self.breakeven_buffer,
        )
        optional = (
            self.partial_take_profit,
            self.breakeven_activate,
            self.trailing_activate,
            self.trailing_distance,
            self.timecut_band,
        )
        if not self.name or any(not isfinite(value) or value <= 0 for value in fractions):
            raise ValueError("exit policy values must be finite and positive")
        if any(value is not None and (not isfinite(value) or value <= 0) for value in optional):
            raise ValueError("optional exit policy values must be finite and positive")
        if not 0 < self.partial_fraction < 1:
            raise ValueError("partial fraction must be between zero and one")
        if self.timecut is not None and self.timecut <= timedelta(0):
            raise ValueError("timecut must be positive")


FIXED_EXIT_POLICY = ExitPolicy(name="fixed_full_exit_1p8_3p8")
DEFENSIVE_BASELINE_POLICY = ExitPolicy(
    name="defensive_baseline",
    breakeven_activate=0.010,
    trailing_activate=0.022,
    trailing_distance=0.018,
)
ENHANCED_EXIT_POLICY = ExitPolicy(
    name="enhanced_exit_v1",
    partial_take_profit=0.020,
    breakeven_activate=0.010,
    trailing_activate=0.022,
    trailing_distance=0.018,
    timecut=timedelta(hours=4),
    timecut_band=0.006,
)


@dataclass(frozen=True, slots=True)
class PolicyTrade:
    entry_index: int
    exit_index: int
    entry_price: float
    exit_price: float
    notional: float
    net_pnl: float
    exit_reason: str
    partial_exit_count: int
    is_final_liquidation: bool = False


@dataclass(frozen=True, slots=True)
class PolicyBacktestResult:
    initial_equity: float
    final_equity: float
    total_return: float
    max_drawdown: float
    sharpe: float
    trade_count: int
    closed_trade_count: int
    win_rate: float
    exposure: float
    partial_exit_count: int
    partial_rejection_count: int
    timecut_exit_count: int
    trades: tuple[PolicyTrade, ...]
    equity_curve: tuple[float, ...]
    position_curve: tuple[bool, ...]


@dataclass(slots=True)
class _Position:
    entry_index: int
    entry_time: Any
    entry_price: float
    quantity: float
    initial_quantity: float
    notional: float
    entry_fee: float
    highest_close: float
    breakeven_locked: bool = False
    partial_taken: bool = False
    gross_exit_proceeds: float = 0.0
    exit_fees: float = 0.0
    partial_exit_count: int = 0


def live_entry_eligibility(candles: Sequence[Candle]) -> tuple[bool, ...]:
    """Replay the deterministic candle-only portion of the live entry gate.

    Each decision recomputes the same indicators on the trailing 100 completed
    candles used by ``scripts/scan_and_trade.py``.  The historical proxy fixes
    order-book adjustment at its neutral value, so the live 75 confidence gate
    becomes an unadjusted candle-score threshold of 75.
    """

    _validate_candles(candles)
    eligible = [False] * len(candles)
    for index in range(LIVE_ENTRY_LOOKBACK - 1, len(candles)):
        window = candles[index - LIVE_ENTRY_LOOKBACK + 1 : index + 1]
        if any(
            window[offset].timestamp - window[offset - 1].timestamp != SOURCE_DELTA
            for offset in range(1, len(window))
        ):
            continue
        opens = [candle.open for candle in window]
        highs = [candle.high for candle in window]
        lows = [candle.low for candle in window]
        closes = [candle.close for candle in window]
        volumes = [candle.volume for candle in window]
        fast = simple_moving_average(closes, 20)[-1] or closes[-1]
        slow = simple_moving_average(closes, 50)[-1] or closes[-1]
        rsi = wilder_rsi(closes, 14)[-1] or 50.0
        histogram = macd(closes, 12, 26, 9)[2][-1] or 0.0
        _, upper, lower = bollinger_bands(closes, 20, 2.0)
        bull, bear, _ = institutional_displacement_signals(
            opens,
            highs,
            lows,
            closes,
            volumes,
            vol_period=20,
            vol_multiplier=2.0,
            min_body_pct=45.0,
        )

        taro = 0.0
        if fast > slow and closes[-1] > slow:
            taro += 45.0
        if 40.0 <= rsi <= 70.0:
            taro += 30.0
        if histogram > 0:
            taro += 25.0
        diana = 100.0 if bull[-1] else (0.0 if bear[-1] else 50.0)
        ret_20 = (closes[-1] - closes[-20]) / closes[-20]
        nova = 50.0 + min(max(ret_20 * 500.0, -40.0), 40.0)
        vibe = 50.0
        if lower[-1] is not None and upper[-1] is not None:
            width = upper[-1] - lower[-1]
            if width > 0:
                vibe = min(max((closes[-1] - lower[-1]) / width * 100.0, 0.0), 100.0)
        confidence = taro * 0.40 + diana * 0.30 + nova * 0.15 + vibe * 0.15
        if bull[-1] and fast > slow:
            confidence += 10.0
        safe = rsi < 72.0 and not bear[-1] and closes[-1] > slow * 0.98
        eligible[index] = safe and confidence >= ENTRY_CONFIDENCE_THRESHOLD
    return tuple(eligible)


def run_policy_backtest(
    candles: Sequence[Candle],
    entry_eligible: Sequence[bool],
    *,
    settings: TradingSettings,
    policy: ExitPolicy,
) -> PolicyBacktestResult:
    """Execute closed-bar decisions at the next bar open.

    Exit thresholds are intentionally evaluated on completed closes, not candle
    highs/lows.  This avoids inventing an intrabar path from 30-minute OHLC and
    imposes one-bar execution latency versus the live three-second watcher.
    """

    _validate_candles(candles)
    if len(candles) != len(entry_eligible):
        raise ValueError("candles and entry eligibility must have the same length")
    if len(candles) < 2:
        raise ValueError("at least two candles are required")

    initial = float(settings.initial_capital_krw)
    cash = initial
    position: _Position | None = None
    pending: tuple[str, str] | None = None
    daily_entries: dict[str, int] = {}
    trades: list[PolicyTrade] = []
    partial_rejection_count = 0
    curve: list[float] = []
    positions: list[bool] = []
    slip = settings.slippage_bps / 10_000.0

    for index, candle in enumerate(candles):
        if pending is not None:
            action, reason = pending
            pending = None
            if action == "entry" and position is None:
                trading_date = candle.timestamp.astimezone(KST).date().isoformat()
                entries_today = daily_entries.get(trading_date, 0)
                notional = _dynamic_entry_notional(cash, settings.minimum_order_krw)
                if (
                    entries_today < settings.maximum_daily_entries
                    and notional >= settings.minimum_order_krw
                ):
                    fill_price = candle.open * (1.0 + slip)
                    entry_fee = notional * settings.fee_rate
                    if notional + entry_fee <= cash:
                        quantity = notional / fill_price
                        cash -= notional + entry_fee
                        position = _Position(
                            entry_index=index,
                            entry_time=candle.timestamp,
                            entry_price=fill_price,
                            quantity=quantity,
                            initial_quantity=quantity,
                            notional=notional,
                            entry_fee=entry_fee,
                            highest_close=fill_price,
                        )
                        daily_entries[trading_date] = entries_today + 1
            elif position is not None and action in {"partial", "full"}:
                ratio = policy.partial_fraction if action == "partial" else 1.0
                quantity = position.quantity * ratio
                fill_price = candle.open * (1.0 - slip)
                gross = quantity * fill_price
                remaining_gross = (position.quantity - quantity) * fill_price
                if action == "partial" and (
                    gross < settings.minimum_order_krw
                    or remaining_gross < settings.minimum_order_krw
                ):
                    # A gap can invalidate a partial order that was tradeable
                    # at the prior close. Preserve the full position and let
                    # the next completed close make a fresh decision.
                    partial_rejection_count += 1
                else:
                    fee = gross * settings.fee_rate
                    cash += gross - fee
                    position.quantity -= quantity
                    position.gross_exit_proceeds += gross
                    position.exit_fees += fee
                    if action == "partial":
                        position.partial_taken = True
                        position.partial_exit_count += 1
                    else:
                        trades.append(_finish_trade(position, index, fill_price, reason))
                        position = None

        if position is not None:
            position.highest_close = max(position.highest_close, candle.close)
            if (
                policy.breakeven_activate is not None
                and position.highest_close
                >= position.entry_price * (1.0 + policy.breakeven_activate)
            ):
                position.breakeven_locked = True
            reason = _exit_reason(position, candle, policy, settings.minimum_order_krw)
            if reason is not None and index < len(candles) - 1:
                pending = reason
        elif index < len(candles) - 1 and bool(entry_eligible[index]):
            pending = ("entry", "entry")

        marked = cash + (position.quantity * candle.close if position is not None else 0.0)
        curve.append(marked)
        positions.append(position is not None)

    if position is not None:
        fill_price = candles[-1].close * (1.0 - slip)
        gross = position.quantity * fill_price
        fee = gross * settings.fee_rate
        cash += gross - fee
        position.gross_exit_proceeds += gross
        position.exit_fees += fee
        trades.append(
            _finish_trade(
                position,
                len(candles) - 1,
                fill_price,
                "final_liquidation",
                is_final_liquidation=True,
            )
        )
        curve[-1] = cash
        positions[-1] = False

    returns = [
        curve[index] / curve[index - 1] - 1.0
        for index in range(1, len(curve))
        if curve[index - 1] > 0
    ]
    volatility = pstdev(returns) if len(returns) > 1 else 0.0
    sharpe = (
        fmean(returns) / volatility * sqrt(_periods_per_year(candles))
        if volatility > 0
        else 0.0
    )
    wins = sum(trade.net_pnl > 0 for trade in trades)
    return PolicyBacktestResult(
        initial_equity=initial,
        final_equity=curve[-1],
        total_return=curve[-1] / initial - 1.0,
        max_drawdown=_maximum_drawdown(curve),
        sharpe=sharpe,
        trade_count=len(trades),
        closed_trade_count=sum(not trade.is_final_liquidation for trade in trades),
        win_rate=wins / len(trades) if trades else 0.0,
        exposure=sum(positions[1:]) / (len(positions) - 1),
        partial_exit_count=sum(trade.partial_exit_count for trade in trades),
        partial_rejection_count=partial_rejection_count,
        timecut_exit_count=sum(trade.exit_reason == "timecut" for trade in trades),
        trades=tuple(trades),
        equity_curve=tuple(curve),
        position_curve=tuple(positions),
    )


def build_live_policy_report(
    candles: Sequence[Candle],
    *,
    capitals: Sequence[int] = (50_000, 100_000),
) -> dict[str, Any]:
    """Build identical Wave 5 OOS comparisons for fixed and enhanced exits."""

    config = Wave5Config()
    if len(candles) < config.historical_count:
        raise ValueError("live policy research requires 40,000 completed candles")
    sample = tuple(candles[-config.historical_count :])
    _validate_candles(sample)
    if {candle.market for candle in sample} != {"KRW-BTC"}:
        raise ValueError("live policy research is frozen to KRW-BTC")
    if not capitals or any(isinstance(value, bool) or value < 20_000 for value in capitals):
        raise ValueError("capital scenarios must be integers of at least 20,000 KRW")

    eligibility = live_entry_eligibility(sample)
    oos_start = config.train_size - 1
    execution_candles = sample[oos_start:]
    execution_eligibility = eligibility[oos_start:]
    identity = dataset_manifest(sample)
    scenarios = []
    for capital in capitals:
        cost_cases: dict[str, Any] = {}
        for cost_name, fee_rate, slippage_bps in (
            ("base", 0.0025, 5.0),
            ("double_cost_stress", 0.005, 10.0),
        ):
            settings = TradingSettings(
                initial_capital_krw=int(capital),
                fee_rate=fee_rate,
                slippage_bps=slippage_bps,
                allocation_fraction=0.30,
                minimum_order_krw=5_000,
                maximum_order_krw=max(10_000, int(capital * 0.60)),
                maximum_daily_entries=MAX_DAILY_ENTRIES,
                cash_reserve_krw=0,
            )
            results = {
                "cash": run_policy_backtest(
                    execution_candles,
                    [False] * len(execution_candles),
                    settings=settings,
                    policy=FIXED_EXIT_POLICY,
                ),
                FIXED_EXIT_POLICY.name: run_policy_backtest(
                    execution_candles,
                    execution_eligibility,
                    settings=settings,
                    policy=FIXED_EXIT_POLICY,
                ),
                DEFENSIVE_BASELINE_POLICY.name: run_policy_backtest(
                    execution_candles,
                    execution_eligibility,
                    settings=settings,
                    policy=DEFENSIVE_BASELINE_POLICY,
                ),
                ENHANCED_EXIT_POLICY.name: run_policy_backtest(
                    execution_candles,
                    execution_eligibility,
                    settings=settings,
                    policy=ENHANCED_EXIT_POLICY,
                ),
            }
            cost_cases[cost_name] = {
                name: _metric(result, config) for name, result in results.items()
            }
        base = cost_cases["base"]
        stress = cost_cases["double_cost_stress"]
        enhanced_wins = (
            base[ENHANCED_EXIT_POLICY.name]["compounded_return"]
            > base[DEFENSIVE_BASELINE_POLICY.name]["compounded_return"]
            and stress[ENHANCED_EXIT_POLICY.name]["compounded_return"]
            > stress[DEFENSIVE_BASELINE_POLICY.name]["compounded_return"]
            and base[ENHANCED_EXIT_POLICY.name]["maximum_drawdown"]
            <= base[DEFENSIVE_BASELINE_POLICY.name]["maximum_drawdown"]
        )
        best_name = max(
            base,
            key=lambda name: (
                stress[name]["compounded_return"],
                base[name]["compounded_return"],
                -base[name]["maximum_drawdown"],
            ),
        )
        scenarios.append(
            {
                "initial_capital_krw": int(capital),
                "cost_cases": cost_cases,
                "conclusion": {
                    "enhanced_strictly_outperformed_defensive_baseline": enhanced_wins,
                    "historical_best_after_cost_stress": best_name,
                    "can_promote": False,
                },
            }
        )

    return {
        "schema_version": 1,
        "status": "RESEARCH_ONLY",
        "dataset": {
            "markets": ["KRW-BTC"],
            "candle_count": identity.candle_count,
            "start_at": identity.start_at.isoformat() if identity.start_at else None,
            "end_at": identity.end_at.isoformat() if identity.end_at else None,
            "sha256": identity.sha256,
            "source": "public_completed_30m_ohlcv",
        },
        "validation_geometry": {
            "expanding": True,
            "historical_count": config.historical_count,
            "initial_train_count": config.train_size,
            "test_count": config.test_size,
            "fold_count": config.fold_count,
            "boundaries": [
                {"train": [a, b], "test": [c, d]}
                for a, b, c, d in config.boundaries()
            ],
        },
        "execution": {
            "signal_observed_at": "completed_30m_close",
            "fill_at": "next_30m_open",
            "allow_short": False,
            "allow_pyramiding": False,
            "entry_notional": "min(total_equity*30%, cash*50%)/1.003",
            "minimum_order_krw": 5_000,
            "maximum_daily_entries": MAX_DAILY_ENTRIES,
            "daily_entry_timezone": "Asia/Seoul (UTC+09:00)",
        },
        "entry_proxy": {
            "lookback_bars": LIVE_ENTRY_LOOKBACK,
            "minimum_confidence": ENTRY_CONFIDENCE_THRESHOLD,
            "historically_replayed": [
                "TARO_DIANA_NOVA_VIBE_candle_scores",
                "institutional_displacement",
                "RSI_and_SMA_safety_gate",
            ],
            "unavailable": [
                "cross_market_top_rank_selection",
                "historical_orderbook_bid_ratio",
                "historical_market_warning_state",
                "historical_AI_memory_bias",
            ],
        },
        "exit_policies": {
            FIXED_EXIT_POLICY.name: _policy_payload(FIXED_EXIT_POLICY),
            DEFENSIVE_BASELINE_POLICY.name: _policy_payload(DEFENSIVE_BASELINE_POLICY),
            ENHANCED_EXIT_POLICY.name: _policy_payload(ENHANCED_EXIT_POLICY),
        },
        "costs": {
            "base": {"fee_rate_per_fill": 0.0025, "slippage_bps_per_fill": 5.0},
            "double_cost_stress": {
                "fee_rate_per_fill": 0.005,
                "slippage_bps_per_fill": 10.0,
            },
        },
        "capital_scenarios": scenarios,
        "promotion": {
            "automatic_promotion": "forbidden",
            "can_promote": False,
            "paper_or_live_strategy_changed": False,
            "requires_new_forward_evidence": True,
        },
        "limitations": [
            "BTC-only data cannot reproduce the live cross-market winner selection.",
            "No point-in-time order-book, warning, or AI-memory history was available.",
            "Exit triggers use completed closes and next-open fills; 3-second intrabar execution is not claimed.",
            "All folds are adaptive historical evidence, not prospective forward evidence.",
        ],
    }


def report_digest(payload: Mapping[str, Any]) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()


def _dynamic_entry_notional(cash: float, minimum_order: int) -> float:
    invest = int(min(int(cash * 0.30), int(cash * 0.50)) / 1.003)
    invest = max(invest, minimum_order)
    invest = min(invest, int(cash / 1.003))
    return float(invest) if invest >= minimum_order else 0.0


def _exit_reason(
    position: _Position,
    candle: Candle,
    policy: ExitPolicy,
    minimum_order: int,
) -> tuple[str, str] | None:
    stop = position.entry_price * (
        1.0 + policy.breakeven_buffer
        if position.breakeven_locked
        else 1.0 - policy.stop_loss
    )
    if candle.close <= stop:
        return ("full", "breakeven_lock" if position.breakeven_locked else "stop_loss")
    if candle.close >= position.entry_price * (1.0 + policy.take_profit):
        return ("full", "take_profit")
    if (
        policy.trailing_activate is not None
        and policy.trailing_distance is not None
        and position.highest_close > position.entry_price * (1.0 + policy.trailing_activate)
        and candle.close <= position.highest_close * (1.0 - policy.trailing_distance)
    ):
        return ("full", "trailing_stop")
    if (
        policy.partial_take_profit is not None
        and not position.partial_taken
        and candle.close >= position.entry_price * (1.0 + policy.partial_take_profit)
        and position.quantity * candle.close * policy.partial_fraction >= minimum_order
        and position.quantity * candle.close * (1.0 - policy.partial_fraction) >= minimum_order
    ):
        return ("partial", "partial_take_profit")
    close_time = candle.timestamp + SOURCE_DELTA
    if (
        policy.timecut is not None
        and policy.timecut_band is not None
        and close_time - position.entry_time >= policy.timecut
        and abs(candle.close / position.entry_price - 1.0) <= policy.timecut_band
    ):
        return ("full", "timecut")
    return None


def _finish_trade(
    position: _Position,
    exit_index: int,
    exit_price: float,
    reason: str,
    *,
    is_final_liquidation: bool = False,
) -> PolicyTrade:
    net_pnl = (
        position.gross_exit_proceeds
        - position.notional
        - position.entry_fee
        - position.exit_fees
    )
    return PolicyTrade(
        entry_index=position.entry_index,
        exit_index=exit_index,
        entry_price=position.entry_price,
        exit_price=exit_price,
        notional=position.notional,
        net_pnl=net_pnl,
        exit_reason=reason,
        partial_exit_count=position.partial_exit_count,
        is_final_liquidation=is_final_liquidation,
    )


def _metric(result: PolicyBacktestResult, config: Wave5Config) -> dict[str, Any]:
    folds = []
    for fold, boundary in enumerate(config.boundaries()):
        train_start, train_end, test_start, test_end = boundary
        start = fold * config.test_size
        end = start + config.test_size
        curve = result.equity_curve[start : end + 1]
        fold_trades = tuple(
            trade for trade in result.trades if start < trade.exit_index <= end
        )
        wins = sum(trade.net_pnl > 0 for trade in fold_trades)
        folds.append(
            {
                "fold": fold + 1,
                "train": [train_start, train_end],
                "test": [test_start, test_end],
                "initial_equity_krw": curve[0],
                "final_equity_krw": curve[-1],
                "total_return": curve[-1] / curve[0] - 1.0,
                "maximum_drawdown": _maximum_drawdown(curve),
                "trade_count": len(fold_trades),
                "win_rate": wins / len(fold_trades) if fold_trades else 0.0,
                "exposure": sum(result.position_curve[start + 1 : end + 1])
                / config.test_size,
            }
        )
    digest = sha256(b"bithumb-coin-trader:live-policy-equity:v1\n")
    for value in result.equity_curve:
        digest.update(float(value).hex().encode("ascii"))
        digest.update(b"\n")
    return {
        "compounded_return": result.total_return,
        "maximum_drawdown": result.max_drawdown,
        "sharpe": result.sharpe,
        "trade_count": result.trade_count,
        "closed_trade_count": result.closed_trade_count,
        "win_rate": result.win_rate,
        "exposure": result.exposure,
        "partial_exit_count": result.partial_exit_count,
        "partial_rejection_count": result.partial_rejection_count,
        "timecut_exit_count": result.timecut_exit_count,
        "positive_folds": sum(fold["total_return"] > 0 for fold in folds),
        "oos_equity_evidence": {
            "point_count": len(result.equity_curve),
            "initial_equity_krw": result.initial_equity,
            "final_equity_krw": result.final_equity,
            "sha256": digest.hexdigest(),
        },
        "folds": folds,
    }


def _policy_payload(policy: ExitPolicy) -> dict[str, Any]:
    return {
        "stop_loss_fraction": policy.stop_loss,
        "take_profit_fraction": policy.take_profit,
        "partial_take_profit_fraction": policy.partial_take_profit,
        "partial_exit_fraction": policy.partial_fraction,
        "breakeven_activate_fraction": policy.breakeven_activate,
        "breakeven_buffer_fraction": policy.breakeven_buffer,
        "trailing_activate_fraction": policy.trailing_activate,
        "trailing_distance_fraction": policy.trailing_distance,
        "timecut_seconds": int(policy.timecut.total_seconds()) if policy.timecut else None,
        "timecut_band_fraction": policy.timecut_band,
    }


def _validate_candles(candles: Sequence[Candle]) -> None:
    if not candles:
        raise ValueError("live policy research requires candles")
    if any(
        candle.timestamp.second
        or candle.timestamp.microsecond
        or candle.timestamp.minute % 30
        for candle in candles
    ):
        raise ValueError("live policy research requires aligned 30-minute candles")
    if any(
        candles[index].timestamp <= candles[index - 1].timestamp
        for index in range(1, len(candles))
    ):
        raise ValueError("candles must be strictly chronological")
    if len({candle.market for candle in candles}) != 1:
        raise ValueError("live policy research requires exactly one market")


def _maximum_drawdown(curve: Sequence[float]) -> float:
    peak = curve[0]
    maximum = 0.0
    for value in curve:
        peak = max(peak, value)
        if peak > 0:
            maximum = max(maximum, (peak - value) / peak)
    return maximum


def _periods_per_year(candles: Sequence[Candle]) -> float:
    intervals = [
        (candles[index].timestamp - candles[index - 1].timestamp).total_seconds()
        for index in range(1, len(candles))
    ]
    typical = median(intervals)
    if typical <= 0:
        raise ValueError("candles must be strictly chronological")
    return 365.25 * 24 * 60 * 60 / typical
