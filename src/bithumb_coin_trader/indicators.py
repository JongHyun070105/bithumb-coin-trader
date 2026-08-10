from __future__ import annotations

from math import log, sqrt
from statistics import pstdev
from typing import Sequence


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
