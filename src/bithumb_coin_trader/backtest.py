from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import mean, pstdev
from typing import Sequence

from .config import TradingSettings
from .models import Candle, Signal


@dataclass(frozen=True, slots=True)
class Trade:
    side: Signal
    entry_index: int
    exit_index: int
    entry_price: float
    exit_price: float
    notional: float
    net_pnl: float


@dataclass(frozen=True, slots=True)
class BacktestResult:
    initial_equity: float
    final_equity: float
    total_return: float
    max_drawdown: float
    sharpe: float
    trade_count: int
    win_rate: float
    exposure: float
    trades: tuple[Trade, ...]
    equity_curve: tuple[float, ...]


@dataclass(slots=True)
class _OpenPosition:
    side: Signal
    entry_index: int
    entry_price: float
    quantity: float
    notional: float
    equity_after_entry_fee: float


class Backtester:
    def __init__(self, settings: TradingSettings | None = None, *, allow_short: bool = False) -> None:
        self.settings = settings or TradingSettings()
        self.allow_short = allow_short

    def run(self, candles: Sequence[Candle], signals: Sequence[Signal]) -> BacktestResult:
        if len(candles) != len(signals):
            raise ValueError("candles and signals must have the same length")
        if len(candles) < 2:
            raise ValueError("at least two candles are required")
        equity = float(self.settings.initial_capital_krw)
        curve = [equity]
        position: _OpenPosition | None = None
        trades: list[Trade] = []
        exposed_periods = 0

        for index in range(1, len(candles)):
            requested = Signal(signals[index - 1])
            if requested is Signal.SHORT and not self.allow_short:
                requested = Signal.FLAT
            open_price = candles[index].open
            if position is not None and requested is not position.side:
                equity, trade = self._close(position, open_price, index)
                trades.append(trade)
                position = None
            if position is None and requested is not Signal.FLAT:
                available = max(0.0, equity - self.settings.cash_reserve_krw)
                notional = min(equity * self.settings.allocation_fraction, available)
                if notional >= self.settings.minimum_order_krw:
                    position = self._open(requested, open_price, index, notional, equity)
                    equity = position.equity_after_entry_fee
            marked = equity
            if position is not None:
                exposed_periods += 1
                marked += self._unrealized(position, candles[index].close)
            curve.append(marked)

        if position is not None:
            equity, trade = self._close(position, candles[-1].close, len(candles) - 1)
            trades.append(trade)
            curve[-1] = equity
        final_equity = curve[-1]
        returns = [curve[index] / curve[index - 1] - 1 for index in range(1, len(curve)) if curve[index - 1] > 0]
        volatility = pstdev(returns) if len(returns) > 1 else 0.0
        sharpe = (mean(returns) / volatility * sqrt(365)) if volatility > 0 else 0.0
        wins = sum(trade.net_pnl > 0 for trade in trades)
        return BacktestResult(
            initial_equity=float(self.settings.initial_capital_krw),
            final_equity=final_equity,
            total_return=final_equity / self.settings.initial_capital_krw - 1,
            max_drawdown=self._max_drawdown(curve),
            sharpe=sharpe,
            trade_count=len(trades),
            win_rate=wins / len(trades) if trades else 0.0,
            exposure=exposed_periods / (len(candles) - 1),
            trades=tuple(trades),
            equity_curve=tuple(curve),
        )

    def _open(self, side: Signal, price: float, index: int, notional: float, equity: float) -> _OpenPosition:
        slip = self.settings.slippage_bps / 10_000
        entry_price = price * (1 + slip if side is Signal.LONG else 1 - slip)
        entry_fee = notional * self.settings.fee_rate
        return _OpenPosition(side, index, entry_price, notional / entry_price, notional, equity - entry_fee)

    def _close(self, position: _OpenPosition, price: float, index: int) -> tuple[float, Trade]:
        slip = self.settings.slippage_bps / 10_000
        exit_price = price * (1 - slip if position.side is Signal.LONG else 1 + slip)
        gross_pnl = int(position.side) * position.quantity * (exit_price - position.entry_price)
        exit_notional = position.quantity * exit_price
        exit_fee = exit_notional * self.settings.fee_rate
        net_equity = position.equity_after_entry_fee + gross_pnl - exit_fee
        total_entry_fee = position.notional * self.settings.fee_rate
        trade = Trade(
            side=position.side,
            entry_index=position.entry_index,
            exit_index=index,
            entry_price=position.entry_price,
            exit_price=exit_price,
            notional=position.notional,
            net_pnl=gross_pnl - total_entry_fee - exit_fee,
        )
        return net_equity, trade

    @staticmethod
    def _unrealized(position: _OpenPosition, price: float) -> float:
        return int(position.side) * position.quantity * (price - position.entry_price)

    @staticmethod
    def _max_drawdown(curve: Sequence[float]) -> float:
        peak = curve[0]
        maximum = 0.0
        for value in curve:
            peak = max(peak, value)
            if peak > 0:
                maximum = max(maximum, (peak - value) / peak)
        return maximum
