"""Market Microstructure Feature Extraction Engine for Strategy V9 / Microstructure Research.

Implements pre-registered feature families under strict causal contracts:
1. Order Flow Imbalance (OFI v1 naive, OFI v2 Cont et al. 2014)
2. Aggressive Trade Imbalance (ATI) with exchange semantics
3. Microprice & Queue Imbalance (MPQI) with level weighting
4. Backward as-of alignment and warmup contracts
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from statistics import mean
from typing import Any, Mapping, Sequence


FEATURE_NOT_READY = "FEATURE_NOT_READY"


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
    def best_bid_size(self) -> float:
        return self.bids[0][1] if self.bids else 0.0

    @property
    def best_ask_size(self) -> float:
        return self.asks[0][1] if self.asks else 0.0

    @property
    def mid_price(self) -> float:
        bb, ba = self.best_bid, self.best_ask
        return (bb + ba) / 2.0 if (bb > 0 and ba > 0) else 0.0

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid if (self.best_ask > 0 and self.best_bid > 0) else 0.0

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
    side: str  # Normalized to "BUY" (taker hit ask) or "SELL" (taker hit bid)
    exchange: str = "BITHUMB"
    raw_side: str = ""


@dataclass(frozen=True, slots=True)
class FeatureValue:
    feature_id: str
    version: str
    timestamp: datetime
    value: float | None
    status: str  # "VALID", "NOT_READY", "STALE", "INVALID"
    parameters: dict[str, Any]


def normalize_aggressor_side(exchange: str, raw_side: Any) -> str:
    """Normalizes exchange-specific aggressor/taker side to canonical BUY or SELL.
    
    Semantics:
    - BUY: buyer is the aggressor/taker (executed against resting ask).
    - SELL: seller is the aggressor/taker (executed against resting bid).
    """
    ex = exchange.upper()
    if ex == "BITHUMB":
        # Bithumb WebSocket trade: "bid" means buyer taker (BUY), "ask" means seller taker (SELL)
        s = str(raw_side).lower()
        if s in ("bid", "buy"):
            return "BUY"
        elif s in ("ask", "sell"):
            return "SELL"
    elif ex == "BINANCE":
        # Binance trade: is_buyer_maker == False -> BUY taker. is_buyer_maker == True -> SELL taker.
        if isinstance(raw_side, bool):
            return "SELL" if raw_side else "BUY"
        s = str(raw_side).lower()
        if s in ("false", "buy"):
            return "BUY"
        elif s in ("true", "sell"):
            return "SELL"
    elif ex == "UPBIT":
        # Upbit WebSocket trade: ask_bid == "BID" -> buyer taker, "ASK" -> seller taker
        s = str(raw_side).upper()
        if s in ("BID", "BUY"):
            return "BUY"
        elif s in ("ASK", "SELL"):
            return "SELL"
    
    # Generic fallback
    s = str(raw_side).upper()
    if s in ("BUY", "BID"):
        return "BUY"
    if s in ("SELL", "ASK"):
        return "SELL"
    raise ValueError(f"Unknown aggressor side {raw_side!r} for exchange {exchange}")


def compute_ofi_v1(
    prev_ob: OrderbookSnapshot,
    curr_ob: OrderbookSnapshot,
    depth: int = 5,
) -> float:
    """Order Flow Imbalance V1 (Naive sign indicator formulation).
    
    Formula from Preregistration V1:
    OFI = DeltaBidSize * I(Bid_t >= Bid_t-1) - DeltaAskSize * I(Ask_t <= Ask_t-1)
    """
    bid_t0 = prev_ob.best_bid
    bid_t1 = curr_ob.best_bid
    ask_t0 = prev_ob.best_ask
    ask_t1 = curr_ob.best_ask

    bid_sz0 = sum(s for _, s in prev_ob.bids[:depth])
    bid_sz1 = sum(s for _, s in curr_ob.bids[:depth])
    ask_sz0 = sum(s for _, s in prev_ob.asks[:depth])
    ask_sz1 = sum(s for _, s in curr_ob.asks[:depth])

    delta_bid = bid_sz1 - bid_sz0
    delta_ask = ask_sz1 - ask_sz0

    i_bid = 1.0 if bid_t1 >= bid_t0 else 0.0
    i_ask = 1.0 if ask_t1 <= ask_t0 else 0.0

    return (delta_bid * i_bid) - (delta_ask * i_ask)


def compute_ofi_v2(
    prev_ob: OrderbookSnapshot,
    curr_ob: OrderbookSnapshot,
) -> float:
    """Order Flow Imbalance V2 (Cont, Kukanov & Stoikov 2014 rigorous Level 1 formulation).
    
    Properly accounts for price-level jumps:
    - Bid:
        If P_b(t) > P_b(t-1): I_b = q_b(t)
        If P_b(t) == P_b(t-1): I_b = q_b(t) - q_b(t-1)
        If P_b(t) < P_b(t-1): I_b = -q_b(t-1)
    - Ask:
        If P_a(t) < P_a(t-1): I_a = -q_a(t)
        If P_a(t) == P_a(t-1): I_a = -(q_a(t) - q_a(t-1))
        If P_a(t) > P_a(t-1): I_a = q_a(t-1)
    OFI = I_b + I_a
    """
    p_b0, q_b0 = prev_ob.best_bid, prev_ob.best_bid_size
    p_b1, q_b1 = curr_ob.best_bid, curr_ob.best_bid_size
    p_a0, q_a0 = prev_ob.best_ask, prev_ob.best_ask_size
    p_a1, q_a1 = curr_ob.best_ask, curr_ob.best_ask_size

    # Bid side inflow
    if p_b1 > p_b0:
        inflow_b = q_b1
    elif p_b1 == p_b0:
        inflow_b = q_b1 - q_b0
    else:
        inflow_b = -q_b0

    # Ask side inflow
    if p_a1 < p_a0:
        inflow_a = -q_a1
    elif p_a1 == p_a0:
        inflow_a = -(q_a1 - q_a0)
    else:
        inflow_a = q_a0

    return inflow_b + inflow_a


def compute_ati(
    trades: Sequence[TradeTick],
    current_time: datetime,
    window_seconds: float,
    *,
    min_trades: int = 1,
) -> float | None:
    """Aggressive Trade Imbalance (ATI) over causal backward window [current_time - window_seconds, current_time].
    
    Returns None (FEATURE_NOT_READY) if fewer than min_trades exist in window.
    """
    c_ts = current_time.timestamp()
    w_start = c_ts - window_seconds

    # Filter trades in window: w_start <= t.timestamp <= c_ts
    window_trades = [
        t for t in trades
        if w_start <= t.timestamp.timestamp() <= c_ts
    ]

    if len(window_trades) < min_trades:
        return None

    buy_vol = sum(t.volume for t in window_trades if t.side == "BUY")
    sell_vol = sum(t.volume for t in window_trades if t.side == "SELL")
    tot_vol = buy_vol + sell_vol

    if tot_vol <= 0:
        return 0.0

    return (buy_vol - sell_vol) / tot_vol


def compute_mpqi(
    ob: OrderbookSnapshot,
    depth: int = 1,
    weights: Sequence[float] | None = None,
) -> tuple[float, float]:
    """Microprice and Queue Imbalance (MPQI) with explicit depth level weighting.
    
    Returns (microprice, queue_imbalance).
    Queue Imbalance is bounded in [-1, 1].
    """
    if not ob.bids or not ob.asks:
        return (ob.mid_price, 0.0)

    d = min(depth, len(ob.bids), len(ob.asks))
    w = list(weights[:d]) if weights else [1.0 / (i + 1) for i in range(d)]
    w_sum = sum(w)
    norm_w = [weight / w_sum for weight in w]

    weighted_bid_sz = sum(ob.bids[i][1] * norm_w[i] for i in range(d))
    weighted_ask_sz = sum(ob.asks[i][1] * norm_w[i] for i in range(d))
    tot_sz = weighted_bid_sz + weighted_ask_sz

    if tot_sz <= 0:
        return (ob.mid_price, 0.0)

    # Queue Imbalance
    qi = (weighted_bid_sz - weighted_ask_sz) / tot_sz

    # Microprice: (Bid * AskSize + Ask * BidSize) / (BidSize + AskSize)
    best_bid = ob.best_bid
    best_ask = ob.best_ask
    microprice = (best_bid * weighted_ask_sz + best_ask * weighted_bid_sz) / tot_sz

    return (microprice, qi)


@dataclass(frozen=True, slots=True)
class MicrostructureFeatures:
    market: str
    timestamp: datetime
    mid_price: float
    spread_bps: float
    obi_level_1: float
    obi_level_5: float
    microprice: float
    microprice_bias_bps: float
    trade_imbalance_30s: float
    volume_shock_ratio: float


class MicrostructureFeatureEngine:
    """Deterministic High-Precision Microstructure Feature Extractor."""

    @staticmethod
    def compute_obi(bids: Sequence[tuple[float, float]], asks: Sequence[tuple[float, float]], depth: int = 5) -> float:
        bid_vol = sum(size for _, size in bids[:depth])
        ask_vol = sum(size for _, size in asks[:depth])
        tot = bid_vol + ask_vol
        if tot <= 0:
            return 0.0
        return (bid_vol - ask_vol) / tot

    @staticmethod
    def compute_microprice(best_bid: float, bid_size_1: float, best_ask: float, ask_size_1: float) -> tuple[float, float]:
        tot = bid_size_1 + ask_size_1
        mid = (best_bid + best_ask) / 2.0
        if tot <= 0 or mid <= 0:
            return mid, 0.0
        micro = (best_bid * ask_size_1 + best_ask * bid_size_1) / tot
        bias_bps = ((micro - mid) / mid) * 10_000.0
        return micro, bias_bps

    @staticmethod
    def compute_trade_imbalance(recent_trades: Sequence[TradeTick]) -> float:
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

        obi_1 = self.compute_obi(bids, asks, depth=1)
        obi_5 = self.compute_obi(bids, asks, depth=5)

        bb = orderbook.best_bid
        ba = orderbook.best_ask
        bbs = orderbook.best_bid_size
        bas = orderbook.best_ask_size
        micro, micro_bias = self.compute_microprice(bb, bbs, ba, bas)

        trade_imb = self.compute_trade_imbalance(recent_trades)
        recent_vol = sum(t.volume for t in recent_trades)
        vol_shock = recent_vol / baseline_trade_volume if baseline_trade_volume > 0 else 1.0

        return MicrostructureFeatures(
            market=orderbook.market,
            timestamp=orderbook.timestamp,
            mid_price=mid,
            spread_bps=spread,
            obi_level_1=obi_1,
            obi_level_5=obi_5,
            microprice=micro,
            microprice_bias_bps=micro_bias,
            trade_imbalance_30s=trade_imb,
            volume_shock_ratio=vol_shock,
        )
