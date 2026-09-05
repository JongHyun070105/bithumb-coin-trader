"""Causal As-Of Join and Cross-Exchange Alignment Engine (P1.4, P1.5).

Implements strict backward as-of semantics:
At decision timestamp T, the reference event timestamp t_ref must satisfy:
    t_ref <= T
Nearest-neighbor alignment is strictly prohibited as it leaks future data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Generic, Sequence, TypeVar


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class AlignedPair(Generic[T]):
    target_timestamp: float
    reference_timestamp: float | None
    reference_item: T | None
    staleness_ms: float | None
    status: str  # "ALIGNED", "MISSING_REFERENCE", "STALE_REFERENCE"


def _to_epoch_seconds(ts: datetime | float | int) -> float:
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.timestamp()
    return float(ts)


class BackwardAsOfAligner(Generic[T]):
    """Strict causal backward as-of joiner."""

    def __init__(
        self,
        reference_stream: Sequence[tuple[float | datetime, T]],
        *,
        max_staleness_ms: float = 5000.0,
        assumed_transport_delay_ms: float = 0.0,
    ) -> None:
        if max_staleness_ms <= 0:
            raise ValueError("max_staleness_ms must be positive")
        if assumed_transport_delay_ms < 0:
            raise ValueError("assumed_transport_delay_ms must be non-negative")

        # Ensure reference stream is sorted chronologically
        sorted_stream = sorted(
            [(_to_epoch_seconds(ts), item) for ts, item in reference_stream],
            key=lambda x: x[0],
        )
        self.reference_stream = sorted_stream
        self.max_staleness_ms = max_staleness_ms
        self.assumed_transport_delay_ms = assumed_transport_delay_ms

    def align_as_of(self, target_time: datetime | float | int) -> AlignedPair[T]:
        """Finds latest reference event available at or prior to target_time."""
        target_sec = _to_epoch_seconds(target_time)
        effective_cutoff_sec = target_sec - (self.assumed_transport_delay_ms / 1000.0)

        # Binary search / scan for latest reference event <= effective_cutoff_sec
        best_ref_sec: float | None = None
        best_ref_item: T | None = None

        # Binary search over sorted reference_stream
        low = 0
        high = len(self.reference_stream) - 1
        found_idx = -1

        while low <= high:
            mid = (low + high) // 2
            ref_sec = self.reference_stream[mid][0]
            if ref_sec <= effective_cutoff_sec:
                found_idx = mid
                low = mid + 1  # search for later valid event
            else:
                high = mid - 1

        if found_idx == -1:
            return AlignedPair(
                target_timestamp=target_sec,
                reference_timestamp=None,
                reference_item=None,
                staleness_ms=None,
                status="MISSING_REFERENCE",
            )

        best_ref_sec, best_ref_item = self.reference_stream[found_idx]
        staleness_ms = (target_sec - best_ref_sec) * 1000.0

        if staleness_ms > self.max_staleness_ms:
            return AlignedPair(
                target_timestamp=target_sec,
                reference_timestamp=best_ref_sec,
                reference_item=best_ref_item,
                staleness_ms=staleness_ms,
                status="STALE_REFERENCE",
            )

        return AlignedPair(
            target_timestamp=target_sec,
            reference_timestamp=best_ref_sec,
            reference_item=best_ref_item,
            staleness_ms=staleness_ms,
            status="ALIGNED",
        )
