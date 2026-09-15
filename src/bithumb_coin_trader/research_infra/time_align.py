"""Time Alignment and No-Lookahead Infrastructure.

CRITICAL CONTRACT:
    Features at time t must only use information available at or before t.
    Labels at time t use future information (by definition) but must not
    leak into feature construction.

    The ordering timestamp is LOCAL_WRITE_TIMESTAMP (when event was persisted).
    Cross-exchange joins use backward/as-of on this timestamp — never forward.

Timestamp hierarchy:
    1. exchange_timestamp_ms — from exchange, NOT used for ordering (clocks unsynchronized)
    2. local_recv_timestamp_ms — when event arrived at collector
    3. local_write_timestamp_ms — when event was persisted (AUTHORITATIVE for ordering)
    4. ordering_timestamp_ns — = local_write_timestamp_ms * 1_000_000

Cross-exchange alignment:
    Uses backward/as-of join on ordering_timestamp.
    For exchange A at time t, the matching B observation is the latest B
    with ordering_timestamp <= t. This guarantees no lookahead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Sequence

from .canonical_events import CanonicalEvent, EventKind


@dataclass(frozen=True, slots=True)
class AlignedSnapshot:
    """A point-in-time snapshot aligned across exchanges.

    Contains the latest available state from each exchange as of a
    specific ordering timestamp. All values are backward-looking —
    no future information is included.
    """

    ordering_timestamp_ns: int
    primary_event: CanonicalEvent
    aligned_events: dict[str, CanonicalEvent]  # exchange -> latest event as of timestamp
    lag_ms: dict[str, int]  # exchange -> lag from primary


def check_no_lookahead(
    events: Sequence[CanonicalEvent],
) -> list[str]:
    """Verify that events are ordered by ordering_timestamp with no lookahead.

    Returns list of violation descriptions. Empty list = clean.
    """
    violations = []
    for i in range(1, len(events)):
        prev = events[i - 1]
        curr = events[i]
        if curr.ordering_timestamp_ns < prev.ordering_timestamp_ns:
            violations.append(
                f"Event {i} ordering_ts ({curr.ordering_timestamp_ns}) < "
                f"Event {i-1} ordering_ts ({prev.ordering_timestamp_ns})"
            )
    return violations


def build_as_of_index(
    events: Sequence[CanonicalEvent],
    target_exchange: str,
) -> dict[int, CanonicalEvent]:
    """Build an as-of lookup index for cross-exchange alignment.

    For each ordering_timestamp in the primary exchange's events,
    returns the latest event from target_exchange with
    ordering_timestamp <= primary's timestamp.

    This guarantees no lookahead: we never use information from
    the target exchange that wasn't available at the primary's time.
    """
    index: dict[int, CanonicalEvent] = {}
    target_events = [e for e in events if e.exchange == target_exchange]
    if not target_events:
        return index

    target_idx = 0
    last_target: CanonicalEvent | None = None

    # Sort both by ordering timestamp
    primary_times = sorted(set(e.ordering_timestamp_ns for e in events))

    for t in primary_times:
        # Advance target pointer to latest event <= t
        while target_idx < len(target_events) and target_events[target_idx].ordering_timestamp_ns <= t:
            last_target = target_events[target_idx]
            target_idx += 1
        if last_target is not None:
            index[t] = last_target

    return index


def align_cross_exchange(
    primary_events: Sequence[CanonicalEvent],
    all_events: Sequence[CanonicalEvent],
    exchanges: Sequence[str],
) -> Iterator[AlignedSnapshot]:
    """Produce aligned cross-exchange snapshots using backward/as-of join.

    For each primary event, finds the latest event from each other exchange
    with ordering_timestamp <= primary's timestamp.

    Args:
        primary_events: Events from the primary exchange (e.g., Bithumb)
        all_events: All events from all exchanges, sorted by ordering_timestamp
        exchanges: List of exchanges to align

    Yields:
        AlignedSnapshot for each primary event
    """
    # Build per-exchange sorted event lists
    by_exchange: dict[str, list[CanonicalEvent]] = {}
    for ex in exchanges:
        by_exchange[ex] = sorted(
            [e for e in all_events if e.exchange == ex],
            key=lambda e: e.ordering_timestamp_ns,
        )

    # Build as-of indices for non-primary exchanges
    other_exchanges = [ex for ex in exchanges if ex != primary_events[0].exchange if primary_events]
    as_of_indices: dict[str, dict[int, CanonicalEvent]] = {}
    for ex in other_exchanges:
        as_of_indices[ex] = build_as_of_index(all_events, ex)

    for event in primary_events:
        t = event.ordering_timestamp_ns
        aligned = {event.exchange: event}
        lag = {event.exchange: 0}

        for ex in other_exchanges:
            idx = as_of_indices.get(ex, {})
            match = None
            # Find latest event <= t
            for check_t in sorted(idx.keys(), reverse=True):
                if check_t <= t:
                    match = idx[check_t]
                    break
            if match is not None:
                aligned[ex] = match
                lag[ex] = t - match.ordering_timestamp_ns

        yield AlignedSnapshot(
            ordering_timestamp_ns=t,
            primary_event=event,
            aligned_events=aligned,
            lag_ms={ex: v // 1_000_000 for ex, v in lag.items()},
        )


def partition_events_by_exchange(
    events: Sequence[CanonicalEvent],
) -> dict[str, list[CanonicalEvent]]:
    """Partition events by exchange, maintaining time order within each."""
    by_exchange: dict[str, list[CanonicalEvent]] = {}
    for event in events:
        by_exchange.setdefault(event.exchange, []).append(event)
    for ex in by_exchange:
        by_exchange[ex].sort(key=lambda e: e.ordering_timestamp_ns)
    return by_exchange


def filter_events_by_kind(
    events: Sequence[CanonicalEvent],
    kind: EventKind,
) -> list[CanonicalEvent]:
    """Filter events by kind, maintaining time order."""
    return [e for e in events if e.event_kind == kind]


def filter_events_by_market(
    events: Sequence[CanonicalEvent],
    market: str,
) -> list[CanonicalEvent]:
    """Filter events by market, maintaining time order."""
    return [e for e in events if e.market == market]
