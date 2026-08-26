"""Deterministic LONG/FLAT target-weight backtester for Bithumb spot research."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import mean
from typing import Sequence

from .config import TradingSettings
from .daily_strategy_candidates import _validate_daily_candles
from .models import Candle


@dataclass(frozen=True, slots=True)
class RebalanceFill:
    index: int
    side: str
    price: float
    quantity: float
    notional: float
    fee: float
    target_weight: float
    is_final_liquidation: bool = False


@dataclass(frozen=True, slots=True)
class RebalanceBacktestResult:
    initial_equity: float
    final_equity: float
    total_return: float
    max_drawdown: float
    exposure: float
    fill_count: int
    total_fees: float
    gross_traded_notional: float
    turnover: float
    fills: tuple[RebalanceFill, ...]
    equity_curve: tuple[float, ...]
    cash_curve: tuple[float, ...]
    base_quantity_curve: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.fill_count != len(self.fills):
            raise ValueError("fill_count must match fill evidence")
        if not (
            len(self.equity_curve)
            == len(self.cash_curve)
            == len(self.base_quantity_curve)
        ):
            raise ValueError("all ledger curves must align")


class RebalanceBacktester:
    """Execute prior-close target weights at the next daily open.

    A target is never interpreted as leverage or a short.  Orders smaller than
    the configured exchange minimum are deferred: the desired target remains
    in the input series and can produce a later fill once drift is large enough.
    """

    def __init__(self, settings: TradingSettings | None = None) -> None:
        self.settings = settings or TradingSettings()

    def run(
        self,
        candles: Sequence[Candle],
        target_weights: Sequence[float],
        *,
        validate_daily: bool = False,
    ) -> RebalanceBacktestResult:
        if len(candles) != len(target_weights):
            raise ValueError("candles and target weights must have the same length")
        if len(candles) < 2:
            raise ValueError("at least two candles are required")
        if validate_daily:
            _validate_daily_candles(candles)
        else:
            if any(candles[i].timestamp >= candles[i + 1].timestamp for i in range(len(candles) - 1)):
                raise ValueError("candles must be strictly chronological")
        weights = tuple(float(weight) for weight in target_weights)
        if any(not isfinite(weight) or not 0.0 <= weight <= 1.0 for weight in weights):
            raise ValueError("target weights must be finite fractions in [0, 1]")

        cash = float(self.settings.initial_capital_krw)
        quantity = 0.0
        fills: list[RebalanceFill] = []
        equity_curve = [cash]
        cash_curve = [cash]
        quantity_curve = [quantity]
        exposed_periods = 0

        for index in range(1, len(candles)):
            target = weights[index - 1]
            open_price = candles[index].open
            reference_equity = cash + quantity * open_price
            current_reference_value = quantity * open_price
            desired_reference_value = reference_equity * target
            delta = desired_reference_value - current_reference_value

            if delta >= self.settings.minimum_order_krw:
                cash, quantity, fill = self._buy(
                    index=index,
                    open_price=open_price,
                    requested_notional=delta,
                    target_weight=target,
                    cash=cash,
                    quantity=quantity,
                )
                if fill is not None:
                    fills.append(fill)
            elif -delta >= self.settings.minimum_order_krw:
                cash, quantity, fill = self._sell(
                    index=index,
                    open_price=open_price,
                    requested_reference_notional=-delta,
                    target_weight=target,
                    cash=cash,
                    quantity=quantity,
                )
                if fill is not None:
                    fills.append(fill)

            if quantity > 0:
                exposed_periods += 1
            marked = self._liquidation_value(cash, quantity, candles[index].close)
            equity_curve.append(marked)
            cash_curve.append(cash)
            quantity_curve.append(quantity)

        if quantity > 0:
            final_index = len(candles) - 1
            price = candles[-1].close * (1.0 - self.settings.slippage_bps / 10_000.0)
            notional = quantity * price
            fee = notional * self.settings.fee_rate
            cash += notional - fee
            fills.append(
                RebalanceFill(
                    index=final_index,
                    side="sell",
                    price=price,
                    quantity=quantity,
                    notional=notional,
                    fee=fee,
                    target_weight=0.0,
                    is_final_liquidation=True,
                )
            )
            quantity = 0.0
            equity_curve[-1] = cash
            cash_curve[-1] = cash
            quantity_curve[-1] = 0.0

        total_fees = sum(fill.fee for fill in fills)
        gross_notional = sum(fill.notional for fill in fills)
        average_equity = mean(equity_curve)
        return RebalanceBacktestResult(
            initial_equity=float(self.settings.initial_capital_krw),
            final_equity=equity_curve[-1],
            total_return=equity_curve[-1] / self.settings.initial_capital_krw - 1.0,
            max_drawdown=self._max_drawdown(equity_curve),
            exposure=exposed_periods / (len(candles) - 1),
            fill_count=len(fills),
            total_fees=total_fees,
            gross_traded_notional=gross_notional,
            turnover=gross_notional / average_equity if average_equity > 0 else 0.0,
            fills=tuple(fills),
            equity_curve=tuple(equity_curve),
            cash_curve=tuple(cash_curve),
            base_quantity_curve=tuple(quantity_curve),
        )

    def _buy(
        self,
        *,
        index: int,
        open_price: float,
        requested_notional: float,
        target_weight: float,
        cash: float,
        quantity: float,
    ) -> tuple[float, float, RebalanceFill | None]:
        available = max(0.0, cash - self.settings.cash_reserve_krw)
        notional = min(
            requested_notional,
            float(self.settings.maximum_order_krw),
            available / (1.0 + self.settings.fee_rate),
        )
        if notional < self.settings.minimum_order_krw:
            return cash, quantity, None
        price = open_price * (1.0 + self.settings.slippage_bps / 10_000.0)
        bought = notional / price
        fee = notional * self.settings.fee_rate
        cash -= notional + fee
        if cash < self.settings.cash_reserve_krw - 1e-8:
            raise AssertionError("buy violated cash reserve")
        return cash, quantity + bought, RebalanceFill(
            index=index,
            side="buy",
            price=price,
            quantity=bought,
            notional=notional,
            fee=fee,
            target_weight=target_weight,
        )

    def _sell(
        self,
        *,
        index: int,
        open_price: float,
        requested_reference_notional: float,
        target_weight: float,
        cash: float,
        quantity: float,
    ) -> tuple[float, float, RebalanceFill | None]:
        price = open_price * (1.0 - self.settings.slippage_bps / 10_000.0)
        requested_quantity = requested_reference_notional / open_price
        sold = min(quantity, requested_quantity, self.settings.maximum_order_krw / price)
        notional = sold * price
        if notional < self.settings.minimum_order_krw:
            return cash, quantity, None
        fee = notional * self.settings.fee_rate
        remaining = max(0.0, quantity - sold)
        return cash + notional - fee, remaining, RebalanceFill(
            index=index,
            side="sell",
            price=price,
            quantity=sold,
            notional=notional,
            fee=fee,
            target_weight=target_weight,
        )

    def _liquidation_value(self, cash: float, quantity: float, price: float) -> float:
        exit_price = price * (1.0 - self.settings.slippage_bps / 10_000.0)
        notional = quantity * exit_price
        return cash + notional * (1.0 - self.settings.fee_rate)

    @staticmethod
    def _max_drawdown(curve: Sequence[float]) -> float:
        peak = curve[0]
        maximum = 0.0
        for value in curve:
            peak = max(peak, value)
            if peak > 0:
                maximum = max(maximum, (peak - value) / peak)
        return maximum
