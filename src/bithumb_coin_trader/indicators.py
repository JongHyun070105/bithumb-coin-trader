from __future__ import annotations

from math import isfinite, log, sqrt
from statistics import pstdev
from typing import Sequence


def _validate_period(period: int, name: str = "period") -> None:
    if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
        raise ValueError(f"{name} must be a positive integer")


def _validated_values(values: Sequence[float]) -> list[float]:
    normalized = [float(value) for value in values]
    if not all(isfinite(value) for value in normalized):
        raise ValueError("indicator values must be finite")
    return normalized


def simple_moving_average(values: Sequence[float], period: int) -> list[float | None]:
    """Return a trailing simple moving average with ``None`` warmup values."""

    _validate_period(period)
    normalized = _validated_values(values)
    result: list[float | None] = [None] * len(normalized)
    running = 0.0
    for index, value in enumerate(normalized):
        running += value
        if index >= period:
            running -= normalized[index - period]
        if index >= period - 1:
            result[index] = running / period
    return result


sma = simple_moving_average


def wilder_rsi(values: Sequence[float], period: int = 14) -> list[float | None]:
    """Return Wilder's RSI, seeded from the first ``period`` price changes."""

    _validate_period(period)
    normalized = _validated_values(values)
    result: list[float | None] = [None] * len(normalized)
    if len(normalized) <= period:
        return result
    changes = [normalized[index] - normalized[index - 1] for index in range(1, len(normalized))]
    average_gain = sum(max(change, 0.0) for change in changes[:period]) / period
    average_loss = sum(max(-change, 0.0) for change in changes[:period]) / period

    def value(gain: float, loss: float) -> float:
        if loss == 0.0:
            return 50.0 if gain == 0.0 else 100.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    result[period] = value(average_gain, average_loss)
    for index in range(period + 1, len(normalized)):
        change = changes[index - 1]
        average_gain = (average_gain * (period - 1) + max(change, 0.0)) / period
        average_loss = (average_loss * (period - 1) + max(-change, 0.0)) / period
        result[index] = value(average_gain, average_loss)
    return result


rsi = wilder_rsi


