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
