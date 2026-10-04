"""Deterministic, no-lookahead baseline research harness (tooling only).

Offline simulation machinery: chronological train/validation/holdout splits
with an embargo, a single-use holdout vault, a bar backtester in which the
position for period t+1 may only depend on bars <= t, explicit fee/slippage
assumptions, and standard metrics with cost sensitivity.

Nothing here proves alpha. Benchmarks are interface examples. No exchange,
network, private-API, paper, or live access.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class Bar:
    ts_ms: int
    close: float


@dataclass(frozen=True, slots=True)
class CostModel:
    fee_bps: float = 4.0
    slippage_bps: float = 2.0

    def rate(self, multiplier: float = 1.0) -> float:
        return (self.fee_bps + self.slippage_bps) * multiplier / 10_000.0


class Strategy(Protocol):
    def target_position(self, history: tuple[Bar, ...]) -> float:
        """Position in [-1, 1] for the NEXT period, using only ``history`` (bars <= now)."""


def validate_bars(bars: Sequence[Bar]) -> None:
    if len(bars) < 3:
        raise ValueError("need at least 3 bars")
    for prev, cur in zip(bars, bars[1:]):
        if cur.ts_ms <= prev.ts_ms:
            raise ValueError("bar timestamps must be strictly increasing")
    if any(not math.isfinite(b.close) or b.close <= 0 for b in bars):
        raise ValueError("bar closes must be finite and positive")


# ---------------------------------------------------------------- splits

@dataclass(frozen=True, slots=True)
class SplitIndices:
    train: tuple[int, int]
    validation: tuple[int, int]
    holdout: tuple[int, int]
    embargo_bars: int

    def ranges(self) -> list[tuple[str, tuple[int, int]]]:
        return [("train", self.train), ("validation", self.validation), ("holdout", self.holdout)]


def chronological_split(n: int, *, train_frac: float, validation_frac: float, embargo_bars: int) -> SplitIndices:
    """Half-open [start, end) index ranges. Embargo bars are dropped between segments
    so labels/features that look ``embargo_bars`` ahead cannot straddle a boundary."""
    if embargo_bars < 0 or not (0 < train_frac and 0 < validation_frac and train_frac + validation_frac < 1):
        raise ValueError("invalid split parameters")
    usable = n - 2 * embargo_bars
    if usable < 3:
        raise ValueError("series too short for requested embargo")
    tr = int(usable * train_frac)
    va = int(usable * validation_frac)
    ho = usable - tr - va
    if min(tr, va, ho) < 1:
        raise ValueError("a split segment would be empty")
    t_end = tr
    v_start = t_end + embargo_bars
    v_end = v_start + va
    h_start = v_end + embargo_bars
    return SplitIndices((0, t_end), (v_start, v_end), (h_start, h_start + ho), embargo_bars)


class HoldoutAccessError(RuntimeError):
    pass


class HoldoutVault:
    """Single-use holdout access bound to a frozen-candidate fingerprint."""

    def __init__(self, holdout_bars: Sequence[Bar], expected_freeze_sha256: str) -> None:
        self._bars = tuple(holdout_bars)
        self._expected = expected_freeze_sha256
        self.access_log: list[str] = []
        self._opened = False

    def open(self, freeze_sha256: str) -> tuple[Bar, ...]:
        if self._opened:
            self.access_log.append("DENIED_REUSE")
            raise HoldoutAccessError("holdout already consumed; no re-evaluation after tuning")
        if freeze_sha256 != self._expected:
            self.access_log.append("DENIED_FINGERPRINT")
            raise HoldoutAccessError("candidate fingerprint does not match the frozen candidate")
        self._opened = True
        self.access_log.append("OPENED")
        return self._bars

    def __repr__(self) -> str:
        return f"HoldoutVault(opened={self._opened}, bars=<sealed>)"


def candidate_fingerprint(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


# -------------------------------------------------------------- backtest

@dataclass(frozen=True, slots=True)
class BacktestResult:
    positions: tuple[float, ...]
    gross_returns: tuple[float, ...]
    net_returns: tuple[float, ...]
    turnover: tuple[float, ...]


def run_backtest(bars: Sequence[Bar], strategy: Strategy, costs: CostModel, *, cost_multiplier: float = 1.0) -> BacktestResult:
    """Decision at bar t (history = bars[:t+1]) earns the return t -> t+1. Costs charged on |dPosition|."""
    validate_bars(bars)
    rate = costs.rate(cost_multiplier)
    pos_prev = 0.0
    positions, gross, net, turn = [], [], [], []
    for t in range(len(bars) - 1):
        pos = float(strategy.target_position(tuple(bars[: t + 1])))
        if not math.isfinite(pos) or abs(pos) > 1.0:
            raise ValueError("position must be finite within [-1, 1]")
        r = bars[t + 1].close / bars[t].close - 1.0
        dpos = abs(pos - pos_prev)
        g = pos * r
        positions.append(pos)
        gross.append(g)
        net.append(g - dpos * rate)
        turn.append(dpos)
        pos_prev = pos
    return BacktestResult(tuple(positions), tuple(gross), tuple(net), tuple(turn))


def _perturbations(bars: Sequence[Bar], t: int, perturb: float) -> list[list[Bar]]:
    def shifted(f: Callable[[int, float], float]) -> list[Bar]:
        return [b if i <= t else Bar(b.ts_ms, f(i, b.close)) for i, b in enumerate(bars)]
    return [
        shifted(lambda i, c: c * (1.0 + perturb * (1 if i % 2 else -0.5))),
        shifted(lambda i, c: c * 3.0),
        shifted(lambda i, c: c * 0.2),
    ]


def assert_no_lookahead(
    strategy_factory: Callable[[Sequence[Bar]], Strategy], bars: Sequence[Bar], *, cut_points: Sequence[int], perturb: float = 0.37,
) -> None:
    """Leakage guard. The factory is handed the FULL series (where fit/normalisation leaks arise);
    decisions up to and including cut t must be identical when every later bar is altered
    (noise, 3x level shift, 0.2x level shift)."""
    validate_bars(bars)
    base = run_backtest(bars, strategy_factory(bars), CostModel(0, 0)).positions
    for t in cut_points:
        if not 0 <= t < len(bars) - 1:
            raise ValueError("cut point out of range")
        for altered in _perturbations(bars, t, perturb):
            moved = run_backtest(altered, strategy_factory(altered), CostModel(0, 0)).positions
            if base[: t + 1] != moved[: t + 1]:
                raise AssertionError(f"lookahead detected at cut {t}")


# --------------------------------------------------------------- metrics

@dataclass(frozen=True, slots=True)
class Metrics:
    periods: int
    total_return: float
    cagr: float
    max_drawdown: float
    sharpe: float
    turnover_total: float
    turnover_per_period: float
    hit_rate: float | None
    exposure: float
    trade_count: int


def compute_metrics(result: BacktestResult, *, periods_per_year: float) -> Metrics:
    n = len(result.net_returns)
    if n == 0 or periods_per_year <= 0:
        raise ValueError("empty result or invalid periods_per_year")
    equity, peak, mdd = 1.0, 1.0, 0.0
    for r in result.net_returns:
        equity *= 1.0 + r
        if equity <= 0:
            equity = 0.0
            mdd = 1.0
            break
        peak = max(peak, equity)
        mdd = max(mdd, 1.0 - equity / peak)
    total = equity - 1.0
    years = n / periods_per_year
    cagr = -1.0 if equity <= 0 else equity ** (1.0 / years) - 1.0
    mean = sum(result.net_returns) / n
    var = sum((r - mean) ** 2 for r in result.net_returns) / (n - 1) if n > 1 else 0.0
    sd = math.sqrt(var)
    sharpe = mean / sd * math.sqrt(periods_per_year) if sd > 0 else 0.0
    exposed = [r for r, p in zip(result.net_returns, result.positions) if p != 0.0]
    return Metrics(
        periods=n, total_return=total, cagr=cagr, max_drawdown=mdd, sharpe=sharpe,
        turnover_total=sum(result.turnover), turnover_per_period=sum(result.turnover) / n,
        hit_rate=(sum(1 for r in exposed if r > 0) / len(exposed)) if exposed else None,
        exposure=sum(abs(p) for p in result.positions) / n,
        trade_count=sum(1 for d in result.turnover if d > 0),
    )


def cost_sensitivity(
    bars: Sequence[Bar], strategy_factory: Callable[[], Strategy], costs: CostModel,
    *, multipliers: Sequence[float] = (0.0, 1.0, 2.0, 4.0), periods_per_year: float,
) -> dict[float, Metrics]:
    return {m: compute_metrics(run_backtest(bars, strategy_factory(), costs, cost_multiplier=m), periods_per_year=periods_per_year) for m in multipliers}


# ------------------------------------------------------------ benchmarks

class FlatStrategy:
    def target_position(self, history: tuple[Bar, ...]) -> float:
        return 0.0


class BuyAndHold:
    def target_position(self, history: tuple[Bar, ...]) -> float:
        return 1.0


@dataclass
class SmaCross:
    fast: int = 5
    slow: int = 20

    def target_position(self, history: tuple[Bar, ...]) -> float:
        if len(history) < self.slow:
            return 0.0
        fast = sum(b.close for b in history[-self.fast:]) / self.fast
        slow = sum(b.close for b in history[-self.slow:]) / self.slow
        return 1.0 if fast > slow else 0.0
