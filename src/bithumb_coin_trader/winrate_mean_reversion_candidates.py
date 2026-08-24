"""Selective, research-only mean-reversion candidates for KRW-BTC.

The strategies in this module consume completed 30-minute candles and emit
spot LONG/FLAT state.  A signal observed at a candle close is intentionally
left for the backtester to fill at the following candle open.  Missing source
candles split the input into independent segments so indicators, armed setups,
and positions cannot leak across a gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from math import isfinite
from typing import Callable, Sequence

from .indicators import average_true_range, bollinger_bands, wilder_rsi
from .models import Candle, Signal


SOURCE_DELTA = timedelta(minutes=30)
KST = timezone(timedelta(hours=9))
CandidateFactory = Callable[[], "SelectiveMeanReversionStrategy"]


@dataclass(frozen=True, slots=True)
class MeanReversionCandidateParameters:
    bollinger_period: int
    bollinger_deviations: float
    rsi_period: int
    arm_rsi: float
    reclaim_rsi: float
    atr_period: int
    minimum_atr_fraction: float
    maximum_atr_fraction: float
    four_hour_sma_period: int
    regime_floor: float
    take_profit_fraction: float
    stop_loss_fraction: float
    maximum_holding_bars: int
    maximum_armed_bars: int

    def __post_init__(self) -> None:
        periods = (
            self.bollinger_period,
            self.rsi_period,
            self.atr_period,
            self.four_hour_sma_period,
            self.maximum_holding_bars,
            self.maximum_armed_bars,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 1
            for value in periods
        ):
            raise ValueError("periods and state windows must be integers greater than one")
        numbers = (
            self.bollinger_deviations,
            self.arm_rsi,
            self.reclaim_rsi,
            self.minimum_atr_fraction,
            self.maximum_atr_fraction,
            self.regime_floor,
            self.take_profit_fraction,
            self.stop_loss_fraction,
        )
        if not all(isfinite(value) for value in numbers):
            raise ValueError("candidate parameters must be finite")
        if self.bollinger_deviations <= 0:
            raise ValueError("Bollinger deviations must be positive")
        if not 0 <= self.arm_rsi <= self.reclaim_rsi <= 100:
            raise ValueError("RSI thresholds must be ordered within zero and 100")
        if not 0 <= self.minimum_atr_fraction < self.maximum_atr_fraction < 1:
            raise ValueError("ATR fraction bounds must be ordered within zero and one")
        if not 0 < self.regime_floor <= 1.1:
            raise ValueError("regime floor must be positive and no greater than 1.1")
        if not 0 < self.take_profit_fraction < 1:
            raise ValueError("take-profit fraction must be between zero and one")
        if not 0 < self.stop_loss_fraction < 1:
            raise ValueError("stop-loss fraction must be between zero and one")


class SelectiveMeanReversionStrategy:
    """Arm on an oversold lower-band breach and enter only after a reclaim.

    The optional price exits are signal-state exits, not intrabar fill claims.
    Entry price is taken from the next candle open, matching the backtester's
    fill contract.  A subsequent completed candle may request an exit after its
    high/low touches a threshold; the actual exit remains the next open.
    """

    def __init__(self, *, name: str, parameters: MeanReversionCandidateParameters) -> None:
        if not name:
            raise ValueError("candidate name cannot be empty")
        self.name = name
        self.parameters = parameters

    def generate(self, candles: Sequence[Candle], **_: object) -> list[Signal]:
        _validate_candles(candles)
        signals = [Signal.FLAT] * len(candles)
        for start, end in _contiguous_segments(candles):
            signals[start:end] = self._generate_segment(candles[start:end])
        return signals

    def _generate_segment(self, candles: Sequence[Candle]) -> list[Signal]:
        if not candles:
            return []
        parameters = self.parameters
        closes = [candle.close for candle in candles]
        highs = [candle.high for candle in candles]
        lows = [candle.low for candle in candles]
        middle, _, lower = bollinger_bands(
            closes,
            parameters.bollinger_period,
            parameters.bollinger_deviations,
        )
        rsi = wilder_rsi(closes, parameters.rsi_period)
        atr = average_true_range(highs, lows, closes, parameters.atr_period)
        regime = _completed_four_hour_regime(
            candles,
            parameters.four_hour_sma_period,
            parameters.regime_floor,
        )

        signals = [Signal.FLAT] * len(candles)
        position = Signal.FLAT
        armed_at: int | None = None
        entry_pending = False
        entry_price: float | None = None
        held_bars = 0

        for index, candle in enumerate(candles):
            ready = None not in (lower[index], rsi[index], atr[index])
            if position is Signal.LONG:
                if entry_pending:
                    entry_price = candle.open
                    entry_pending = False
                assert entry_price is not None
                held_bars += 1
                stop_touched = candle.low <= entry_price * (
                    1.0 - parameters.stop_loss_fraction
                )
                target_touched = candle.high >= entry_price * (
                    1.0 + parameters.take_profit_fraction
                )
                middle_recovered = (
                    middle[index] is not None
                    and candle.close >= middle[index]
                    and rsi[index] is not None
                    and rsi[index] >= 50.0
                )
                if (
                    stop_touched
                    or target_touched
                    or middle_recovered
                    or held_bars >= parameters.maximum_holding_bars
                ):
                    position = Signal.FLAT
                    entry_price = None
                    held_bars = 0
                signals[index] = position
                continue

            if armed_at is not None and index - armed_at > parameters.maximum_armed_bars:
                armed_at = None
            if not ready:
                continue
            assert lower[index] is not None and rsi[index] is not None and atr[index] is not None
            atr_fraction = atr[index] / candle.close
            volatility_ok = (
                parameters.minimum_atr_fraction
                <= atr_fraction
                <= parameters.maximum_atr_fraction
            )
            if (
                volatility_ok
                and candle.close < lower[index]
                and rsi[index] <= parameters.arm_rsi
            ):
                armed_at = index
            elif (
                armed_at is not None
                and regime[index]
                and candle.close >= lower[index]
                and rsi[index] >= parameters.reclaim_rsi
                and (index == 0 or candle.close > candles[index - 1].close)
            ):
                position = Signal.LONG
                entry_pending = True
                held_bars = 0
                armed_at = None
            signals[index] = position
        return signals


def candidate_factories() -> dict[str, CandidateFactory]:
    """Return fixed candidate factories for the shared research harness."""

    configurations = {
        "mr_bb20_rsi32_reclaim_4h_flexible": MeanReversionCandidateParameters(
            20, 2.0, 14, 32.0, 36.0, 14, 0.002, 0.032, 30, 0.975, 0.012, 0.018, 18, 10
        ),
        "mr_bb20_rsi28_reclaim_4h_uptrend": MeanReversionCandidateParameters(
            20, 2.0, 14, 28.0, 33.0, 14, 0.002, 0.027, 40, 1.0, 0.012, 0.014, 16, 12
        ),
        "mr_bb30_extreme_reclaim_4h_uptrend": MeanReversionCandidateParameters(
            30, 2.5, 14, 24.0, 30.0, 14, 0.002, 0.030, 40, 1.0, 0.016, 0.014, 24, 16
        ),
        "mr_bb20_lowvol_reclaim_4h_flexible": MeanReversionCandidateParameters(
            20, 2.2, 10, 30.0, 35.0, 14, 0.0015, 0.018, 30, 0.985, 0.010, 0.012, 12, 10
        ),
        "mr_bb24_rsi35_fast_reclaim_4h_uptrend": MeanReversionCandidateParameters(
            24, 2.0, 14, 35.0, 39.0, 14, 0.002, 0.025, 24, 1.0, 0.010, 0.013, 10, 8
        ),
    }
    return {
        name: (lambda name=name, parameters=parameters: SelectiveMeanReversionStrategy(
            name=name,
            parameters=parameters,
        ))
        for name, parameters in configurations.items()
    }


def _validate_candles(candles: Sequence[Candle]) -> None:
    if any(
        candles[index].timestamp >= candles[index + 1].timestamp
        for index in range(len(candles) - 1)
    ):
        raise ValueError("candles must be strictly chronological")
    if len({candle.market for candle in candles}) > 1:
        raise ValueError("candidate input must contain exactly one market")


def _contiguous_segments(candles: Sequence[Candle]) -> list[tuple[int, int]]:
    if not candles:
        return []
    starts = [0]
    starts.extend(
        index
        for index in range(1, len(candles))
        if candles[index].timestamp - candles[index - 1].timestamp != SOURCE_DELTA
    )
    return [
        (start, starts[index + 1] if index + 1 < len(starts) else len(candles))
        for index, start in enumerate(starts)
    ]


def _completed_four_hour_regime(
    candles: Sequence[Candle], period: int, floor: float
) -> list[bool]:
    """Map the latest completed KST-aligned 4h close/SMA state to source bars."""

    result = [False] * len(candles)
    completed_closes: list[float] = []
    state = False
    index = 0
    while index < len(candles):
        kst = candles[index].timestamp.astimezone(KST)
        bucket_hour = (kst.hour // 4) * 4
        bucket_key = (kst.date(), bucket_hour)
        bucket: list[int] = []
        while index < len(candles):
            candidate_kst = candles[index].timestamp.astimezone(KST)
            if (candidate_kst.date(), (candidate_kst.hour // 4) * 4) != bucket_key:
                break
            bucket.append(index)
            index += 1
        expected_minutes = [(bucket_hour + offset // 2, (offset % 2) * 30) for offset in range(8)]
        actual_minutes = [
            (candles[item].timestamp.astimezone(KST).hour,
             candles[item].timestamp.astimezone(KST).minute)
            for item in bucket
        ]
        for item in bucket:
            result[item] = state
        if len(bucket) == 8 and actual_minutes == expected_minutes:
            completed_closes.append(candles[bucket[-1]].close)
            if len(completed_closes) >= period:
                average = sum(completed_closes[-period:]) / period
                state = completed_closes[-1] >= average * floor
            # The new regime becomes observable only at this completed 4h
            # close.  Earlier 30m bars in the bucket retain the prior state.
            result[bucket[-1]] = state
    return result
