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


@dataclass(frozen=True)
class RedundancyDecision:
    disposition: Literal["canonical", "duplicate", "conflict"]
    identity: str
    payload_sha256: str
    canonical_sha256: str
    canonical_source: str


@dataclass(frozen=True)
class RedundancyAudit:
    decision: RedundancyDecision
    source: str
    received_at: datetime
    raw_bytes: bytes | None = None


class BithumbRedundancyFilter:
    """A finite monotonic-time window; expiry can expose very late repeats."""

    def __init__(self, *, max_entries: int = 100_000, retention_seconds: float = 180.0) -> None:
        if max_entries < 1 or retention_seconds <= 0:
            raise ValueError("dedup bounds must be positive")
        self.max_entries = max_entries
        self.retention_seconds = retention_seconds
        self._seen: OrderedDict[str, tuple[float, str, str]] = OrderedDict()
        self.evicted = 0

    @property
    def size(self) -> int:
        return len(self._seen)

    @staticmethod
    def _identity(stream: str, market: str, payload: dict[str, Any], digest: str) -> str:
        # The public trade schema documents sequential_id as unique but not ordered.
        # Orderbook and ticker expose timestamps; neither documents a sequence ID.
        if stream == "trade" and payload.get("sequential_id") is not None:
            native = ("sequential_id", payload["sequential_id"])
        elif stream in ("orderbook", "ticker") and payload.get("timestamp") is not None:
            native = ("timestamp", payload["timestamp"], payload.get("stream_type", "REALTIME"))
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
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        identity = self._identity(stream, market, payload, digest)
        while self._seen:
            oldest_time = next(iter(self._seen.values()))[0]
            if observed_monotonic - oldest_time <= self.retention_seconds:
                break
            self._seen.popitem(last=False)
            self.evicted += 1
        prior = self._seen.get(identity)
        if prior is not None:
            _, prior_digest, prior_source = prior
            return RedundancyDecision(
                "duplicate" if digest == prior_digest else "conflict",
                identity, digest, prior_digest, prior_source,
            )
        self._seen[identity] = (observed_monotonic, digest, source)
        if len(self._seen) > self.max_entries:
            self._seen.popitem(last=False)
            self.evicted += 1
        return RedundancyDecision("canonical", identity, digest, digest, source)

    def release_canonical(self, decision: RedundancyDecision) -> bool:
        """Roll back an unqueued first copy so another source can become canonical."""
        if decision.disposition != "canonical":
            return False
        prior = self._seen.get(decision.identity)
        if prior is None or prior[1:] != (decision.payload_sha256, decision.canonical_source):
            return False
        del self._seen[decision.identity]
        return True
