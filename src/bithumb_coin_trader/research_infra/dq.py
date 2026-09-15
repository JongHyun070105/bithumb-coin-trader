"""Data Quality / Coverage Layer for Microstructure Research.

Distinguishes between:
    DATA_PRESENT          - Data exists and was observed
    VERIFIED_ZERO_EVENT   - Immutable evidence proves zero events in interval
    UNKNOWN_MISSING       - No data and no proof of zero events (must exclude)
    INCOMPLETE            - Partial data in interval
    CORRUPT               - Data exists but fails validation
    UNVERIFIED            - Data exists but not yet validated
    UNAVAILABLE           - Data source not accessible

For V2's known eight missing slots:
    2026-09-12_17: Bithumb KRW-MANA ticker, KRW-MANA trade
    2026-09-12_18: Bithumb KRW-AXS ticker, KRW-AXS trade, KRW-MANA ticker, KRW-MANA trade
    2026-09-12_19: Bithumb KRW-MANA ticker, KRW-MANA trade
    These are classified as UNKNOWN_MISSING (not VERIFIED_ZERO_EVENT).

Missing != zero-event. This is a critical scientific invariant.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


class CoverageState(str, Enum):
    PHYSICAL_FILE_PRESENT = "PHYSICAL_FILE_PRESENT"  # File exists, not yet validated
    PARSE_VALID = "PARSE_VALID"  # Parsed successfully
    MANIFEST_BOUND = "MANIFEST_BOUND"  # Has valid manifest binding
    COHORT_VALID = "COHORT_VALID"  # Timestamps fall in expected cohort
    DATA_PRESENT = "DATA_PRESENT"  # Scientifically verified data present
    VERIFIED_ZERO_EVENT = "VERIFIED_ZERO_EVENT"
    UNKNOWN_MISSING = "UNKNOWN_MISSING"
    INCOMPLETE = "INCOMPLETE"
    CORRUPT = "CORRUPT"
    UNVERIFIED = "UNVERIFIED"
    UNAVAILABLE = "UNAVAILABLE"

    @property
    def is_safe_for_research(self) -> bool:
        """Whether this state is safe to include in research without special handling."""
        return self in (
            CoverageState.DATA_PRESENT,
            CoverageState.VERIFIED_ZERO_EVENT,
        )

    @property
    def requires_exclusion(self) -> bool:
        """Whether intervals with this state must be excluded from research."""
        return self in (
            CoverageState.UNKNOWN_MISSING,
            CoverageState.CORRUPT,
            CoverageState.UNAVAILABLE,
            CoverageState.UNVERIFIED,
            CoverageState.PHYSICAL_FILE_PRESENT,
            CoverageState.PARSE_VALID,
            CoverageState.MANIFEST_BOUND,
            CoverageState.COHORT_VALID,
        )


@dataclass(frozen=True)
class FeedSlotCoverage:
    """Coverage status for a single (exchange, feed, market, hour) slot."""

    exchange: str
    feed: str  # stream type: trade, orderbook, ticker
    market: str
    cohort_utc: str  # "YYYY-MM-DD_HH"
    state: CoverageState
    event_count: int = 0
    first_timestamp_ms: int | None = None
    last_timestamp_ms: int | None = None
    source_file: str | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> FeedSlotCoverage:
        return cls(
            exchange=d["exchange"],
            feed=d["feed"],
            market=d["market"],
            cohort_utc=d["cohort_utc"],
            state=CoverageState(d["state"]),
            event_count=d.get("event_count", 0),
            first_timestamp_ms=d.get("first_timestamp_ms"),
            last_timestamp_ms=d.get("last_timestamp_ms"),
            source_file=d.get("source_file"),
            notes=d.get("notes", ""),
        )


@dataclass(frozen=True)
class DQSummary:
    """Aggregated DQ summary for a dataset or time range."""

    dataset_id: str
    total_slots: int
    data_present: int
    verified_zero: int
    unknown_missing: int
    incomplete: int
    corrupt: int
    unverified: int
    unavailable: int
    safe_slots: int  # DATA_PRESENT + VERIFIED_ZERO_EVENT
    coverage_pct: float  # safe_slots / total_slots
    exclusion_count: int  # slots requiring exclusion
    affected_hours: tuple[str, ...]
    affected_feeds: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DQCatalog:
    """In-memory catalog of feed-slot coverage statuses."""

    def __init__(self) -> None:
        self._slots: dict[str, FeedSlotCoverage] = {}

    def _key(self, exchange: str, feed: str, market: str, cohort: str) -> str:
        return f"{exchange}/{feed}/{market}/{cohort}"

    def add_slot(self, slot: FeedSlotCoverage) -> None:
        key = self._key(slot.exchange, slot.feed, slot.market, slot.cohort_utc)
        self._slots[key] = slot

    def get_slot(
        self, exchange: str, feed: str, market: str, cohort: str
    ) -> FeedSlotCoverage | None:
        return self._slots.get(self._key(exchange, feed, market, cohort))

    def get_slots_for_cohort(self, cohort: str) -> list[FeedSlotCoverage]:
        return [s for s in self._slots.values() if s.cohort_utc == cohort]

    def get_slots_for_hour(self, hour: str) -> list[FeedSlotCoverage]:
        """Get all slots for a given hour (same as get_slots_for_cohort)."""
        return self.get_slots_for_cohort(hour)

    def list_cohorts(self) -> list[str]:
        cohorts = sorted(set(s.cohort_utc for s in self._slots.values()))
        return cohorts

    def require_safe_cohorts(
        self,
        exchanges: Sequence[str] | None = None,
        feeds: Sequence[str] | None = None,
        markets: Sequence[str] | None = None,
    ) -> list[str]:
        """Return cohorts where ALL matching slots are safe for research."""
        safe = []
        for cohort in self.list_cohorts():
            slots = self.get_slots_for_cohort(cohort)
            if exchanges:
                slots = [s for s in slots if s.exchange in exchanges]
            if feeds:
                slots = [s for s in slots if s.feed in feeds]
            if markets:
                slots = [s for s in slots if s.market in markets]
            if all(s.state.is_safe_for_research for s in slots):
                safe.append(cohort)
        return safe

    def summary(self, dataset_id: str) -> DQSummary:
        all_slots = list(self._slots.values())
        by_state = {}
        for state in CoverageState:
            by_state[state] = sum(1 for s in all_slots if s.state == state)

        safe = by_state[CoverageState.DATA_PRESENT] + by_state[CoverageState.VERIFIED_ZERO_EVENT]
        excl = sum(by_state[s] for s in CoverageState if s.requires_exclusion)
        total = len(all_slots)

        affected_hours = sorted(set(
            s.cohort_utc for s in all_slots if s.state.requires_exclusion
        ))
        affected_feeds = sorted(set(
            f"{s.exchange}/{s.feed}/{s.market}"
            for s in all_slots
            if s.state.requires_exclusion
        ))

        return DQSummary(
            dataset_id=dataset_id,
            total_slots=total,
            data_present=by_state[CoverageState.DATA_PRESENT],
            verified_zero=by_state[CoverageState.VERIFIED_ZERO_EVENT],
            unknown_missing=by_state[CoverageState.UNKNOWN_MISSING],
            incomplete=by_state[CoverageState.INCOMPLETE],
            corrupt=by_state[CoverageState.CORRUPT],
            unverified=by_state[CoverageState.UNVERIFIED],
            unavailable=by_state[CoverageState.UNAVAILABLE],
            safe_slots=safe,
            coverage_pct=safe / total if total > 0 else 0.0,
            exclusion_count=excl,
            affected_hours=tuple(affected_hours),
            affected_feeds=tuple(affected_feeds),
        )

    def save(self, path: Path) -> None:
        data = [s.to_dict() for s in self._slots.values()]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path) -> DQCatalog:
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        cat = cls()
        for d in data:
            cat.add_slot(FeedSlotCoverage.from_dict(d))
        return cat


def build_dq_catalog_from_local(
    raw_root: Path,
    dataset_id: str,
    expected_feeds: dict[tuple[str, str, str], list[str]] | None = None,
) -> DQCatalog:
    """Build DQ catalog by scanning local raw JSONL files.

    Args:
        raw_root: Path to data/microstructure/raw/
        dataset_id: Dataset identifier
        expected_feeds: Optional {(exchange, feed, market): [cohorts]} to detect missing slots

    Returns:
        DQCatalog with discovered slots and UNKNOWN_MISSING for expected-but-absent feeds
    """
    cat = DQCatalog()

    discovered: set[tuple[str, str, str, str]] = set()

    if not raw_root.exists():
        return cat

    for date_dir in sorted(raw_root.iterdir()):
        if not date_dir.is_dir():
            continue
        date = date_dir.name  # e.g., "2026-08-25"
        for exchange_dir in sorted(date_dir.iterdir()):
            if not exchange_dir.is_dir():
                continue
            exchange = exchange_dir.name  # e.g., "bithumb"
            for feed_dir in sorted(exchange_dir.iterdir()):
                if not feed_dir.is_dir():
                    continue
                feed = feed_dir.name  # e.g., "trade"
                for jsonl_file in sorted(feed_dir.iterdir()):
                    if not jsonl_file.name.endswith(".jsonl"):
                        continue
                    # Parse filename: {exchange}_{feed}_{market}_{date}_{hour}.jsonl
                    parts = jsonl_file.stem.split("_")
                    if len(parts) >= 5:
                        market = parts[2].upper().replace("-", "-")
                        hour = parts[4]
                        cohort = f"{date}_{hour}"

                        discovered.add((exchange, feed, market, cohort))

                        # File exists — mark as PHYSICAL_FILE_PRESENT only.
                        # DATA_PRESENT requires higher-level validation.
                        file_size = jsonl_file.stat().st_size
                        if file_size == 0:
                            state = CoverageState.UNVERIFIED
                        else:
                            state = CoverageState.PHYSICAL_FILE_PRESENT

                        cat.add_slot(FeedSlotCoverage(
                            exchange=exchange,
                            feed=feed,
                            market=market,
                            cohort_utc=cohort,
                            state=state,
                            event_count=-1,  # Not counted at physical level
                            source_file=str(jsonl_file),
                        ))

    # Check for expected-but-missing slots
    if expected_feeds:
        for (exchange, feed, market), cohorts in expected_feeds.items():
            for cohort in cohorts:
                if (exchange, feed, market, cohort) not in discovered:
                    cat.add_slot(FeedSlotCoverage(
                        exchange=exchange,
                        feed=feed,
                        market=market,
                        cohort_utc=cohort,
                        state=CoverageState.UNKNOWN_MISSING,
                        event_count=0,
                        notes="Expected but not found in local data",
                    ))

    return cat


def build_v2_known_missing() -> list[FeedSlotCoverage]:
    """Return the known eight missing V2 feed-hour slots as UNKNOWN_MISSING.

    These are NOT verified zero-event. They must be classified as UNKNOWN_MISSING
    unless immutable historical evidence proves otherwise.
    """
    bithumb_mana_ticker_17 = FeedSlotCoverage(
        exchange="bithumb", feed="ticker", market="KRW-MANA",
        cohort_utc="2026-09-12_17", state=CoverageState.UNKNOWN_MISSING,
        notes="V2 known missing: no immutable zero-event proof",
    )
    bithumb_mana_trade_17 = FeedSlotCoverage(
        exchange="bithumb", feed="trade", market="KRW-MANA",
        cohort_utc="2026-09-12_17", state=CoverageState.UNKNOWN_MISSING,
        notes="V2 known missing: no immutable zero-event proof",
    )
    bithumb_axs_ticker_18 = FeedSlotCoverage(
        exchange="bithumb", feed="ticker", market="KRW-AXS",
        cohort_utc="2026-09-12_18", state=CoverageState.UNKNOWN_MISSING,
        notes="V2 known missing: no immutable zero-event proof",
    )
    bithumb_axs_trade_18 = FeedSlotCoverage(
        exchange="bithumb", feed="trade", market="KRW-AXS",
        cohort_utc="2026-09-12_18", state=CoverageState.UNKNOWN_MISSING,
        notes="V2 known missing: no immutable zero-event proof",
    )
    bithumb_mana_ticker_18 = FeedSlotCoverage(
        exchange="bithumb", feed="ticker", market="KRW-MANA",
        cohort_utc="2026-09-12_18", state=CoverageState.UNKNOWN_MISSING,
        notes="V2 known missing: no immutable zero-event proof",
    )
    bithumb_mana_trade_18 = FeedSlotCoverage(
        exchange="bithumb", feed="trade", market="KRW-MANA",
        cohort_utc="2026-09-12_18", state=CoverageState.UNKNOWN_MISSING,
        notes="V2 known missing: no immutable zero-event proof",
    )
    bithumb_mana_ticker_19 = FeedSlotCoverage(
        exchange="bithumb", feed="ticker", market="KRW-MANA",
        cohort_utc="2026-09-12_19", state=CoverageState.UNKNOWN_MISSING,
        notes="V2 known missing: no immutable zero-event proof",
    )
    bithumb_mana_trade_19 = FeedSlotCoverage(
        exchange="bithumb", feed="trade", market="KRW-MANA",
        cohort_utc="2026-09-12_19", state=CoverageState.UNKNOWN_MISSING,
        notes="V2 known missing: no immutable zero-event proof",
    )
    return [
        bithumb_mana_ticker_17, bithumb_mana_trade_17,
        bithumb_axs_ticker_18, bithumb_axs_trade_18,
        bithumb_mana_ticker_18, bithumb_mana_trade_18,
        bithumb_mana_ticker_19, bithumb_mana_trade_19,
    ]


# V2 authoritative feed universe: 76 feeds per hour
_V2_BITHUMB_MARKETS = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-DOGE",
    "KRW-ADA", "KRW-AVAX", "KRW-DOT", "KRW-LINK", "KRW-NEAR",
    "KRW-SUI", "KRW-ETC", "KRW-BCH", "KRW-TRX", "KRW-SHIB",
    "KRW-SAND", "KRW-MANA", "KRW-AXS", "KRW-APT", "KRW-XLM",
]
_V2_BINANCE_MARKETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
_V2_UPBIT_MARKETS = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP"]


def get_v2_feed_universe() -> list[tuple[str, str, str]]:
    """Return the V2 76-feed universe as (exchange, feed, market) tuples."""
    feeds = []
    for market in _V2_BITHUMB_MARKETS:
        for feed in ("trade", "orderbook", "ticker"):
            feeds.append(("bithumb", feed, market))
    for market in _V2_BINANCE_MARKETS:
        for feed in ("trade", "orderbook"):
            feeds.append(("binance", feed, market))
    for market in _V2_UPBIT_MARKETS:
        for feed in ("trade", "orderbook"):
            feeds.append(("upbit", feed, market))
    return feeds  # 20*3 + 4*2 + 4*2 = 60+8+8 = 76


def get_v2_candidate_hours() -> list[str]:
    """Return the V2 30 candidate hours."""
    hours = []
    for h in range(24):
        hours.append(f"2026-09-12_{h:02d}")
    for h in range(6):
        hours.append(f"2026-09-13_{h:02d}")
    return hours  # 30 hours


def build_v2_authoritative_dq_catalog() -> DQCatalog:
    """Build V2 DQ catalog from the authoritative 2280-slot universe.

    This is NOT derived from local file counts.
    It is based on the documented V2 contract:
    - 30 candidate hours
    - 76 feeds per hour
    - 2280 total slots
    - 2272 DATA_PRESENT
    - 8 UNKNOWN_MISSING (the known eight)
    - 0 VERIFIED_ZERO_EVENT for the missing eight
    """
    cat = DQCatalog()

    missing_slots = build_v2_known_missing()
    missing_set = {
        (s.exchange, s.feed, s.market, s.cohort_utc) for s in missing_slots
    }

    universe = get_v2_feed_universe()
    hours = get_v2_candidate_hours()

    for hour in hours:
        for exchange, feed, market in universe:
            key = (exchange, feed, market, hour)
            if key in missing_set:
                # Find the corresponding missing slot
                slot = next(s for s in missing_slots
                           if (s.exchange, s.feed, s.market, s.cohort_utc) == key)
                cat.add_slot(slot)
            else:
                cat.add_slot(FeedSlotCoverage(
                    exchange=exchange,
                    feed=feed,
                    market=market,
                    cohort_utc=hour,
                    state=CoverageState.DATA_PRESENT,
                    notes="V2 authoritative: documented as present in V2 validation evidence",
                ))

    return cat