def bollinger_bands(
    values: Sequence[float], period: int = 20, standard_deviations: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Return ``(middle, upper, lower)`` population-standard-deviation bands."""

    _validate_period(period)
    if not isfinite(standard_deviations) or standard_deviations < 0:
        raise ValueError("standard_deviations must be a non-negative finite number")
    normalized = _validated_values(values)
    middle = simple_moving_average(normalized, period)
    upper: list[float | None] = [None] * len(normalized)
    lower: list[float | None] = [None] * len(normalized)
    for index in range(period - 1, len(normalized)):
        center = middle[index]
        assert center is not None
        deviation = pstdev(normalized[index - period + 1 : index + 1])
        upper[index] = center + standard_deviations * deviation
        lower[index] = center - standard_deviations * deviation
    return middle, upper, lower


def bollinger_bandwidth(
    values: Sequence[float], period: int = 20, standard_deviations: float = 2.0
) -> list[float | None]:
    """Return Bollinger bandwidth ``(upper - lower) / middle``."""

    middle, upper, lower = bollinger_bands(values, period, standard_deviations)
    result: list[float | None] = [None] * len(middle)
    for index, center in enumerate(middle):
        if center is None:
            continue
        if center == 0.0:
            raise ValueError("Bollinger bandwidth is undefined for a zero middle band")
        assert upper[index] is not None and lower[index] is not None
        result[index] = (upper[index] - lower[index]) / center
    return result


def rolling_percentile(
    values: Sequence[float], period: int, percentile: float
) -> list[float | None]:
    """Return a linearly interpolated percentile for each trailing window."""

    _validate_period(period)
    if not isfinite(percentile) or not 0.0 <= percentile <= 100.0:
        raise ValueError("percentile must be between 0 and 100")
    normalized = _validated_values(values)
    result: list[float | None] = [None] * len(normalized)
    for index in range(period - 1, len(normalized)):
        window = sorted(normalized[index - period + 1 : index + 1])
        position = (len(window) - 1) * percentile / 100.0
        lower_index = int(position)
        upper_index = min(lower_index + 1, len(window) - 1)
        fraction = position - lower_index
        result[index] = window[lower_index] + fraction * (window[upper_index] - window[lower_index])
    return result


def rolling_percentile_rank(values: Sequence[float], period: int) -> list[float | None]:
    """Return the trailing percentile rank of the current value on [0, 100]."""

    _validate_period(period)
    normalized = _validated_values(values)
    result: list[float | None] = [None] * len(normalized)
    for index in range(period - 1, len(normalized)):
        current = normalized[index]
        window = normalized[index - period + 1 : index + 1]
        less = sum(value < current for value in window)
        equal = sum(value == current for value in window)
        result[index] = 100.0 * (less + 0.5 * equal) / period
    return result


def ema(values: Sequence[float], period: int) -> list[float | None]:
    if period <= 1:
        raise ValueError("EMA period must be greater than one")
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    seed = sum(values[:period]) / period
    result[period - 1] = seed
    alpha = 2.0 / (period + 1)
    current = seed
    for index in range(period, len(values)):
        current = alpha * values[index] + (1 - alpha) * current
        result[index] = current
    return result


def true_range(
    high: Sequence[float], low: Sequence[float], close: Sequence[float]
) -> list[float | None]:
    """Return True Range with the first value reserved as warmup.

    This follows TA-Lib's ``TRANGE`` convention: the first bar has no previous
    close, so its result is ``None`` rather than simply ``high - low``.
    """

    highs, lows, closes = _validated_ohlc(high, low, close)
    result: list[float | None] = [None] * len(closes)
    for index in range(1, len(closes)):
        result[index] = max(
            highs[index] - lows[index],
            abs(highs[index] - closes[index - 1]),
            abs(lows[index] - closes[index - 1]),
        )
    return result


def average_true_range(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    period: int = 14,
) -> list[float | None]:
    """Return Wilder's ATR, seeded from the first ``period`` True Ranges.

    The first defined value is at index ``period``, matching TA-Lib's ATR
    lookback. Subsequent values use Wilder smoothing.
    """

    _validate_period(period)
    ranges = true_range(high, low, close)
    result: list[float | None] = [None] * len(ranges)
    if len(ranges) <= period:
        return result

    seed_ranges = ranges[1 : period + 1]
    assert all(value is not None for value in seed_ranges)
    current = sum(value for value in seed_ranges if value is not None) / period
    result[period] = current
    for index in range(period + 1, len(ranges)):
        current_range = ranges[index]
        assert current_range is not None
        current = (current * (period - 1) + current_range) / period
        result[index] = current
    return result


def directional_indicators(
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    period: int = 14,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Return ``(+DI, -DI, ADX)`` using Wilder smoothing.

    ``+DI`` and ``-DI`` begin at index ``period``. ADX begins at index
    ``2 * period - 1`` after a full seed window of Directional Index values.
    Zero-range windows are represented by zero rather than division errors.
    """

    _validate_period(period)
    highs, lows, closes = _validated_ohlc(high, low, close)
    length = len(closes)
    positive_di: list[float | None] = [None] * length
    negative_di: list[float | None] = [None] * length
    adx: list[float | None] = [None] * length
    if length <= period:
        return positive_di, negative_di, adx

    ranges = true_range(highs, lows, closes)
    positive_dm = [0.0] * length
    negative_dm = [0.0] * length
    for index in range(1, length):
        upward = highs[index] - highs[index - 1]
        downward = lows[index - 1] - lows[index]
        if upward > downward and upward > 0.0:
            positive_dm[index] = upward
        elif downward > upward and downward > 0.0:
            negative_dm[index] = downward

    initial_ranges = ranges[1 : period + 1]
    assert all(value is not None for value in initial_ranges)
    smoothed_range = sum(value for value in initial_ranges if value is not None)
    smoothed_positive_dm = sum(positive_dm[1 : period + 1])
    smoothed_negative_dm = sum(negative_dm[1 : period + 1])
    dx: list[float | None] = [None] * length

    def set_directional_values(index: int) -> None:
        if smoothed_range == 0.0:
            positive = negative = 0.0
        else:
            positive = 100.0 * smoothed_positive_dm / smoothed_range
            negative = 100.0 * smoothed_negative_dm / smoothed_range
        positive_di[index] = positive
        negative_di[index] = negative
        directional_sum = positive + negative
        dx[index] = (
            0.0
            if directional_sum == 0.0
            else 100.0 * abs(positive - negative) / directional_sum
        )

    set_directional_values(period)
    for index in range(period + 1, length):
        current_range = ranges[index]
        assert current_range is not None
        smoothed_range = smoothed_range - smoothed_range / period + current_range
        smoothed_positive_dm = (
            smoothed_positive_dm
            - smoothed_positive_dm / period
            + positive_dm[index]
        )
        smoothed_negative_dm = (
            smoothed_negative_dm
            - smoothed_negative_dm / period
            + negative_dm[index]
        )
        set_directional_values(index)

    first_adx_index = 2 * period - 1
    if length <= first_adx_index:
        return positive_di, negative_di, adx
    seed_dx = dx[period : first_adx_index + 1]
    assert all(value is not None for value in seed_dx)
    current_adx = sum(value for value in seed_dx if value is not None) / period
    adx[first_adx_index] = current_adx
    for index in range(first_adx_index + 1, length):
        current_dx = dx[index]
        assert current_dx is not None
        current_adx = (current_adx * (period - 1) + current_dx) / period
        adx[index] = current_adx
    return positive_di, negative_di, adx


def macd(
    values: Sequence[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Return ``(MACD, signal, histogram)`` using SMA-seeded EMAs.

    Like TA-Lib's ``MACD``, the fast EMA seed is aligned to the slow EMA's
    first output and all three returned lines share a lookback of
    ``slow_period + signal_period - 2``. This avoids exposing partially warmed
    MACD values before the signal EMA can be seeded.
    """

    _validate_period(fast_period, "fast_period")
    _validate_period(slow_period, "slow_period")
    _validate_period(signal_period, "signal_period")
    if fast_period >= slow_period:
        raise ValueError("fast_period must be less than slow_period")
    normalized = _validated_values(values)
    length = len(normalized)
    macd_line: list[float | None] = [None] * length
    signal_line: list[float | None] = [None] * length
    histogram: list[float | None] = [None] * length
    if length < slow_period:
        return macd_line, signal_line, histogram

    fast = _ema_with_first_output(normalized, fast_period, slow_period - 1)
    slow = _ema_from_start(normalized, slow_period)
    raw_macd: list[float | None] = [None] * length
    for index in range(slow_period - 1, length):
        assert fast[index] is not None and slow[index] is not None
        raw_macd[index] = fast[index] - slow[index]

    first_index = slow_period + signal_period - 2
    if length <= first_index:
        return macd_line, signal_line, histogram
    signal_seed = raw_macd[slow_period - 1 : first_index + 1]
    assert all(value is not None for value in signal_seed)
    current_signal = sum(value for value in signal_seed if value is not None) / signal_period
    signal_alpha = 2.0 / (signal_period + 1)
    for index in range(first_index, length):
        current_macd = raw_macd[index]
        assert current_macd is not None
        if index > first_index:
            current_signal = (
                signal_alpha * current_macd + (1.0 - signal_alpha) * current_signal
            )
        macd_line[index] = current_macd
        signal_line[index] = current_signal
        histogram[index] = current_macd - current_signal
    return macd_line, signal_line, histogram


def percentage_volume_oscillator(
    volume: Sequence[float], fast_period: int = 12, slow_period: int = 26
) -> list[float | None]:
    """Return PVO as ``100 * (fast EMA - slow EMA) / slow EMA``.

    The first defined value is at ``slow_period - 1``. A zero slow EMA makes
    the oscillator undefined and raises ``ValueError`` instead of emitting an
    infinity or silently substituting zero.
    """

    _validate_period(fast_period, "fast_period")
    _validate_period(slow_period, "slow_period")
    if fast_period >= slow_period:
        raise ValueError("fast_period must be less than slow_period")
    normalized = _validated_values(volume)
    if any(value < 0.0 for value in normalized):
        raise ValueError("volume values must be non-negative")
    result: list[float | None] = [None] * len(normalized)
    if len(normalized) < slow_period:
        return result
    fast = _ema_from_start(normalized, fast_period)
    slow = _ema_from_start(normalized, slow_period)
    for index in range(slow_period - 1, len(normalized)):
        fast_value = fast[index]
        slow_value = slow[index]
        assert fast_value is not None and slow_value is not None
        if slow_value == 0.0:
            raise ValueError("percentage volume oscillator is undefined for zero volume EMA")
        result[index] = 100.0 * (fast_value - slow_value) / slow_value
    return result


def _validated_ohlc(
    high: Sequence[float], low: Sequence[float], close: Sequence[float]
) -> tuple[list[float], list[float], list[float]]:
    highs = _validated_values(high)
    lows = _validated_values(low)
    closes = _validated_values(close)
    if not (len(highs) == len(lows) == len(closes)):
        raise ValueError("high, low, and close must have equal lengths")
    if any(high_value < low_value for high_value, low_value in zip(highs, lows)):
        raise ValueError("high values must be greater than or equal to low values")
    return highs, lows, closes


def _ema_from_start(values: Sequence[float], period: int) -> list[float | None]:
    """Return an EMA seeded by the first full-window SMA."""

    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    current = sum(values[:period]) / period
    result[period - 1] = current
    alpha = 2.0 / (period + 1)
    for index in range(period, len(values)):
        current = alpha * values[index] + (1.0 - alpha) * current
        result[index] = current
    return result


def _ema_with_first_output(
    values: Sequence[float], period: int, first_output_index: int
) -> list[float | None]:
    """Return an EMA whose SMA seed ends at ``first_output_index``."""

    result: list[float | None] = [None] * len(values)
    if len(values) <= first_output_index:
        return result
    seed_start = first_output_index - period + 1
    current = sum(values[seed_start : first_output_index + 1]) / period
    result[first_output_index] = current
    alpha = 2.0 / (period + 1)
    for index in range(first_output_index + 1, len(values)):
        current = alpha * values[index] + (1.0 - alpha) * current
        result[index] = current
    return result


def rolling_volatility(values: Sequence[float], period: int) -> list[float | None]:
    if period <= 1:
        raise ValueError("volatility period must be greater than one")
    returns: list[float | None] = [None]
    returns.extend(log(values[index] / values[index - 1]) for index in range(1, len(values)))
    result: list[float | None] = [None] * len(values)
    for index in range(period, len(values)):
        window = [value for value in returns[index - period + 1 : index + 1] if value is not None]
        if len(window) == period:
            result[index] = pstdev(window) * sqrt(365)
    return result
