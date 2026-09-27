"""Deterministic LONG/FLAT target-weight backtester for Bithumb spot research."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from statistics import mean
from typing import Sequence

from .config import TradingSettings
from .daily_strategy_candidates import _validate_daily_candles
from .models import Candle
from .research_infra.costs import (
    CostScenarioError,
    SpotCostScenario,
    spot_execution_price,
    spot_fill_terms,
    spot_quantity_for_notional,
)


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
    reference_price: float | None = None
    slippage_cost: float = 0.0
    order_type: str = "TAKER"


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
    execution_assumptions: dict[str, object] | None = None
    unsupported_execution_semantics: tuple[str, ...] = ()

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
        cost_scenario: SpotCostScenario | None = None,
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
        fee_rate = (
            cost_scenario.fee_rate("TAKER")
            if cost_scenario is not None
            else self.settings.fee_rate
        )
        minimum_order = max(
            float(self.settings.minimum_order_krw),
            cost_scenario.minimum_order_notional if cost_scenario is not None else 0.0,
        )
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

            if delta >= minimum_order:
                cash, quantity, fill = self._buy(
                    index=index,
                    open_price=open_price,
                    requested_notional=delta,
                    target_weight=target,
                    cash=cash,
                    quantity=quantity,
                    minimum_order=minimum_order,
                    fee_rate=fee_rate,
                    cost_scenario=cost_scenario,
                )
                if fill is not None:
                    fills.append(fill)
            elif -delta >= minimum_order:
                cash, quantity, fill = self._sell(
                    index=index,
                    open_price=open_price,
                    requested_reference_notional=-delta,
                    target_weight=target,
                    cash=cash,
                    quantity=quantity,
                    minimum_order=minimum_order,
                    cost_scenario=cost_scenario,
                )
                if fill is not None:
                    fills.append(fill)

            if quantity > 0:
                exposed_periods += 1
            marked = self._liquidation_value(
                cash, quantity, candles[index].close, cost_scenario=cost_scenario
            )
            equity_curve.append(marked)
            cash_curve.append(cash)
            quantity_curve.append(quantity)

        if quantity > 0:
            final_index = len(candles) - 1
            if cost_scenario is not None:
                final_fill = spot_fill_terms(
                    cost_scenario,
                    reference_price=candles[-1].close,
                    requested_quantity=quantity,
                    side="SELL",
                )
                if abs(final_fill.quantity - quantity) > cost_scenario.lot_size * 1e-6:
                    raise CostScenarioError("position quantity is not aligned to the scenario lot size")
                price = final_fill.fill_price
                notional = final_fill.notional
                fee = final_fill.fee
                slippage_cost = final_fill.slippage_cost
            else:
                price = candles[-1].close * (1.0 - self.settings.slippage_bps / 10_000.0)
                notional = quantity * price
                fee = notional * self.settings.fee_rate
                slippage_cost = abs(price - candles[-1].close) * quantity
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
                    reference_price=candles[-1].close,
                    slippage_cost=slippage_cost,
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
            execution_assumptions=cost_scenario.to_dict() if cost_scenario else None,
            unsupported_execution_semantics=(
                self._unsupported_cost_semantics(cost_scenario)
                if cost_scenario is not None
                else ()
            ),
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
        minimum_order: float,
        fee_rate: float,
        cost_scenario: SpotCostScenario | None,
    ) -> tuple[float, float, RebalanceFill | None]:
        available = max(0.0, cash - self.settings.cash_reserve_krw)
        notional = min(
            requested_notional,
            float(self.settings.maximum_order_krw),
            available / (1.0 + fee_rate),
        )
        if notional < minimum_order:
            return cash, quantity, None
        if cost_scenario is not None:
            try:
                requested_quantity = spot_quantity_for_notional(
                    cost_scenario,
                    reference_price=open_price,
                    requested_notional=notional,
                    side="BUY",
                )
                fill_terms = spot_fill_terms(
                    cost_scenario,
                    reference_price=open_price,
                    requested_quantity=requested_quantity,
                    side="BUY",
                )
            except CostScenarioError:
                return cash, quantity, None
            if fill_terms.notional < minimum_order:
                return cash, quantity, None
            price = fill_terms.fill_price
            bought = fill_terms.quantity
            fill_notional = fill_terms.notional
            fee = fill_terms.fee
            slippage_cost = fill_terms.slippage_cost
        else:
            price = open_price * (1.0 + self.settings.slippage_bps / 10_000.0)
            bought = notional / price
            fill_notional = notional
            fee = notional * self.settings.fee_rate
            slippage_cost = abs(price - open_price) * bought
        cash -= fill_notional + fee
        if cash < self.settings.cash_reserve_krw - 1e-8:
            raise AssertionError("buy violated cash reserve")
        return cash, quantity + bought, RebalanceFill(
            index=index,
            side="buy",
            price=price,
            quantity=bought,
            notional=fill_notional,
            fee=fee,
            target_weight=target_weight,
            reference_price=open_price,
            slippage_cost=slippage_cost,
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
        minimum_order: float,
        cost_scenario: SpotCostScenario | None,
    ) -> tuple[float, float, RebalanceFill | None]:
        requested_quantity = requested_reference_notional / open_price
        if cost_scenario is not None:
            price = spot_execution_price(cost_scenario, open_price, "SELL")
        else:
            price = open_price * (1.0 - self.settings.slippage_bps / 10_000.0)
        requested_quantity = min(
            quantity, requested_quantity, self.settings.maximum_order_krw / price
        )
        if cost_scenario is not None:
            try:
                fill_terms = spot_fill_terms(
                    cost_scenario,
                    reference_price=open_price,
                    requested_quantity=requested_quantity,
                    side="SELL",
                )
            except CostScenarioError:
                return cash, quantity, None
            sold = fill_terms.quantity
            price = fill_terms.fill_price
            notional = fill_terms.notional
            fee = fill_terms.fee
            slippage_cost = fill_terms.slippage_cost
        else:
            sold = requested_quantity
            notional = sold * price
            fee = notional * self.settings.fee_rate
            slippage_cost = abs(open_price - price) * sold
        if notional < minimum_order:
            return cash, quantity, None
        remaining = max(0.0, quantity - sold)
        return cash + notional - fee, remaining, RebalanceFill(
            index=index,
            side="sell",
            price=price,
            quantity=sold,
            notional=notional,
            fee=fee,
            target_weight=target_weight,
            reference_price=open_price,
            slippage_cost=slippage_cost,
        )

    def _liquidation_value(
        self,
        cash: float,
        quantity: float,
        price: float,
        *,
        cost_scenario: SpotCostScenario | None = None,
    ) -> float:
        if quantity <= 0:
            return cash
        if cost_scenario is not None:
            terms = spot_fill_terms(
                cost_scenario,
                reference_price=price,
                requested_quantity=quantity,
                side="SELL",
                enforce_minimum=False,
            )
            return cash + terms.notional - terms.fee
        exit_price = price * (1.0 - self.settings.slippage_bps / 10_000.0)
        notional = quantity * exit_price
        return cash + notional * (1.0 - self.settings.fee_rate)

    @staticmethod
    def _unsupported_cost_semantics(scenario: SpotCostScenario) -> tuple[str, ...]:
        unsupported: list[str] = []
        if scenario.latency_ms > 0:
            unsupported.append("LATENCY_NOT_MODELED_AT_CANDLE_RESOLUTION")
        if scenario.partial_fill_probability is None:
            unsupported.append("PARTIAL_FILLS_EXPLICITLY_UNSUPPORTED_BY_SCENARIO")
        elif scenario.partial_fill_probability > 0:
            unsupported.append("PARTIAL_FILL_PROBABILITY_NOT_MODELED_BY_CANDLE_ENGINE")
        return tuple(unsupported)

    @staticmethod
    def _max_drawdown(curve: Sequence[float]) -> float:
        peak = curve[0]
        maximum = 0.0
        for value in curve:
            peak = max(peak, value)
            if peak > 0:
                maximum = max(maximum, (peak - value) / peak)
        return maximum
