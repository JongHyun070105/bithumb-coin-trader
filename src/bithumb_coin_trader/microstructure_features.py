"""Market Microstructure Feature Extraction Engine for Strategy V9.

Extracts real-time and historical micro-alpha factors from L2 orderbook and tick trade streams:
1. Order Book Imbalance (OBI)
2. Aggressive Trade Imbalance (ATI)
3. Microprice & Queue Pressure
4. Spread Dynamics (Spread in bps, compression/expansion)
5. Cross-Asset / Cross-Market Lead-Lag Signals
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Any, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class OrderbookSnapshot:
    market: str
    timestamp: datetime
    bids: tuple[tuple[float, float], ...]  # ((price, size), ...) sorted high to low
    asks: tuple[tuple[float, float], ...]  # ((price, size), ...) sorted low to high

    @property
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else 0.0

    @property
    def mid_price(self) -> float:
        bb, ba = self.best_bid, self.best_ask
        return (bb + ba) / 2.0 if (bb > 0 and ba > 0) else 0.0

    @property
    def spread_bps(self) -> float:
        mid = self.mid_price
        if mid <= 0:
            return 0.0
        return ((self.best_ask - self.best_bid) / mid) * 10_000.0


@dataclass(frozen=True, slots=True)
class TradeTick:
    market: str
    timestamp: datetime
    price: float
    volume: float
    side: str  # "BUY" (aggressive buy / ask hit) or "SELL" (aggressive sell / bid hit)


@dataclass(frozen=True, slots=True)
class MicrostructureFeatures:
    market: str
    timestamp: datetime
    mid_price: float
    spread_bps: float
    obi_level_1: float  # Top 1 depth imbalance [-1, 1]
    obi_level_5: float  # Top 5 depth imbalance [-1, 1]
    microprice: float   # Quantity-weighted equilibrium price
    microprice_bias_bps: float  # (Microprice - Mid) / Mid in bps
    trade_imbalance_30s: float  # Net aggressive volume ratio [-1, 1]
    volume_shock_ratio: float   # Recent volume vs rolling baseline


class MicrostructureFeatureEngine:
    """Deterministic High-Precision Microstructure Feature Extractor."""

    @staticmethod
    def compute_obi(bids: Sequence[tuple[float, float]], asks: Sequence[tuple[float, float]], depth: int = 5) -> float:
        """Compute Order Book Imbalance across top `depth` levels."""
        bid_vol = sum(size for _, size in bids[:depth])
        ask_vol = sum(size for _, size in asks[:depth])
        tot = bid_vol + ask_vol
        if tot <= 0:
            return 0.0
        return (bid_vol - ask_vol) / tot

    @staticmethod
    def compute_microprice(best_bid: float, bid_size_1: float, best_ask: float, ask_size_1: float) -> tuple[float, float]:
        """Compute Microprice (queue-weighted fair value) and its spread bias in bps."""
        tot = bid_size_1 + ask_size_1
        mid = (best_bid + best_ask) / 2.0
        if tot <= 0 or mid <= 0:
            return mid, 0.0
        # Microprice = (Bid * AskSize + Ask * BidSize) / (BidSize + AskSize)
        # Note: Heavy BidSize shifts microprice towards Ask!
        micro = (best_bid * ask_size_1 + best_ask * bid_size_1) / tot
        bias_bps = ((micro - mid) / mid) * 10_000.0
        return micro, bias_bps

    @staticmethod
    def compute_trade_imbalance(recent_trades: Sequence[TradeTick]) -> float:
        """Compute Aggressive Trade Imbalance from recent tick stream."""
        if not recent_trades:
            return 0.0
        buy_vol = sum(t.volume for t in recent_trades if t.side.upper() in ("BUY", "BID", "ASK_HIT"))
        sell_vol = sum(t.volume for t in recent_trades if t.side.upper() in ("SELL", "ASK", "BID_HIT"))
        tot = buy_vol + sell_vol
        if tot <= 0:
            return 0.0
        return (buy_vol - sell_vol) / tot

    def extract_features(
        self,
        orderbook: OrderbookSnapshot,
        recent_trades: Sequence[TradeTick],
        baseline_trade_volume: float = 1.0,
    ) -> MicrostructureFeatures:
        bids = orderbook.bids
        asks = orderbook.asks
        mid = orderbook.mid_price
        spread = orderbook.spread_bps

        # 1. OBI
        obi_1 = self.compute_obi(bids, asks, depth=1)
        obi_5 = self.compute_obi(bids, asks, depth=5)

        # 2. Microprice
        bb = orderbook.best_bid
        ba = orderbook.best_ask
        bs1 = bids[0][1] if bids else 0.0
        as1 = asks[0][1] if asks else 0.0
        micro, micro_bias = self.compute_microprice(bb, bs1, ba, as1)

        # 3. Trade Imbalance
        ati = self.compute_trade_imbalance(recent_trades)

        # 4. Volume Shock
        recent_vol = sum(t.volume for t in recent_trades)
        shock_ratio = (recent_vol / baseline_trade_volume) if baseline_trade_volume > 0 else 1.0

        return MicrostructureFeatures(
            market=orderbook.market,
            timestamp=orderbook.timestamp,
            mid_price=mid,
            spread_bps=spread,
            obi_level_1=obi_1,
            obi_level_5=obi_5,
            microprice=micro,
            microprice_bias_bps=micro_bias,
            trade_imbalance_30s=ati,
            volume_shock_ratio=shock_ratio,
        )
