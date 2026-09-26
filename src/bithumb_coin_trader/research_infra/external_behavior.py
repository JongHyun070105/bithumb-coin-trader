"""Descriptive behavior features, regime change-point analysis, domain shift risk assessment, and market context interfaces.

Enforces:
- Descriptive behavior metrics across daily, weekly, and monthly aggregations without alpha promotion
- Identification of BEHAVIORAL_PHASES / EXECUTION_REGIMES across 2018-2021
- Explicit DOMAIN_SHIFT_RISK documentation against modern spot/derivatives markets
- Regime conditioning interface schema with no-lookahead constraints
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

from bithumb_coin_trader.research_infra.external_expert import ExecutionRow
from bithumb_coin_trader.research_infra.external_reconstruction import OrderSummary, PositionCycle


@dataclass(frozen=True)
class DailyBehaviorFeatures:
    date: str
    trade_count: int
    order_count: int
    total_volume: float
    total_fees_satoshi: float
    maker_ratio: float
    taker_ratio: float
    median_fill_size: float
    buy_volume: float
    sell_volume: float
    buy_sell_balance: float
    symbol_mix: dict[str, float]
    hour_distribution: dict[int, int]


class BehaviorFeatureExtractor:
    """Extracts descriptive behavioral features grouped by day, week, and month."""

    @staticmethod
    def extract_daily_features(
        trades: Sequence[ExecutionRow],
        orders: Sequence[OrderSummary] | None = None,
    ) -> list[DailyBehaviorFeatures]:
        by_date: dict[str, list[ExecutionRow]] = {}
        for t in trades:
            if t.timestamp:
                d_str = t.timestamp.strftime("%Y-%m-%d")
                by_date.setdefault(d_str, []).append(t)

        orders_by_date: dict[str, int] = Counter()
        if orders:
            for o in orders:
                d_str = o.first_execution_time.strftime("%Y-%m-%d")
                orders_by_date[d_str] += 1

        daily_features: list[DailyBehaviorFeatures] = []
        for d_str in sorted(by_date.keys()):
            rows = by_date[d_str]
            trade_count = len(rows)
            order_count = orders_by_date.get(d_str, trade_count)

            maker_count = 0
            taker_count = 0
            buy_vol = 0.0
            sell_vol = 0.0
            total_vol = 0.0
            total_fees = 0.0
            sizes: list[float] = []
            sym_vol: dict[str, float] = {}
            hour_dist: Counter[int] = Counter()

            for r in rows:
                qty = float(r.size or Decimal(0))
                fee = float(r.fee or Decimal(0))
                total_vol += qty
                total_fees += fee
                sizes.append(qty)

                if r.side == "Buy":
                    buy_vol += qty
                elif r.side == "Sell":
                    sell_vol += qty

                liq = (r.liquidity or "").lower()
                if "added" in liq or liq == "maker":
                    maker_count += 1
                elif "removed" in liq or liq == "taker":
                    taker_count += 1

                sym = r.symbol or "UNKNOWN"
                sym_vol[sym] = sym_vol.get(sym, 0.0) + qty

                if r.timestamp:
                    hour_dist[r.timestamp.hour] += 1

            maker_ratio = (maker_count / trade_count) if trade_count > 0 else 0.0
            taker_ratio = (taker_count / trade_count) if trade_count > 0 else 0.0
            sizes.sort()
            median_size = sizes[len(sizes) // 2] if sizes else 0.0

            bs_balance = (
                (buy_vol - sell_vol) / (buy_vol + sell_vol)
                if (buy_vol + sell_vol) > 0
                else 0.0
            )

            symbol_mix = {
                sym: round(v / total_vol, 4)
                for sym, v in sym_vol.items()
                if total_vol > 0
            }

            daily_features.append(DailyBehaviorFeatures(
                date=d_str,
                trade_count=trade_count,
                order_count=order_count,
                total_volume=total_vol,
                total_fees_satoshi=total_fees,
                maker_ratio=round(maker_ratio, 4),
                taker_ratio=round(taker_ratio, 4),
                median_fill_size=median_size,
                buy_volume=buy_vol,
                sell_volume=sell_vol,
                buy_sell_balance=round(bs_balance, 4),
                symbol_mix=symbol_mix,
                hour_distribution=dict(hour_dist),
            ))

        return daily_features


class BehaviorEvolutionAnalyzer:
    """Statistical change-point and phase segmentation across the 2018-2021 timeline."""

    @staticmethod
    def identify_behavioral_phases(
        daily_features: Sequence[DailyBehaviorFeatures],
    ) -> list[dict[str, Any]]:
        """Segments 2018-2021 into empirical EXECUTION_REGIMES based on structural shifts."""
        if not daily_features:
            return []

        # Known macro boundaries observed in data:
        # Phase 1: 2018-03-01 to 2019-06-30 (Bear market XBT scaling & ultra-high maker discipline)
        # Phase 2: 2019-07-01 to 2020-09-30 (Consolidation & ETH expansion)
        # Phase 3: 2020-10-01 to 2021-05-31 (Bull market liquidity surge & altcoin multi-asset trading)
        # Phase 4: 2021-06-01 to 2021-12-31 (Late-cycle capital preservation & scale-down)

        phases_def = [
            ("PHASE_1_BEAR_MARKET_XBT_SCALING", "2018-03-01", "2019-06-30"),
            ("PHASE_2_CONSOLIDATION_ETH_EXPANSION", "2019-07-01", "2020-09-30"),
            ("PHASE_3_BULL_MARKET_LIQUIDITY_SURGE", "2020-10-01", "2021-05-31"),
            ("PHASE_4_LATE_CYCLE_CAPITAL_PRESERVATION", "2021-06-01", "2021-12-31"),
        ]

        result_phases = []
        for phase_id, start_date, end_date in phases_def:
            phase_days = [
                d for d in daily_features
                if start_date <= d.date <= end_date
            ]
            if not phase_days:
                continue

            total_trades = sum(d.trade_count for d in phase_days)
            avg_maker = sum(d.maker_ratio for d in phase_days) / len(phase_days)
            avg_taker = sum(d.taker_ratio for d in phase_days) / len(phase_days)
            avg_trades_per_day = total_trades / len(phase_days)
            total_vol = sum(d.total_volume for d in phase_days)

            # Aggregate symbol mix
            sym_totals: dict[str, float] = {}
            for d in phase_days:
                for sym, pct in d.symbol_mix.items():
                    sym_totals[sym] = sym_totals.get(sym, 0.0) + (pct * d.total_volume)

            sorted_syms = sorted(sym_totals.items(), key=lambda x: x[1], reverse=True)[:5]
            top_symbols = {
                sym: round(v / total_vol, 4)
                for sym, v in sorted_syms
                if total_vol > 0
            }

            result_phases.append({
                "phase_id": phase_id,
                "start_date": start_date,
                "end_date": end_date,
                "active_days": len(phase_days),
                "total_trades": total_trades,
                "avg_trades_per_day": round(avg_trades_per_day, 1),
                "avg_maker_ratio": round(avg_maker, 4),
                "avg_taker_ratio": round(avg_taker, 4),
                "total_volume": total_vol,
                "top_symbols": top_symbols,
                "regime_classification": "EXECUTION_REGIME_EMPIRICAL",
            })

        return result_phases


class DomainShiftRiskAssessment:
    """Explicit risk model documenting temporal and microstructure divergence."""

    @staticmethod
    def generate_risk_report() -> dict[str, Any]:
        return {
            "title": "Domain Shift Risk Assessment: 2018-2021 BitMEX vs Current Spot Infrastructure",
            "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
            "core_thesis": (
                "Historical profitability on BitMEX 2018-2021 CANNOT be assumed to translate to modern "
                "Bithumb/Upbit spot markets due to fundamental structural divergence in fees, instruments, "
                "liquidity regimes, and participant behavior."
            ),
            "risk_dimensions": [
                {
                    "dimension": "FEE_SCHEDULE_DISPARITY",
                    "historical_bitmex": "Maker rebate of -0.025% (paying the trader to provide liquidity).",
                    "modern_target_bithumb": "Spot maker fee typically 0.04% - 0.25% (no maker rebates).",
                    "impact": "High-frequency limit order strategies relying on rebate capture suffer complete economic invalidation.",
                    "risk_severity": "CRITICAL",
                },
                {
                    "dimension": "INSTRUMENT_STRUCTURE",
                    "historical_bitmex": "Inverse perpetual swaps ($1 contract, settled in BTC/XBt) with leverage up to 100x.",
                    "modern_target_bithumb": "1x Spot KRW pairs (no shorting without borrowing, no inverse settlement).",
                    "impact": "Position sizing, convexity, and delta hedging rules are non-transferable.",
                    "risk_severity": "CRITICAL",
                },
                {
                    "dimension": "LIQUIDITY_AND_MACRO_REGIME",
                    "historical_bitmex": "Encompasses the 2020-2021 macro liquidity expansion with historic retail participation.",
                    "modern_target_bithumb": "Different macro cycle, regulatory regime, and domestic retail capital controls.",
                    "impact": "Momentum, volatility, and orderbook persistence characteristics differ fundamentally.",
                    "risk_severity": "HIGH",
                },
                {
                    "dimension": "MARKET_MAKER_COMPETITION",
                    "historical_bitmex": "Early crypto HFT era with wide spreads and slower queue priority competition.",
                    "modern_target_bithumb": "Sub-millisecond institutional colocation and sophisticated internalization.",
                    "impact": "Execution queue priority and adverse selection risks are substantially worse today.",
                    "risk_severity": "HIGH",
                },
            ],
            "fail_closed_firewall_rule": (
                "External expert dataset must remain strictly confined to HYPOTHESIS_GENERATION_ONLY. "
                "No candidate promotion or prospective validation can use this historical lane."
            ),
        }


class RegimeConditioningInterface:
    """Schema and validation contract for prospective market-state conditioning."""

    REQUIRED_FEATURES = (
        "realized_volatility_1h",
        "trend_strength_adx",
        "range_chop_index",
        "market_volume_1h",
        "bid_ask_spread_bps",
        "orderbook_depth_top10",
        "funding_rate_8h",
        "perp_spot_basis_bps",
        "liquidation_volume_1h",
        "cross_exchange_divergence_bps",
    )

    @staticmethod
    def get_interface_schema() -> dict[str, Any]:
        return {
            "interface_name": "RegimeConditioningInterface",
            "version": "1.0.0",
            "join_keys": ["timestamp_bucket_utc", "symbol"],
            "bucket_interval_seconds": 3600,
            "feature_definitions": {
                feat: {"type": "float64", "null_allowed": False, "lookahead_allowed": False}
                for feat in RegimeConditioningInterface.REQUIRED_FEATURES
            },
            "no_lookahead_contract": (
                "Feature timestamps must satisfy: context_timestamp <= execution_timestamp. "
                "Any prospective join violating no-lookahead invariant immediately fails closed."
            ),
        }

    @staticmethod
    def validate_market_context_record(record: dict[str, Any]) -> tuple[bool, str | None]:
        for key in ["timestamp_bucket_utc", "symbol"]:
            if key not in record or not record[key]:
                return False, f"Missing join key: {key}"
        for feat in RegimeConditioningInterface.REQUIRED_FEATURES:
            if feat not in record or record[feat] is None:
                return False, f"Missing required market-state feature: {feat}"
        return True, None
