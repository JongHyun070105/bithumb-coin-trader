"""Validator-gated, research-only Wave 5 strategy comparison.

Wave 5 is intentionally isolated from credentials, account state, LLM output,
order books, and order execution.  It consumes completed public 30-minute
OHLCV candles, emits spot LONG/FLAT signals, and relies on ``Backtester`` for
next-bar-open fills.  No result from this module is eligible for automatic
paper or live promotion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from hashlib import sha256
import json
from math import isfinite
from typing import Any, Callable, Mapping, Sequence

from .config import TradingSettings
from .models import Candle, Signal
from .research import (
    CandidateComparisonReport,
    ProjectResearchReport,
    ResearchError,
    run_chronological_research,
)
from .strategy import donchian_4h_55_20_strategy


KST = timezone(timedelta(hours=9))
SOURCE_MINUTES = 30
SOURCE_DELTA = timedelta(minutes=SOURCE_MINUTES)
WAVE5_CANDIDATE_NAMES = (
    "cash",
    "four_hour_trend_pullback",
    "cross_sectional_momentum",
    "four_hour_breakout",
)
WAVE5_RUNNABLE_BTC_CANDIDATES = (
    "cash",
    "four_hour_trend_pullback",
    "four_hour_breakout",
)

CandidateFactory = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class Wave5Config:
    """Chronological expanding-window geometry frozen for Wave 5."""

    historical_count: int = 40_000
    train_size: int = 16_000
    test_size: int = 3_000
    fold_count: int = 8
    minimum_positive_folds: int = 5
    minimum_closed_trades: int = 12
    maximum_drawdown: float = 0.15

    def __post_init__(self) -> None:
        integer_values = (
            self.historical_count,
            self.train_size,
            self.test_size,
            self.fold_count,
            self.minimum_positive_folds,
            self.minimum_closed_trades,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in integer_values
        ):
            raise ValueError("Wave 5 sizes and thresholds must be positive integers")
        if self.train_size + self.test_size * self.fold_count != self.historical_count:
            raise ValueError("Wave 5 folds must exactly cover the historical sample")
        if self.minimum_positive_folds > self.fold_count:
            raise ValueError("positive-fold threshold exceeds fold count")
        if not isfinite(self.maximum_drawdown) or not 0 < self.maximum_drawdown < 1:
            raise ValueError("maximum drawdown gate must be between zero and one")

    def boundaries(self) -> tuple[tuple[int, int, int, int], ...]:
        return tuple(
            (
                0,
                self.train_size + fold * self.test_size,
                self.train_size + fold * self.test_size,
                self.train_size + (fold + 1) * self.test_size,
            )
            for fold in range(self.fold_count)
        )


class CashStrategy:
    name = "cash"

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_source_candles(candles)
        return [Signal.FLAT] * len(candles)


@dataclass(frozen=True, slots=True)
class FourHourTrendPullbackStrategy:
    """Enter an established 4h uptrend after a close recaptures its fast EMA.

    Only complete KST-aligned 4h buckets are observed.  A gap resets the state
    to FLAT, preventing a stale signal from being carried across missing data.
    """

    fast_ema_period: int = 20
    slow_ema_period: int = 80
    maximum_holding_bars_4h: int = 24
    name: str = "four_hour_trend_pullback"

    def __post_init__(self) -> None:
        if (
            self.fast_ema_period,
            self.slow_ema_period,
            self.maximum_holding_bars_4h,
        ) != (20, 80, 24):
            raise ValueError("Wave 5 trend-pullback parameters are frozen")

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_source_candles(candles)
        signals = [Signal.FLAT] * len(candles)
        position = Signal.FLAT
        held = 0
        previous_close: float | None = None
        fast: float | None = None
        slow: float | None = None
        fast_alpha = 2.0 / (self.fast_ema_period + 1.0)
        slow_alpha = 2.0 / (self.slow_ema_period + 1.0)
        complete_count = 0
        previous_bucket_end: int | None = None

        for bucket in _complete_four_hour_buckets(candles):
            bucket_end, close = bucket
            if previous_bucket_end is not None and bucket_end != previous_bucket_end + 8:
                position = Signal.FLAT
                held = 0
                previous_close = None
                fast = None
                slow = None
                complete_count = 0
            previous_bucket_end = bucket_end
            complete_count += 1
            previous_fast_value = fast
            fast = close if fast is None else fast_alpha * close + (1.0 - fast_alpha) * fast
            slow = close if slow is None else slow_alpha * close + (1.0 - slow_alpha) * slow

            if position is Signal.LONG:
                held += 1
                if close < slow or held >= self.maximum_holding_bars_4h:
                    position = Signal.FLAT
            elif (
                complete_count >= self.slow_ema_period
                and previous_close is not None
                and previous_fast_value is not None
                and fast > slow
                and close > slow
                and previous_close <= previous_fast_value
                and close > fast
            ):
                position = Signal.LONG
                held = 0

            # The decision is written at the completed bucket close.  The
            # backtester consumes it at the following 30m open.
            signals[bucket_end] = position
            previous_close = close

        # Carry each completed-bucket decision only until the next complete
        # bucket. A source gap is already represented by a FLAT reset above.
        current = Signal.FLAT
        bucket_ends = {index for index, _ in _complete_four_hour_buckets(candles)}
        for index in range(len(signals)):
            if (
                index > 0
                and candles[index].timestamp - candles[index - 1].timestamp
                != SOURCE_DELTA
            ):
                current = Signal.FLAT
            if index in bucket_ends:
                current = signals[index]
            else:
                signals[index] = current
        return signals


def _breakout_factory() -> Any:
    return donchian_4h_55_20_strategy()


def wave5_candidate_factories() -> dict[str, CandidateFactory]:
    """Return only candidates executable with a single-market candle stream."""

    return {
        "cash": CashStrategy,
        "four_hour_trend_pullback": FourHourTrendPullbackStrategy,
        "four_hour_breakout": _breakout_factory,
    }


def wave5_candidate_manifest(config: Wave5Config | None = None) -> dict[str, Any]:
    selected = config or Wave5Config()
    return {
        "schema_version": 5,
        "status": "RESEARCH_ONLY",
        "candidate_set": [
            {
                "name": "cash",
                "family": "cash_control",
                "availability": "available",
                "parameters": {},
            },
            {
                "name": "four_hour_trend_pullback",
                "family": "time_series_trend_pullback",
                "availability": "available",
                "parameters": {
                    "fast_ema_4h": 20,
                    "slow_ema_4h": 80,
                    "maximum_holding_bars_4h": 24,
                },
            },
            {
                "name": "cross_sectional_momentum",
                "family": "cross_sectional_momentum",
                "availability": "requires_at_least_three_aligned_markets",
                "parameters": {
                    "formation_days": 28,
                    "rebalance": "daily",
                    "selection": "top_quartile_positive_momentum",
                },
            },
            {
                "name": "four_hour_breakout",
                "family": "donchian_breakout",
                "availability": "available",
                "parameters": {"entry_bars_4h": 55, "exit_bars_4h": 20},
            },
        ],
        "execution": {
            "market_type": "Bithumb KRW spot",
            "source_minutes": 30,
            "signal_observed_at": "completed_close",
            "execution_eligible_at": "next_30m_open",
            "allow_short": False,
            "allow_pyramiding": False,
            "llm_historical_signal": False,
            "orderbook_historical_signal": False,
            "gap_policy": "reset_or_skip_incomplete_4h_bucket",
        },
        "walk_forward": {
            "historical_count": selected.historical_count,
            "initial_train_count": selected.train_size,
            "test_count": selected.test_size,
            "fold_count": selected.fold_count,
            "expanding": True,
        },
        "costs": {
            "base": {"fee_rate_per_fill": 0.0025, "slippage_bps_per_fill": 5.0},
            "double_cost_stress": {
                "fee_rate_per_fill": 0.005,
                "slippage_bps_per_fill": 10.0,
            },
        },
        "selection_gates": {
            "base_return_gt_cash": 0.0,
            "double_cost_return_gt_cash": 0.0,
            "maximum_drawdown_lte": selected.maximum_drawdown,
            "positive_base_folds_gte": selected.minimum_positive_folds,
            "closed_trades_gte": selected.minimum_closed_trades,
        },
        "promotion": {
            "automatic_promotion": "forbidden",
            "paper_or_live_strategy_changed": False,
            "requires_new_forward_evidence": True,
        },
    }


def wave5_candidate_manifest_hash(manifest: Mapping[str, Any] | None = None) -> str:
    payload = manifest or wave5_candidate_manifest()
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()


def compare_wave5_btc_candidates(
    candles: Sequence[Candle],
    *,
    settings: TradingSettings,
    config: Wave5Config | None = None,
) -> CandidateComparisonReport:
    """Compare single-market candidates on identical expanding OOS folds."""

    selected = config or Wave5Config()
    sample = _historical_sample(candles, selected)
    reports = tuple(
        run_chronological_research(
            sample,
            train_size=selected.train_size,
            test_size=selected.test_size,
            settings=settings,
            allow_short=False,
            candidate_name=name,
            candidate_factory=factory,
            continuous_oos=True,
            expanding=True,
        )
        for name, factory in wave5_candidate_factories().items()
    )
    expected = selected.boundaries()
    for report in reports:
        actual = tuple(
            (fold.train_start, fold.train_end, fold.test_start, fold.test_end)
            for fold in report.folds
        )
        if actual != expected:
            raise ResearchError("Wave 5 fold geometry differs from the manifest")
    return CandidateComparisonReport(
        candidates=reports,
        candidate_count=len(reports),
        fold_boundaries=expected,
    )


def evaluate_candidate_gates(
    base: ProjectResearchReport,
    stress: ProjectResearchReport,
    config: Wave5Config | None = None,
) -> dict[str, Any]:
    selected = config or Wave5Config()
    closed_trades = sum(
        not trade.is_final_liquidation
        for fold in base.folds
        for trade in fold.result.trades
    )
    positive_folds = sum(fold.result.total_return > 0 for fold in base.folds)
    checks = {
        "base_return_gt_cash": base.compounded_return > 0.0,
        "double_cost_return_gt_cash": stress.compounded_return > 0.0,
        "maximum_drawdown_lte": base.maximum_drawdown <= selected.maximum_drawdown,
        "positive_base_folds_gte": positive_folds >= selected.minimum_positive_folds,
        "closed_trades_gte": closed_trades >= selected.minimum_closed_trades,
    }
    return {
        "checks": checks,
        "actual": {
            "base_return": base.compounded_return,
            "double_cost_return": stress.compounded_return,
            "maximum_drawdown": base.maximum_drawdown,
            "positive_base_folds": positive_folds,
            "closed_trades": closed_trades,
        },
        "passed": all(checks.values()),
    }


def select_research_candidate(
    base: CandidateComparisonReport,
    stress: CandidateComparisonReport,
    config: Wave5Config | None = None,
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Select a research label only; this can never alter paper/live policy."""

    stress_by_name = {report.candidate_name: report for report in stress.candidates}
    gates = {
        report.candidate_name: evaluate_candidate_gates(
            report, stress_by_name[report.candidate_name], config
        )
        for report in base.candidates
    }
    eligible = [
        report
        for report in base.candidates
        if report.candidate_name != "cash" and gates[report.candidate_name]["passed"]
    ]
    if not eligible:
        return "cash", gates
    selected = max(
        eligible,
        key=lambda report: (
            stress_by_name[report.candidate_name].compounded_return,
            report.compounded_return,
            report.candidate_name,
        ),
    )
    return selected.candidate_name, gates


