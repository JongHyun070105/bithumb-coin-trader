"""True Multi-Asset Shared-Cash Backtester with Exact State Machines and Hardened Accounting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from math import isfinite
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence

from .config import TradingSettings
from .market_registry import MarketMetadata, get_market_metadata
from .models import Candle
from .research_infra.costs import (
    CostScenarioError,
    SpotCostScenario,
    spot_fill_terms,
    spot_quantity_for_notional,
)


@dataclass(frozen=True, slots=True)
class MultiAssetFill:
    timestamp: datetime
    market: str
    side: str  # buy or sell
    price: float
    quantity: float
    notional: float
    fee: float
    reason: str = "rebalance"  # rebalance, delist_exit, final_liquidation
    reference_price: float | None = None
    slippage_cost: float = 0.0
    order_type: str = "TAKER"

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "market": self.market,
            "side": self.side,
            "price": round(self.price, 4),
            "quantity": round(self.quantity, 8),
            "notional": round(self.notional, 2),
            "fee": round(self.fee, 2),
            "reason": self.reason,
            "reference_price": self.reference_price,
            "slippage_cost": round(self.slippage_cost, 2),
            "order_type": self.order_type,
        }


@dataclass(frozen=True, slots=True)
class MultiAssetBacktestResult:
    initial_equity: float
    final_equity: float
    total_return: float
    cagr: float
    max_drawdown: float
    sharpe: float
    fill_count: int
    normal_round_trips: int
    delisting_forced_exits: int
    final_liquidations: int
    trades_per_week: float
    total_fees_krw: float
    observed_max_target_total_exposure: float
    observed_max_target_per_asset_exposure: float
    max_realized_total_exposure: float
    max_realized_per_asset_exposure: float
    min_observed_cash: float
    cash_violations_count: int
    total_drift_violations_count: int
    per_asset_drift_violations_count: int
    unlisted_orders_count: int
    delisted_orders_count: int
    suspended_orders_blocked_count: int
    phantom_fills_count: int
    unresolved_delisted_positions_count: int
    fills: tuple[MultiAssetFill, ...]
    timestamps: tuple[datetime, ...]
    equity_curve: tuple[float, ...]
    cash_curve: tuple[float, ...]
    exposure_curve: tuple[float, ...]
    per_asset_exposure_curves: dict[str, tuple[float, ...]]
    execution_assumptions: dict[str, Any] | None = None
    unsupported_execution_semantics: tuple[str, ...] = ()

    def canonical_json_dump(self) -> str:
        payload = {
            "initial_equity": self.initial_equity,
            "final_equity": round(self.final_equity, 4),
            "total_return": round(self.total_return, 6),
            "total_fees_krw": round(self.total_fees_krw, 2),
            "fill_count": self.fill_count,
            "normal_round_trips": self.normal_round_trips,
            "delisting_forced_exits": self.delisting_forced_exits,
            "final_liquidations": self.final_liquidations,
            "observed_max_target_total_exposure": round(self.observed_max_target_total_exposure, 6),
            "observed_max_target_per_asset_exposure": round(self.observed_max_target_per_asset_exposure, 6),
            "fills": [f.to_canonical_dict() for f in self.fills],
            "equity_curve": [round(x, 4) for x in self.equity_curve],
            "cash_curve": [round(x, 4) for x in self.cash_curve],
            "exposure_curve": [round(x, 6) for x in self.exposure_curve],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class MultiAssetSharedCashBacktester:
    """Hardened Multi-Asset Shared-Cash Backtester with exact price caching and position state machines."""

    def __init__(
        self,
        settings: TradingSettings | None = None,
        *,
        target_total_exposure: float = 0.30,
        drift_total_exposure_limit: float = 0.35,
        target_per_asset_exposure: float = 0.15,
        drift_per_asset_exposure_limit: float = 0.18,
        min_listing_days: int = 30,
    ) -> None:
        exposure_values = {
            "target_total_exposure": target_total_exposure,
            "drift_total_exposure_limit": drift_total_exposure_limit,
            "target_per_asset_exposure": target_per_asset_exposure,
            "drift_per_asset_exposure_limit": drift_per_asset_exposure_limit,
        }
        if any(not isfinite(value) or not 0.0 <= value <= 1.0 for value in exposure_values.values()):
            raise ValueError("exposure settings must be finite fractions in [0, 1]")
        if isinstance(min_listing_days, bool) or not isinstance(min_listing_days, int) or min_listing_days < 0:
            raise ValueError("min_listing_days must be a non-negative integer")
        self.settings = settings or TradingSettings()
        self.target_total_exposure = target_total_exposure
        self.drift_total_exposure_limit = drift_total_exposure_limit
        self.target_per_asset_exposure = target_per_asset_exposure
        self.drift_per_asset_exposure_limit = drift_per_asset_exposure_limit
        self.min_listing_days = min_listing_days

    def run(
        self,
        candles_by_market: Mapping[str, Sequence[Candle]],
        target_weights_by_market: Mapping[str, Sequence[float]],
        *,
        cost_scenario: SpotCostScenario | None = None,
    ) -> MultiAssetBacktestResult:
        if not candles_by_market:
            raise ValueError("at least one market series is required")
        unknown_weight_markets = set(target_weights_by_market) - set(candles_by_market)
        if unknown_weight_markets:
            raise ValueError(
                "target weights contain markets without candles: "
                + ", ".join(sorted(unknown_weight_markets))
            )

        normalized_weights: dict[str, tuple[float, ...]] = {}
        for market, candle_series in candles_by_market.items():
            if not candle_series:
                raise ValueError(f"candle series for {market} is empty")
            timestamps = [c.timestamp for c in candle_series]
            if any(left >= right for left, right in zip(timestamps, timestamps[1:])):
                raise ValueError(f"candles for {market} must have unique chronological timestamps")
            if any(c.market != market for c in candle_series):
                raise ValueError(f"candle market does not match mapping key {market}")

            weights = target_weights_by_market.get(market, (0.0,) * len(candle_series))
            if len(candle_series) != len(weights):
                raise ValueError(f"Weight length mismatch for market {market}")
            try:
                values = tuple(float(weight) for weight in weights)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"target weights for {market} must be numeric fractions") from exc
            if any(not isfinite(weight) or not 0.0 <= weight <= 1.0 for weight in values):
                raise ValueError(f"target weights for {market} must be finite fractions in [0, 1]")
            normalized_weights[market] = values

        all_timestamps = sorted(
            {c.timestamp for c_list in candles_by_market.values() for c in c_list}
        )
        if len(all_timestamps) < 2:
            raise ValueError("At least two timestamps are required for backtesting")

        candle_map: dict[tuple[str, datetime], Candle] = {
            (c.market, c.timestamp): c
            for c_list in candles_by_market.values()
            for c in c_list
        }
        target_map: dict[tuple[str, datetime], float] = {}
        for market, weights in normalized_weights.items():
            c_list = candles_by_market[market]
            for c, w in zip(c_list, weights):
                target_map[(market, c.timestamp)] = w

        markets = sorted(candles_by_market)
        metadata_map = {m: get_market_metadata(m) for m in markets}

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
        quantities: dict[str, float] = {m: 0.0 for m in markets}
        last_known_open_price: dict[str, float] = {}
        last_known_close_price: dict[str, float] = {}

        fills: list[MultiAssetFill] = []
        equity_curve: list[float] = [cash]
        cash_curve: list[float] = [cash]
        exposure_curve: list[float] = [0.0]
        per_asset_exposure_curves: dict[str, list[float]] = {m: [0.0] for m in markets}

        cash_violations = 0
        total_drift_violations = 0
        per_asset_drift_violations = 0
        unlisted_orders = 0
        delisted_orders = 0
        suspended_orders_blocked = 0
        phantom_fills = 0
        unresolved_delisted_positions = 0

        observed_max_target_total = 0.0
        observed_max_target_per_asset = 0.0

        was_in_position: dict[str, bool] = {m: False for m in markets}
        normal_round_trips = 0
        delisting_forced_exits = 0
        final_liquidations = 0

        # Prime initial prices
        for m in markets:
            first_c = candle_map.get((m, all_timestamps[0]))
            if first_c is not None:
                last_known_open_price[m] = first_c.open
                last_known_close_price[m] = first_c.close

        for t_idx in range(1, len(all_timestamps)):
            current_time = all_timestamps[t_idx]
            prev_time = all_timestamps[t_idx - 1]

            # Update last known prices for present candles
            for m in markets:
                c = candle_map.get((m, current_time))
                if c is not None:
                    last_known_open_price[m] = c.open
                    last_known_close_price[m] = c.close

            # 1. Evaluate current portfolio equity using last known open prices
            current_equity = cash
            for m in markets:
                if quantities[m] > 0 and m in last_known_open_price:
                    current_equity += quantities[m] * last_known_open_price[m]

            # 2. Delisting Pre-Liquidation Check (only on actual present candle before delisting)
            for m in markets:
                meta = metadata_map[m]
                if quantities[m] > 0 and meta.is_delisted(current_time):
                    c = candle_map.get((m, current_time))
                    if c is not None:
                        # Liquidate at actual present open price
                        if cost_scenario is not None:
                            fill_terms = spot_fill_terms(
                                cost_scenario,
                                reference_price=c.open,
                                requested_quantity=quantities[m],
                                side="SELL",
                            )
                            self._require_full_lot_fill(fill_terms.quantity, quantities[m], cost_scenario)
                            price = fill_terms.fill_price
                            notional = fill_terms.notional
                            fee = fill_terms.fee
                            slippage_cost = fill_terms.slippage_cost
                        else:
                            price = c.open * (1.0 - self.settings.slippage_bps / 10_000.0)
                            notional = quantities[m] * price
                            fee = notional * self.settings.fee_rate
                            slippage_cost = abs(c.open - price) * quantities[m]
                        cash += notional - fee
                        fills.append(
                            MultiAssetFill(
                                timestamp=current_time,
                                market=m,
                                side="sell",
                                price=price,
                                quantity=quantities[m],
                                notional=notional,
                                fee=fee,
                                reason="delist_exit",
                                reference_price=c.open,
                                slippage_cost=slippage_cost,
                            )
                        )
                        quantities[m] = 0.0
                        if was_in_position[m]:
                            delisting_forced_exits += 1
                            was_in_position[m] = False
                    else:
                        # Cannot fill on missing candle after delisting (no phantom fill)
                        unresolved_delisted_positions += 1

            # 3. Calculate target deltas
            raw_targets: dict[str, float] = {}
            for m in markets:
                meta = metadata_map[m]
                raw_w = target_map.get((m, prev_time), 0.0)

                # State Machine Policy Check:
                # Delisted or Suspended -> target 0 for new buy, but if suspended no rebalance allowed
                if meta.is_delisted(current_time):
                    raw_targets[m] = 0.0
                elif meta.is_suspended(current_time):
                    # Suspended: maintain current holding value proportion, no new trading
                    if quantities[m] > 0 and m in last_known_open_price:
                        raw_targets[m] = (quantities[m] * last_known_open_price[m]) / current_equity if current_equity > 0 else 0.0
                    else:
                        raw_targets[m] = 0.0
                elif meta.is_warning(current_time):
                    # Warning: New BUY strictly prohibited. Existing holdings may hold or exit based on strategy signal
                    if quantities[m] > 0:
                        raw_targets[m] = min(raw_w, self.target_per_asset_exposure)
                    else:
                        raw_targets[m] = 0.0
                elif meta.is_eligible_for_new_entry(current_time, min_listing_days=self.min_listing_days):
                    raw_targets[m] = min(raw_w, self.target_per_asset_exposure)
                else:
                    raw_targets[m] = 0.0

            # Normalize if sum exceeds target total exposure cap
            sum_targets = sum(raw_targets.values())
            if sum_targets > self.target_total_exposure and sum_targets > 0:
                scale = self.target_total_exposure / sum_targets
                capped_targets = {m: w * scale for m, w in raw_targets.items()}
            else:
                capped_targets = raw_targets

            # Record actual observed target metrics
            observed_max_target_total = max(observed_max_target_total, sum(capped_targets.values()))
            for w in capped_targets.values():
                observed_max_target_per_asset = max(observed_max_target_per_asset, w)

            deltas: dict[str, float] = {}
            for m in markets:
                meta = metadata_map[m]
                # If market is suspended, ALL orders are strictly blocked
                if meta.is_suspended(current_time):
                    deltas[m] = 0.0
                    continue

                c = candle_map.get((m, current_time))
                open_p = c.open if c is not None else last_known_open_price.get(m, 0.0)
                if open_p > 0:
                    curr_val = quantities[m] * open_p
                    desired_val = current_equity * capped_targets[m]
                    if c is None:
                        deltas[m] = 0.0
                    else:
                        deltas[m] = desired_val - curr_val
                else:
                    deltas[m] = 0.0

            # 4. Phase 1: Execute all SELLS first
            for m, delta in deltas.items():
                meta = metadata_map[m]
                if meta.is_suspended(current_time):
                    suspended_orders_blocked += 1
                    continue
                if delta < -minimum_order and quantities[m] > 0:
                    c = candle_map.get((m, current_time))
                    if c is None:
                        phantom_fills += 1
                        continue
                    sell_notional = min(-delta, quantities[m] * c.open)
                    requested_quantity = min(quantities[m], sell_notional / c.open)
                    if cost_scenario is not None:
                        try:
                            fill_terms = spot_fill_terms(
                                cost_scenario,
                                reference_price=c.open,
                                requested_quantity=requested_quantity,
                                side="SELL",
                            )
                        except CostScenarioError:
                            continue
                        if fill_terms.notional < minimum_order:
                            continue
                        sold_qty = fill_terms.quantity
                        price = fill_terms.fill_price
                        notional = fill_terms.notional
                        fee = fill_terms.fee
                        slippage_cost = fill_terms.slippage_cost
                    else:
                        price = c.open * (1.0 - self.settings.slippage_bps / 10_000.0)
                        sold_qty = requested_quantity
                        notional = sold_qty * price
                        fee = notional * self.settings.fee_rate
                        slippage_cost = abs(c.open - price) * sold_qty
                    cash += notional - fee
                    quantities[m] -= sold_qty
                    fills.append(
                        MultiAssetFill(
                            timestamp=current_time,
                            market=m,
                            side="sell",
                            price=price,
                            quantity=sold_qty,
                            notional=notional,
                            fee=fee,
                            reason="rebalance",
                            reference_price=c.open,
                            slippage_cost=slippage_cost,
                        )
                    )
                    if quantities[m] <= 1e-8:
                        quantities[m] = 0.0
                        if was_in_position[m]:
                            normal_round_trips += 1
                            was_in_position[m] = False

            # 5. Phase 2: Execute BUYS with shared cash and total exposure room
            available_cash = max(0.0, cash - self.settings.cash_reserve_krw)
            current_crypto_val = 0.0
            for m in markets:
                if quantities[m] <= 0:
                    continue
                candle = candle_map.get((m, current_time))
                open_price = candle.open if candle is not None else last_known_open_price.get(m, 0.0)
                current_crypto_val += quantities[m] * open_price
            max_allowed_crypto = current_equity * self.target_total_exposure
            remaining_exposure_room = max(0.0, max_allowed_crypto - current_crypto_val)

            buy_orders = [(m, delta) for m, delta in deltas.items() if delta >= minimum_order]
            buy_orders.sort(key=lambda x: x[0])

            for m, delta in buy_orders:
                meta = metadata_map[m]
                if meta.is_suspended(current_time):
                    suspended_orders_blocked += 1
                    continue
                if current_time < meta.listed_at:
                    unlisted_orders += 1
                    continue
                if meta.is_delisted(current_time):
                    delisted_orders += 1
                    continue

                c = candle_map.get((m, current_time))
                if c is None:
                    phantom_fills += 1
                    continue

                desired_buy = min(delta, available_cash / (1.0 + fee_rate), remaining_exposure_room)
                if desired_buy >= minimum_order:
                    if cost_scenario is not None:
                        try:
                            requested_quantity = spot_quantity_for_notional(
                                cost_scenario,
                                reference_price=c.open,
                                requested_notional=desired_buy,
                                side="BUY",
                            )
                            fill_terms = spot_fill_terms(
                                cost_scenario,
                                reference_price=c.open,
                                requested_quantity=requested_quantity,
                                side="BUY",
                            )
                        except CostScenarioError:
                            continue
                        if fill_terms.notional < minimum_order:
                            continue
                        price = fill_terms.fill_price
                        bought_qty = fill_terms.quantity
                        notional = fill_terms.notional
                        fee = fill_terms.fee
                        slippage_cost = fill_terms.slippage_cost
                    else:
                        price = c.open * (1.0 + self.settings.slippage_bps / 10_000.0)
                        bought_qty = desired_buy / price
                        notional = bought_qty * price
                        fee = notional * self.settings.fee_rate
                        slippage_cost = abs(price - c.open) * bought_qty
                    cash -= notional + fee
                    quantities[m] += bought_qty
                    available_cash -= notional + fee
                    remaining_exposure_room -= notional
                    fills.append(
                        MultiAssetFill(
                            timestamp=current_time,
                            market=m,
                            side="buy",
                            price=price,
                            quantity=bought_qty,
                            notional=notional,
                            fee=fee,
                            reason="rebalance",
                            reference_price=c.open,
                            slippage_cost=slippage_cost,
                        )
                    )
                    was_in_position[m] = True

            # 6. Mark to market at close prices
            marked_equity = cash
            total_crypto_marked = 0.0
            for m in markets:
                c = candle_map.get((m, current_time))
                close_p = c.close if c is not None else last_known_close_price.get(m, 0.0)
                if close_p > 0 and quantities[m] > 0:
                    if cost_scenario is not None:
                        mark = spot_fill_terms(
                            cost_scenario,
                            reference_price=close_p,
                            requested_quantity=quantities[m],
                            side="SELL",
                            enforce_minimum=False,
                        )
                        exit_price = mark.fill_price
                        crypto_val = mark.notional - mark.fee
                    else:
                        exit_price = close_p * (1.0 - self.settings.slippage_bps / 10_000.0)
                        crypto_val = quantities[m] * exit_price * (1.0 - self.settings.fee_rate)
                    marked_equity += crypto_val
                    total_crypto_marked += crypto_val
                    per_asset_ratio = (crypto_val / marked_equity) if marked_equity > 0 else 0.0
                    per_asset_exposure_curves[m].append(per_asset_ratio)
                    if per_asset_ratio > self.drift_per_asset_exposure_limit:
                        per_asset_drift_violations += 1
                else:
                    per_asset_exposure_curves[m].append(0.0)

            if cash < -1e-6:
                cash_violations += 1
            total_exp_ratio = (total_crypto_marked / marked_equity) if marked_equity > 0 else 0.0
            if total_exp_ratio > self.drift_total_exposure_limit:
                total_drift_violations += 1

            equity_curve.append(marked_equity)
            cash_curve.append(cash)
            exposure_curve.append(total_exp_ratio)

        # Final liquidation at last timestamp
        final_time = all_timestamps[-1]
        for m in markets:
            if quantities[m] > 0:
                c = candle_map.get((m, final_time))
                close_p = c.close if c is not None else last_known_close_price.get(m, 0.0)
                if close_p > 0:
                    if cost_scenario is not None:
                        fill_terms = spot_fill_terms(
                            cost_scenario,
                            reference_price=close_p,
                            requested_quantity=quantities[m],
                            side="SELL",
                        )
                        self._require_full_lot_fill(fill_terms.quantity, quantities[m], cost_scenario)
                        exit_price = fill_terms.fill_price
                        notional = fill_terms.notional
                        fee = fill_terms.fee
                        slippage_cost = fill_terms.slippage_cost
                    else:
                        exit_price = close_p * (1.0 - self.settings.slippage_bps / 10_000.0)
                        notional = quantities[m] * exit_price
                        fee = notional * self.settings.fee_rate
                        slippage_cost = abs(close_p - exit_price) * quantities[m]
                    cash += notional - fee
                    fills.append(
                        MultiAssetFill(
                            timestamp=final_time,
                            market=m,
                            side="sell",
                            price=exit_price,
                            quantity=quantities[m],
                            notional=notional,
                            fee=fee,
                            reason="final_liquidation",
                            reference_price=close_p,
                            slippage_cost=slippage_cost,
                        )
                    )
                    quantities[m] = 0.0
                    final_liquidations += 1

        equity_curve[-1] = cash
        cash_curve[-1] = cash
        exposure_curve[-1] = 0.0

        total_days = (all_timestamps[-1] - all_timestamps[0]).total_seconds() / 86400.0
        total_weeks = total_days / 7.0
        total_years = total_days / 365.25

        total_return = (equity_curve[-1] / self.settings.initial_capital_krw) - 1.0
        cagr = ((equity_curve[-1] / self.settings.initial_capital_krw) ** (1.0 / total_years) - 1.0) if total_years > 0 else 0.0

        rets = [equity_curve[i] / equity_curve[i - 1] - 1.0 for i in range(1, len(equity_curve))]
        vol = pstdev(rets) if len(rets) > 1 else 0.0
        bars_per_year = (len(all_timestamps) - 1) / total_years if total_years > 0 else 365.25
        sharpe = (mean(rets) / vol * (bars_per_year ** 0.5)) if vol > 0 else 0.0

        peak = equity_curve[0]
        max_dd = 0.0
        for val in equity_curve:
            peak = max(peak, val)
            if peak > 0:
                max_dd = max(max_dd, (peak - val) / peak)

        max_realized_per_asset = max(
            (max(curve) for curve in per_asset_exposure_curves.values()),
            default=0.0,
        )

        return MultiAssetBacktestResult(
            initial_equity=float(self.settings.initial_capital_krw),
            final_equity=equity_curve[-1],
            total_return=total_return,
            cagr=cagr,
            max_drawdown=max_dd,
            sharpe=sharpe,
            fill_count=len(fills),
            normal_round_trips=normal_round_trips,
            delisting_forced_exits=delisting_forced_exits,
            final_liquidations=final_liquidations,
            trades_per_week=normal_round_trips / total_weeks if total_weeks > 0 else 0.0,
            total_fees_krw=sum(f.fee for f in fills),
            observed_max_target_total_exposure=observed_max_target_total,
            observed_max_target_per_asset_exposure=observed_max_target_per_asset,
            max_realized_total_exposure=max(exposure_curve),
            max_realized_per_asset_exposure=max_realized_per_asset,
            min_observed_cash=min(cash_curve),
            cash_violations_count=cash_violations,
            total_drift_violations_count=total_drift_violations,
            per_asset_drift_violations_count=per_asset_drift_violations,
            unlisted_orders_count=unlisted_orders,
            delisted_orders_count=delisted_orders,
            suspended_orders_blocked_count=suspended_orders_blocked,
            phantom_fills_count=phantom_fills,
            unresolved_delisted_positions_count=unresolved_delisted_positions,
            fills=tuple(fills),
            timestamps=tuple(all_timestamps),
            equity_curve=tuple(equity_curve),
            cash_curve=tuple(cash_curve),
            exposure_curve=tuple(exposure_curve),
            per_asset_exposure_curves={m: tuple(c) for m, c in per_asset_exposure_curves.items()},
            execution_assumptions=cost_scenario.to_dict() if cost_scenario else None,
            unsupported_execution_semantics=(
                self._unsupported_cost_semantics(cost_scenario)
                if cost_scenario is not None
                else ()
            ),
        )

    @staticmethod
    def _require_full_lot_fill(
        filled_quantity: float,
        requested_quantity: float,
        scenario: SpotCostScenario,
    ) -> None:
        if abs(filled_quantity - requested_quantity) > scenario.lot_size * 1e-6:
            raise CostScenarioError("position quantity is not aligned to the scenario lot size")

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
