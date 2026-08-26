"""Composite Core + Satellite portfolio backtester with multi-cost regime support."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import mean, pstdev
from typing import Any, Sequence

from .config import TradingSettings
from .models import Candle
from .rebalance_backtest import RebalanceBacktestResult, RebalanceBacktester


@dataclass(frozen=True, slots=True)
class CompositePortfolioResult:
    core_name: str
    satellite_name: str
    core_ratio: float
    satellite_ratio: float
    fee_regime: str
    initial_equity: float
    final_equity: float
    total_return: float
    cagr: float
    max_drawdown: float
    sharpe: float
    exposure: float
    fill_count: int
    round_trip_trades: int
    trades_per_year: float
    mean_holding_days: float
    total_fees_krw: float
    equity_curve: tuple[float, ...]


def run_composite_portfolio_backtest(
    candles: Sequence[Candle],
    core_weights: Sequence[float],
    satellite_weights: Sequence[float],
    settings: TradingSettings,
    *,
    core_name: str = "v4_adaptive_donchian_atr",
    satellite_name: str = "v6_satellite",
    core_ratio: float = 0.70,
    satellite_ratio: float = 0.30,
    fee_regime_name: str = "live_zero_fee",
) -> CompositePortfolioResult:
    """Combine Core and Satellite weight streams and evaluate under specified fee settings."""
    if len(core_weights) != len(candles) or len(satellite_weights) != len(candles):
        raise ValueError("Weights and candles length mismatch")

    # Combine weights: total weight = core_ratio * core_w + satellite_ratio * sat_w
    combined_weights: list[float] = [
        min(1.0, core_ratio * cw + satellite_ratio * sw)
        for cw, sw in zip(core_weights, satellite_weights)
    ]

    backtester = RebalanceBacktester(settings)
    res: RebalanceBacktestResult = backtester.run(candles, combined_weights)

    # Analyze round-trip trades
    fills = res.fills
    trades = []
    current_entry = None
    for fill in fills:
        if fill.side == "buy" and current_entry is None:
            current_entry = fill
        elif fill.side == "sell" and current_entry is not None:
            holding_days = fill.index - current_entry.index
            trades.append(holding_days)
            current_entry = None

    holding_days_mean = mean(trades) if trades else 0.0
    total_days = len(candles)
    years = total_days / 365.25
    trades_per_year = len(trades) / years if years > 0 else 0.0

    # CAGR calculation
    cagr = ((res.final_equity / res.initial_equity) ** (1.0 / years) - 1.0) if (years > 0 and res.final_equity > 0) else 0.0

    # Sharpe calculation
    curve = res.equity_curve
    rets = [curve[i] / curve[i - 1] - 1.0 for i in range(1, len(curve))]
    vol = pstdev(rets) if len(rets) > 1 else 0.0
    sharpe = mean(rets) / vol * sqrt(365.25) if vol > 0 else 0.0

    return CompositePortfolioResult(
        core_name=core_name,
        satellite_name=satellite_name,
        core_ratio=core_ratio,
        satellite_ratio=satellite_ratio,
        fee_regime=fee_regime_name,
        initial_equity=res.initial_equity,
        final_equity=res.final_equity,
        total_return=res.total_return,
        cagr=cagr,
        max_drawdown=res.max_drawdown,
        sharpe=sharpe,
        exposure=res.exposure,
        fill_count=res.fill_count,
        round_trip_trades=len(trades),
        trades_per_year=trades_per_year,
        mean_holding_days=holding_days_mean,
        total_fees_krw=res.total_fees,
        equity_curve=res.equity_curve,
    )