def _historical_sample(
    candles: Sequence[Candle], config: Wave5Config
) -> tuple[Candle, ...]:
    if len(candles) < config.historical_count:
        raise ResearchError("Wave 5 requires the complete historical sample")
    sample = tuple(candles[-config.historical_count :])
    _validate_source_candles(sample)
    if len({candle.market for candle in sample}) != 1:
        raise ResearchError("Wave 5 BTC comparison accepts one market only")
    return sample


def _validate_source_candles(candles: Sequence[Candle]) -> None:
    if not candles:
        raise ValueError("Wave 5 requires candles")
    if any(
        candle.timestamp.second
        or candle.timestamp.microsecond
        or candle.timestamp.minute % SOURCE_MINUTES
        for candle in candles
    ):
        raise ValueError("Wave 5 requires aligned 30-minute candles")
    if any(
        candles[index].timestamp <= candles[index - 1].timestamp
        for index in range(1, len(candles))
    ):
        raise ValueError("Wave 5 candles must be strictly chronological")
    if len({candle.market for candle in candles}) != 1:
        raise ValueError("Wave 5 strategy input must contain exactly one market")


def _complete_four_hour_buckets(
    candles: Sequence[Candle],
) -> tuple[tuple[int, float], ...]:
    """Return ``(source close index, close)`` for complete aligned 4h buckets."""

    buckets: list[tuple[int, float]] = []
    index = 0
    while index + 8 <= len(candles):
        first = candles[index]
        local = first.timestamp.astimezone(KST)
        if local.minute or local.hour % 4:
            index += 1
            continue
        window = candles[index : index + 8]
        if all(
            candle.timestamp == first.timestamp + SOURCE_DELTA * offset
            for offset, candle in enumerate(window)
        ):
            buckets.append((index + 7, window[-1].close))
            index += 8
        else:
            index += 1
    return tuple(buckets)
