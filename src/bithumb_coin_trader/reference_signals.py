"""Parse and retain official Bithumb notices as non-executable reference signals."""

from __future__ import annotations

import hashlib
import fcntl
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1
_MARKET_RE = re.compile(r"\bKRW-[A-Z0-9]{2,15}\b")
_SYMBOL_GROUP_RE = re.compile(r"\(([A-Z0-9]{2,15}(?:\s*,\s*[A-Z0-9]{2,15})*)\)")


class ReferenceSignalError(ValueError):
    """Raised when an official reference signal cannot be represented safely."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _timestamp(value: Any, field: str, *, required: bool) -> str | None:
    if value in {None, ""} and not required:
        return None
    if not isinstance(value, str):
        raise ReferenceSignalError(f"{field} must be an ISO timestamp string")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ReferenceSignalError(f"{field} must be a valid ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReferenceSignalError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _category(title: str, official_categories: Sequence[str] = ()) -> str:
    compact = title.replace(" ", "")
    official = " ".join(official_categories).replace(" ", "")
    if any(word in compact for word in ("거래지원종료", "상장폐지")):
        return "trading_support_ended"
    if any(word in compact for word in ("거래지원", "신규상장")) or "마켓추가" in official:
        return "new_listing"
    if any(word in compact for word in ("투자유의", "유의종목")) or "거래유의" in official:
        return "investment_warning"
    if any(word in compact for word in ("입출금중단", "입출금일시중단", "입출금재개")) or "입출금" in official:
        return "transfer_status"
    if any(word in compact for word in ("점검", "시스템작업", "서비스중단")):
        return "maintenance"
    return "general"


def _markets(title: str, item: Mapping[str, Any]) -> tuple[str, ...]:
    markets = set(_MARKET_RE.findall(title.upper()))
    raw_market = item.get("market") or item.get("market_code")
    if isinstance(raw_market, str) and _MARKET_RE.fullmatch(raw_market.upper()):
        markets.add(raw_market.upper())
    for group in _SYMBOL_GROUP_RE.findall(title.upper()):
        for symbol in group.split(","):
            markets.add(f"KRW-{symbol.strip()}")
    return tuple(sorted(markets))


def _lifecycle_action(title: str, category: str) -> str:
    compact = title.replace(" ", "")
    if any(word in compact for word in ("해제", "재개", "정상화")):
        return "released"
    if any(word in compact for word in ("종료", "중단", "정지", "지정")):
        return "activated"
    if category == "new_listing":
        return "announced"
    return "informational"


def _attention_score(category: str, lifecycle_action: str) -> int:
    base = {
        "trading_support_ended": 100,
        "investment_warning": 95,
        "transfer_status": 80,
        "maintenance": 70,
        "new_listing": 60,
        "general": 20,
    }[category]
    if lifecycle_action == "released":
        return min(base, 45)
    return base


def _official_categories(item: Mapping[str, Any]) -> tuple[str, ...]:
    raw = item.get("categories") or item.get("category")
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        values = [value for value in raw if isinstance(value, str)]
    else:
        values = []
    return tuple(sorted({" ".join(value.split()) for value in values if value.strip()}))


def _normalize_source_timestamp(value: Any, field: str) -> str | None:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is None:
            value = f"{value.strip()}+09:00"
    return _timestamp(value, field, required=False)


def _observation_lag_seconds(observed_at: str, published_at: str | None) -> int | None:
    if published_at is None:
        return None
    observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    return max(0, int((observed - published).total_seconds()))


def _identity(
    notice_id: str | None,
    title: str,
    published_at: str | None,
    url: str | None,
    modified_at: str | None = None,
) -> str:
    if notice_id:
        key = {"source": "bithumb-official-notice", "notice_id": notice_id}
    else:
        key = {
            "source": "bithumb-official-notice",
            "title": " ".join(title.split()),
            "published_at": published_at,
            "url": url,
        }
    if modified_at is not None:
        key["modified_at"] = modified_at
    return hashlib.sha256(_canonical_json(key).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class NoticeReferenceSignal:
    observed_at: str
    title: str
    category: str
    affected_markets: tuple[str, ...]
    identity_sha256: str
    notice_id: str | None = None
    published_at: str | None = None
    modified_at: str | None = None
    url: str | None = None
    official_categories: tuple[str, ...] = ()
    lifecycle_action: str = "informational"
    attention_score: int = 20
    observation_lag_seconds: int | None = None
    source: str = "bithumb-official-notice"
    executable: bool = False
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at, "observed_at", required=True))
        object.__setattr__(
            self, "published_at", _timestamp(self.published_at, "published_at", required=False)
        )
        object.__setattr__(
            self, "modified_at", _timestamp(self.modified_at, "modified_at", required=False)
        )
        if self.schema_version not in {LEGACY_SCHEMA_VERSION, SCHEMA_VERSION}:
            raise ReferenceSignalError("unsupported notice reference signal schema")
        if not isinstance(self.title, str) or not self.title.strip():
            raise ReferenceSignalError("title must be a non-empty string")
        if self.category not in {
            "general", "investment_warning", "maintenance", "new_listing",
            "trading_support_ended", "transfer_status",
        }:
            raise ReferenceSignalError("invalid notice category")
        normalized_markets = tuple(self.affected_markets)
        object.__setattr__(self, "affected_markets", normalized_markets)
        normalized_categories = tuple(self.official_categories)
        object.__setattr__(self, "official_categories", normalized_categories)
        if tuple(sorted(set(normalized_markets))) != normalized_markets:
            raise ReferenceSignalError("affected_markets must be sorted and unique")
        if any(not _MARKET_RE.fullmatch(market) for market in self.affected_markets):
            raise ReferenceSignalError("affected_markets must contain KRW market codes")
        if tuple(sorted(set(normalized_categories))) != normalized_categories:
            raise ReferenceSignalError("official_categories must be sorted and unique")
        if any(not category.strip() for category in normalized_categories):
            raise ReferenceSignalError("official_categories must not contain empty values")
        if self.lifecycle_action not in {"activated", "announced", "informational", "released"}:
            raise ReferenceSignalError("invalid notice lifecycle action")
        if not isinstance(self.attention_score, int) or not 0 <= self.attention_score <= 100:
            raise ReferenceSignalError("attention_score must be an integer from 0 to 100")
        if self.observation_lag_seconds is not None and (
            not isinstance(self.observation_lag_seconds, int) or self.observation_lag_seconds < 0
        ):
            raise ReferenceSignalError("observation_lag_seconds must be a non-negative integer")
        if self.executable is not False:
            raise ReferenceSignalError("official notices are reference-only and never executable")
        expected = _identity(
            self.notice_id,
            self.title,
            self.published_at,
            self.url,
            self.modified_at if self.schema_version >= SCHEMA_VERSION else None,
        )
        if self.identity_sha256 != expected:
            raise ReferenceSignalError("notice identity digest is invalid")


@dataclass(frozen=True, slots=True)
class NoticeDigest:
    signal_count: int
    category_counts: Mapping[str, int]
    affected_markets: tuple[str, ...]
    identity_sha256: str
    summary: str


def _unwrap_items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ReferenceSignalError("notice payload is invalid JSON") from exc
    if isinstance(payload, list):
        if not all(isinstance(item, Mapping) for item in payload):
            raise ReferenceSignalError("notice list items must be objects")
        return list(payload)
    if not isinstance(payload, Mapping):
        raise ReferenceSignalError("notice payload must be an object or list")
    if "content" in payload and isinstance(payload["content"], list):
        for block in payload["content"]:
            if isinstance(block, Mapping) and isinstance(block.get("text"), str):
                return _unwrap_items(block["text"])
    current: Any = payload
    for _ in range(5):
        if isinstance(current, list):
            return _unwrap_items(current)
        if not isinstance(current, Mapping):
            break
        if any(key in current for key in ("title", "subject")):
            return [current]
        next_value = next((current[key] for key in ("data", "items", "notices", "list") if key in current), None)
        if next_value is None:
            break
        current = next_value
    raise ReferenceSignalError("notice payload wrapper is missing a notice list")


def parse_bithumb_notices(payload: Any, *, observed_at: str) -> list[NoticeReferenceSignal]:
    """Parse MCP/REST notice shapes without assigning any trading authority."""

    observed = _timestamp(observed_at, "observed_at", required=True)
    assert observed is not None
    signals: list[NoticeReferenceSignal] = []
    seen: set[str] = set()
    for item in _unwrap_items(payload):
        raw_title = item.get("title") or item.get("subject")
        if not isinstance(raw_title, str) or not raw_title.strip():
            continue
        title = " ".join(raw_title.split())
        raw_id = item.get("id") or item.get("notice_id") or item.get("no")
        notice_id = str(raw_id) if raw_id not in {None, ""} else None
        raw_url = item.get("url") or item.get("link") or item.get("pc_url")
        url = raw_url.strip() if isinstance(raw_url, str) and raw_url.strip() else None
        raw_published = next(
            (item[key] for key in ("published_at", "created_at", "createdAt", "registered_at") if item.get(key)),
            None,
        )
        published_at = _normalize_source_timestamp(raw_published, "published_at")
        raw_modified = next(
            (item[key] for key in ("modified_at", "updated_at", "updatedAt") if item.get(key)),
            None,
        )
        modified_at = _normalize_source_timestamp(raw_modified, "modified_at")
        official_categories = _official_categories(item)
        category = _category(title, official_categories)
        lifecycle_action = _lifecycle_action(title, category)
        identity = _identity(notice_id, title, published_at, url, modified_at)
        if identity in seen:
            continue
        seen.add(identity)
        signals.append(
            NoticeReferenceSignal(
                observed_at=observed,
                title=title,
                category=category,
                affected_markets=_markets(title, item),
                identity_sha256=identity,
                notice_id=notice_id,
                published_at=published_at,
                modified_at=modified_at,
                url=url,
                official_categories=official_categories,
                lifecycle_action=lifecycle_action,
                attention_score=_attention_score(category, lifecycle_action),
                observation_lag_seconds=_observation_lag_seconds(observed, published_at),
            )
        )
    return signals


def _decode_reference_lines(raw: bytes) -> list[NoticeReferenceSignal]:
    if raw and not raw.endswith(b"\n"):
        raise ReferenceSignalError("reference signal store has an unterminated final record")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ReferenceSignalError("reference signal store is not valid UTF-8") from exc
    signals: list[NoticeReferenceSignal] = []
    identities: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReferenceSignalError(f"reference signal line {line_number} is invalid JSON") from exc
        fields = set(NoticeReferenceSignal.__dataclass_fields__)
        legacy_fields = {
            "observed_at", "title", "category", "affected_markets", "identity_sha256",
            "notice_id", "published_at", "url", "source", "executable", "schema_version",
        }
        if not isinstance(payload, dict) or not legacy_fields.issubset(payload) or not set(payload).issubset(fields):
            raise ReferenceSignalError(f"reference signal line {line_number} has an invalid schema")
        try:
            signal = NoticeReferenceSignal(**payload)
        except TypeError as exc:
            raise ReferenceSignalError(f"reference signal line {line_number} has invalid fields") from exc
        if signal.identity_sha256 in identities:
            raise ReferenceSignalError(f"reference signal line {line_number} duplicates an identity")
        identities.add(signal.identity_sha256)
        signals.append(signal)
    return signals


def _read_descriptor(descriptor: int) -> list[NoticeReferenceSignal]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    return _decode_reference_lines(b"".join(chunks))


def read_reference_signals(path: str | Path) -> list[NoticeReferenceSignal]:
    source = Path(path)
    if not source.exists():
        return []
    descriptor = os.open(source, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        return _read_descriptor(descriptor)
    finally:
        os.close(descriptor)


def append_reference_signals(
    path: str | Path, signals: Sequence[NoticeReferenceSignal]
) -> list[NoticeReferenceSignal]:
    """Append only identities not already stored, preserving first observation time."""

    if not signals:
        return []
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        existing = _read_descriptor(descriptor)
        known = {signal.identity_sha256 for signal in existing}
        unique: list[NoticeReferenceSignal] = []
        for signal in signals:
            if signal.identity_sha256 not in known:
                known.add(signal.identity_sha256)
                unique.append(signal)
        if not unique:
            return []
        payload = "".join(_canonical_json(asdict(signal)) + "\n" for signal in unique).encode("utf-8")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("reference signal append made no progress")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return unique


def build_notice_digest(signals: Iterable[NoticeReferenceSignal]) -> NoticeDigest:
    ordered = sorted(signals, key=lambda signal: (signal.published_at or "", signal.identity_sha256))
    counts: dict[str, int] = {}
    markets: set[str] = set()
    for signal in ordered:
        counts[signal.category] = counts.get(signal.category, 0) + 1
        markets.update(signal.affected_markets)
    identities = [signal.identity_sha256 for signal in ordered]
    digest = hashlib.sha256(_canonical_json(identities).encode("utf-8")).hexdigest()
    category_text = ", ".join(f"{key}={counts[key]}" for key in sorted(counts)) or "none"
    market_text = ", ".join(sorted(markets)) or "none"
    return NoticeDigest(
        signal_count=len(ordered),
        category_counts=dict(sorted(counts.items())),
        affected_markets=tuple(sorted(markets)),
        identity_sha256=digest,
        summary=f"Bithumb notices {len(ordered)}; categories: {category_text}; markets: {market_text}",
    )
