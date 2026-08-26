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
    ) -> MultiAssetBacktestResult:
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
        for market, weights in target_weights_by_market.items():
            c_list = candles_by_market[market]
            if len(c_list) != len(weights):
                raise ValueError(f"Weight length mismatch for market {market}")
            for c, w in zip(c_list, weights):
                target_map[(market, c.timestamp)] = w

        markets = sorted(candles_by_market)
        metadata_map = {m: get_market_metadata(m) for m in markets}

        cash = float(self.settings.initial_capital_krw)
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
                        price = c.open * (1.0 - self.settings.slippage_bps / 10_000.0)
                        notional = quantities[m] * price
                        fee = notional * self.settings.fee_rate
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
                if delta < -self.settings.minimum_order_krw and quantities[m] > 0:
                    c = candle_map.get((m, current_time))
                    if c is None:
                        phantom_fills += 1
                        continue
                    sell_notional = min(-delta, quantities[m] * c.open)
                    price = c.open * (1.0 - self.settings.slippage_bps / 10_000.0)
                    sold_qty = min(quantities[m], sell_notional / c.open)
                    notional = sold_qty * price
                    fee = notional * self.settings.fee_rate
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
                        )
                    )
                    if quantities[m] <= 1e-8:
                        quantities[m] = 0.0
                        if was_in_position[m]:
                            normal_round_trips += 1
                            was_in_position[m] = False

            # 5. Phase 2: Execute BUYS with shared cash and total exposure room
            available_cash = max(0.0, cash - self.settings.cash_reserve_krw)
            current_crypto_val = sum(
                quantities[m] * (candle_map.get((m, current_time)).open if candle_map.get((m, current_time)) is not None else last_known_open_price.get(m, 0.0))
                for m in markets
                if quantities[m] > 0
            )
            max_allowed_crypto = current_equity * self.target_total_exposure
            remaining_exposure_room = max(0.0, max_allowed_crypto - current_crypto_val)

            buy_orders = [(m, delta) for m, delta in deltas.items() if delta >= self.settings.minimum_order_krw]
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

                desired_buy = min(delta, available_cash / (1.0 + self.settings.fee_rate), remaining_exposure_room)
                if desired_buy >= self.settings.minimum_order_krw:
                    price = c.open * (1.0 + self.settings.slippage_bps / 10_000.0)
                    bought_qty = desired_buy / price
                    notional = bought_qty * price
                    fee = notional * self.settings.fee_rate
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
                    exit_price = close_p * (1.0 - self.settings.slippage_bps / 10_000.0)
                    notional = quantities[m] * exit_price
                    fee = notional * self.settings.fee_rate
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
        )
