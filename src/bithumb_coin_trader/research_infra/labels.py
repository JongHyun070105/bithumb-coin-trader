"""Label Engine for Microstructure Research.

Constructs future-looking target variables for hypothesis testing.
Uses EXACT future observations (not forward-filled) with explicit
missing label tracking.

Label construction:
    For horizon H:
        features at t → label = mid(t+H) / mid(t) - 1

    The future price is selected as the first observation with
    ordering_timestamp >= t + H. If no such observation exists within
    a bounded tolerance, the label is MISSING (not imputed).

Horizons: 1s, 5s, 10s, 30s, 60s

WARNING: Labels MUST NOT be included in feature vectors.
Labels use future information by definition.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Sequence

from .canonical_events import CanonicalEvent


class LabelHorizon(int, Enum):
    S1 = 1
    S5 = 5
    S10 = 10
    S30 = 30
    S60 = 60


@dataclass(frozen=True, slots=True)
class LabelVector:
    """Future labels for a single observation point.

    None = insufficient future data (label is MISSING).
    Labels are NEVER included in feature construction.
    """

    timestamp_ns: int
    exchange: str
    market: str

    # Future mid returns
    mid_return_1s: float | None = None
    mid_return_5s: float | None = None
    mid_return_10s: float | None = None
    mid_return_30s: float | None = None
    mid_return_60s: float | None = None

    # Future direction (1 = up, 0 = down, None = missing)
    direction_1s: int | None = None
    direction_5s: int | None = None
    direction_10s: int | None = None
    direction_30s: int | None = None
    direction_60s: int | None = None

    # Future mid price (for execution simulation)
    future_mid_1s: float | None = None
    future_mid_5s: float | None = None
    future_mid_10s: float | None = None
    future_mid_30s: float | None = None
    future_mid_60s: float | None = None

    @property
    def has_any_label(self) -> bool:
        return any(v is not None for v in [
            self.mid_return_1s, self.mid_return_5s, self.mid_return_10s,
            self.mid_return_30s, self.mid_return_60s,
        ])


class LabelEngine:
    """Constructs labels from mid-price observations.

    Uses backward-looking mid history to compute forward returns.
    Only uses orderbook events (mid-price source) from the target exchange.
    """

    def __init__(
        self,
        tolerance_s: float = 2.0,
    ) -> None:
        self._mid_history: list[tuple[int, float]] = []
        self.tolerance_ns = int(tolerance_s * 1_000_000_000)

    def add_mid_observation(self, timestamp_ns: int, mid_price: float) -> None:
        """Add a mid-price observation to the history."""
        if mid_price > 0:
            self._mid_history.append((timestamp_ns, mid_price))

    def compute_label(self, timestamp_ns: int, exchange: str, market: str) -> LabelVector:
        """Compute labels for a given timestamp using available mid history.

        Uses the first observation with timestamp >= target time.
        Returns None for a horizon if no observation exists within tolerance.
        """
        horizons = [
            ("1s", 1, "mid_return_1s", "direction_1s", "future_mid_1s"),
            ("5s", 5, "mid_return_5s", "direction_5s", "future_mid_5s"),
            ("10s", 10, "mid_return_10s", "direction_10s", "future_mid_10s"),
            ("30s", 30, "mid_return_30s", "direction_30s", "future_mid_30s"),
            ("60s", 60, "mid_return_60s", "direction_60s", "future_mid_60s"),
        ]

        # Current mid
        current_mid = self._find_mid_at(timestamp_ns)
        if current_mid is None or current_mid <= 0:
            return LabelVector(timestamp_ns=timestamp_ns, exchange=exchange, market=market)

        kwargs: dict[str, float | int | None] = {}
        for _, horizon_s, ret_key, dir_key, mid_key in horizons:
            target_ns = timestamp_ns + horizon_s * 1_000_000_000
            future_mid = self._find_mid_at_or_after(target_ns)

            if future_mid is not None and future_mid > 0:
                ret = (future_mid - current_mid) / current_mid
                kwargs[ret_key] = ret
                kwargs[dir_key] = 1 if ret > 0 else 0
                kwargs[mid_key] = future_mid
            else:
                kwargs[ret_key] = None
                kwargs[dir_key] = None
                kwargs[mid_key] = None

        return LabelVector(
            timestamp_ns=timestamp_ns,
            exchange=exchange,
            market=market,
            mid_return_1s=kwargs.get("mid_return_1s"),  # type: ignore[arg-type]
            mid_return_5s=kwargs.get("mid_return_5s"),  # type: ignore[arg-type]
            mid_return_10s=kwargs.get("mid_return_10s"),  # type: ignore[arg-type]
            mid_return_30s=kwargs.get("mid_return_30s"),  # type: ignore[arg-type]
            mid_return_60s=kwargs.get("mid_return_60s"),  # type: ignore[arg-type]
            direction_1s=kwargs.get("direction_1s"),  # type: ignore[arg-type]
            direction_5s=kwargs.get("direction_5s"),  # type: ignore[arg-type]
            direction_10s=kwargs.get("direction_10s"),  # type: ignore[arg-type]
            direction_30s=kwargs.get("direction_30s"),  # type: ignore[arg-type]
            direction_60s=kwargs.get("direction_60s"),  # type: ignore[arg-type]
            future_mid_1s=kwargs.get("future_mid_1s"),  # type: ignore[arg-type]
            future_mid_5s=kwargs.get("future_mid_5s"),  # type: ignore[arg-type]
            future_mid_10s=kwargs.get("future_mid_10s"),  # type: ignore[arg-type]
            future_mid_30s=kwargs.get("future_mid_30s"),  # type: ignore[arg-type]
            future_mid_60s=kwargs.get("future_mid_60s"),  # type: ignore[arg-type]
        )

    def _find_mid_at(self, timestamp_ns: int) -> float | None:
        """Find the mid price at or just before timestamp_ns."""
        best = None
        for t, p in self._mid_history:
            if t <= timestamp_ns:
                best = p
            else:
                break
        return best

    def _find_mid_at_or_after(self, target_ns: int) -> float | None:
        """Find the first mid price at or after target_ns within tolerance."""
        for t, p in self._mid_history:
            if t >= target_ns:
                if t - target_ns <= self.tolerance_ns:
                    return p
                else:
                    return None  # Too far in the future
        return None

    def get_missing_label_stats(self, labels: Sequence[LabelVector]) -> dict[str, float]:
        """Compute missing label rates for each horizon."""
        total = len(labels)
        if total == 0:
            return {}

        stats = {}
        for horizon in ["1s", "5s", "10s", "30s", "60s"]:
            ret_key = f"mid_return_{horizon}"
            missing = sum(1 for l in labels if getattr(l, ret_key) is None)
            stats[horizon] = missing / total
        return stats
