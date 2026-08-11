from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import mean, median, pstdev
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
    is_final_liquidation: bool = False


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
    position_curve: tuple[Signal, ...] = ()


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
        position_curve = [Signal.FLAT]
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
            position_curve.append(position.side if position is not None else Signal.FLAT)

        if position is not None:
            equity, trade = self._close(
                position,
                candles[-1].close,
                len(candles) - 1,
                is_final_liquidation=True,
            )
            trades.append(trade)
            curve[-1] = equity
        final_equity = curve[-1]
        returns = [curve[index] / curve[index - 1] - 1 for index in range(1, len(curve)) if curve[index - 1] > 0]
        volatility = pstdev(returns) if len(returns) > 1 else 0.0
        periods_per_year = self._periods_per_year(candles)
        sharpe = (mean(returns) / volatility * sqrt(periods_per_year)) if volatility > 0 else 0.0
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
            position_curve=tuple(position_curve),
        )

    def slice_result(
        self,
        result: BacktestResult,
        candles: Sequence[Candle],
        *,
        start: int,
        end: int,
    ) -> BacktestResult:
        """Summarize a contiguous evidence slice from one completed run.

        ``start`` and ``end`` are inclusive equity/candle indices. Trades are
        attributed to the slice containing their actual exit, so a position
        crossing a research boundary is counted exactly once.
        """

        if not (0 <= start < end < len(candles)):
            raise ValueError("result slice must contain at least one period")
        if (
            len(result.equity_curve) != len(candles)
            or len(result.position_curve) != len(candles)
        ):
            raise ValueError("result evidence must align with candles")
        curve = result.equity_curve[start : end + 1]
        positions = result.position_curve[start : end + 1]
        trades = tuple(trade for trade in result.trades if start < trade.exit_index <= end)
        returns = [
            curve[index] / curve[index - 1] - 1
            for index in range(1, len(curve))
            if curve[index - 1] > 0
        ]
        volatility = pstdev(returns) if len(returns) > 1 else 0.0
        periods_per_year = self._periods_per_year(candles[start : end + 1])
        sharpe = (mean(returns) / volatility * sqrt(periods_per_year)) if volatility > 0 else 0.0
        wins = sum(trade.net_pnl > 0 for trade in trades)
        initial_equity = curve[0]
        final_equity = curve[-1]
        return BacktestResult(
            initial_equity=initial_equity,
            final_equity=final_equity,
            total_return=final_equity / initial_equity - 1.0,
            max_drawdown=self._max_drawdown(curve),
            sharpe=sharpe,
            trade_count=len(trades),
            win_rate=wins / len(trades) if trades else 0.0,
            exposure=sum(side is not Signal.FLAT for side in positions[1:])
            / (len(positions) - 1),
            trades=trades,
            equity_curve=tuple(curve),
            position_curve=tuple(positions),
        )

    def _open(self, side: Signal, price: float, index: int, notional: float, equity: float) -> _OpenPosition:
        slip = self.settings.slippage_bps / 10_000
        entry_price = price * (1 + slip if side is Signal.LONG else 1 - slip)
        entry_fee = notional * self.settings.fee_rate
        return _OpenPosition(side, index, entry_price, notional / entry_price, notional, equity - entry_fee)

    def _close(
        self,
        position: _OpenPosition,
        price: float,
        index: int,
        *,
        is_final_liquidation: bool = False,
    ) -> tuple[float, Trade]:
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
            is_final_liquidation=is_final_liquidation,
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

    @staticmethod
    def _periods_per_year(candles: Sequence[Candle]) -> float:
        """Infer crypto bar frequency without assuming every dataset is daily."""

        intervals = [
            (candles[index].timestamp - candles[index - 1].timestamp).total_seconds()
            for index in range(1, len(candles))
        ]
        typical_seconds = median(intervals)
        if typical_seconds <= 0:
            raise ValueError("candles must be strictly chronological")
        return 365.25 * 24 * 60 * 60 / typical_seconds
