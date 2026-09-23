"""Bounded, arrival-order deduplication for redundant Bithumb public sockets.

The first observed copy is canonical.  A later copy with the same exchange
identity and different content is a conflict, never a replacement.  Trade
sequential IDs identify trades but do not imply delivery order (Bithumb docs).
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any, Literal


BITHUMB_TRADE_IDENTITY_FIELD = "sequential_id"
# Every other top-level trade field, including unknown future fields, remains
# semantic and fail-closed for two copies of the same trade identity.
BITHUMB_TRADE_NON_SEMANTIC_FIELDS = frozenset({"timestamp"})


@dataclass(frozen=True)
class RedundancyDecision:
    disposition: Literal["canonical", "duplicate", "conflict"]
    identity: str
    payload_sha256: str
    canonical_sha256: str
    canonical_source: str
    equivalence: Literal["canonical", "exact", "trade_timestamp_ignored", "conflict"]


@dataclass(frozen=True)
class RedundancyAudit:
    decision: RedundancyDecision
    source: str
    received_at: datetime
    stream: str
    market: str
    raw_bytes: bytes | None = None


class BithumbRedundancyFilter:
    """A finite monotonic-time window; expiry can expose very late repeats."""

    def __init__(self, *, max_entries: int = 100_000, retention_seconds: float = 180.0) -> None:
        if max_entries < 1 or retention_seconds <= 0:
            raise ValueError("dedup bounds must be positive")
        self.max_entries = max_entries
        self.retention_seconds = retention_seconds
        self._seen: OrderedDict[str, tuple[float, str, str, str]] = OrderedDict()
        self.evicted = 0

    @property
    def size(self) -> int:
        return len(self._seen)

    @staticmethod
    def _identity(stream: str, market: str, payload: dict[str, Any], digest: str) -> str:
        # Trade has a documented nominal identity. Ticker and orderbook do not,
        # so their complete payload digest is the conservative event identity:
        # exact copies deduplicate while distinct states are retained.
        if stream == "trade" and payload.get(BITHUMB_TRADE_IDENTITY_FIELD) is not None:
            native = (BITHUMB_TRADE_IDENTITY_FIELD, payload[BITHUMB_TRADE_IDENTITY_FIELD])
        else:
            native = ("payload_sha256", digest)
        return json.dumps(("bithumb", stream, market.upper(), native), separators=(",", ":"))

    def observe(
        self,
        stream: str,
        market: str,
        payload: dict[str, Any],
        source: str,
        observed_monotonic: float,
    ) -> RedundancyDecision:
        raw_canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        raw_digest = hashlib.sha256(raw_canonical.encode("utf-8")).hexdigest()
        comparison_payload = payload
        if stream == "trade" and payload.get(BITHUMB_TRADE_IDENTITY_FIELD) is not None:
            # Bithumb can emit the same documented trade (same sequential_id
            # and trade fields) with a slightly different envelope timestamp
            # on concurrent public sockets.  That source-local field must not
            # turn an otherwise identical trade into a data conflict.
            comparison_payload = dict(payload)
            for field in BITHUMB_TRADE_NON_SEMANTIC_FIELDS:
                comparison_payload.pop(field, None)
        canonical = json.dumps(
            comparison_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        comparison_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        identity = self._identity(stream, market, payload, comparison_digest)
        while self._seen:
            oldest_time = next(iter(self._seen.values()))[0]
            if observed_monotonic - oldest_time <= self.retention_seconds:
                break
            self._seen.popitem(last=False)
            self.evicted += 1
        prior = self._seen.get(identity)
        if prior is not None:
            _, prior_comparison_digest, prior_raw_digest, prior_source = prior
            matches = comparison_digest == prior_comparison_digest
            return RedundancyDecision(
                "duplicate" if matches else "conflict",
                identity,
                raw_digest,
                prior_raw_digest,
                prior_source,
                (
                    "exact" if raw_digest == prior_raw_digest
                    else "trade_timestamp_ignored" if matches
                    else "conflict"
                ),
            )
        self._seen[identity] = (observed_monotonic, comparison_digest, raw_digest, source)
        if len(self._seen) > self.max_entries:
            self._seen.popitem(last=False)
            self.evicted += 1
        return RedundancyDecision("canonical", identity, raw_digest, raw_digest, source, "canonical")

    def release_canonical(self, decision: RedundancyDecision) -> bool:
        """Roll back an unqueued first copy so another source can become canonical."""
        if decision.disposition != "canonical":
            return False
        prior = self._seen.get(decision.identity)
        if prior is None or prior[2:] != (decision.payload_sha256, decision.canonical_source):
            return False
        del self._seen[decision.identity]
        return True
