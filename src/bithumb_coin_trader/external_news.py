"""Optional external crypto-news collection with no trading authority."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlparse


SCHEMA_VERSION = 1
FINNHUB_ENDPOINT = "https://finnhub.io/api/v1/news?category=crypto"
MAX_ARTICLE_AGE_SECONDS = 48 * 60 * 60
MAX_FUTURE_SKEW_SECONDS = 5 * 60
_MARKET_RE = re.compile(r"KRW-[A-Z0-9]{2,15}")
_SAFE_TEXT_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_NAME_ALIASES = {
    "BITCOIN": "BTC",
    "ETHEREUM": "ETH",
    "RIPPLE": "XRP",
    "SOLANA": "SOL",
    "DOGECOIN": "DOGE",
}


class ExternalNewsError(ValueError):
    """Raised for invalid or unavailable external reference-news data."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _timestamp(value: str, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ExternalNewsError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExternalNewsError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any, field: str, *, limit: int) -> str:
    if not isinstance(value, str):
        raise ExternalNewsError(f"{field} must be text")
    cleaned = " ".join(_SAFE_TEXT_RE.sub("", value).split())
    if not cleaned:
        raise ExternalNewsError(f"{field} must not be empty")
    return cleaned[:limit]


def _safe_url(value: Any) -> str:
    if not isinstance(value, str):
        raise ExternalNewsError("news URL must be text")
    cleaned = value.strip()
    if len(cleaned) > 2048 or _SAFE_TEXT_RE.search(cleaned) or any(character.isspace() for character in cleaned):
        raise ExternalNewsError("news URL contains unsafe characters or is too long")
    parsed = urlparse(cleaned)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ExternalNewsError("news URL must be an HTTPS URL without credentials")
    return cleaned


def _identity(article_id: str) -> str:
    return hashlib.sha256(
        _canonical_json({"provider": "finnhub", "article_id": article_id}).encode("utf-8")
    ).hexdigest()


def _affected_markets(title: str, known_markets: Iterable[str]) -> tuple[str, ...]:
    known = {market for market in known_markets if _MARKET_RE.fullmatch(market)}
    upper = title.upper()
    symbols: set[str] = set()
    for name, symbol in _NAME_ALIASES.items():
        if re.search(rf"\b{re.escape(name)}\b", upper):
            symbols.add(symbol)
    for market in known:
        symbol = market.removeprefix("KRW-")
        if re.search(rf"(?:\$|\()\s*{re.escape(symbol)}\b", upper):
            symbols.add(symbol)
    return tuple(sorted({f"KRW-{symbol}" for symbol in symbols if f"KRW-{symbol}" in known}))


@dataclass(frozen=True, slots=True)
class NewsReferenceSignal:
    observed_at: str
    published_at: str
    title: str
    source_name: str
    url: str
    provider_article_id: str
    affected_markets: tuple[str, ...]
    identity_sha256: str
    reference_score: int
    provider: str = "finnhub"
    executable: bool = False
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at, "observed_at"))
        object.__setattr__(self, "published_at", _timestamp(self.published_at, "published_at"))
        object.__setattr__(self, "title", _clean_text(self.title, "title", limit=500))
        object.__setattr__(self, "source_name", _clean_text(self.source_name, "source_name", limit=120))
        object.__setattr__(self, "url", _safe_url(self.url))
        markets = tuple(self.affected_markets)
        object.__setattr__(self, "affected_markets", markets)
        if tuple(sorted(set(markets))) != markets or any(not _MARKET_RE.fullmatch(m) for m in markets):
            raise ExternalNewsError("affected_markets must be sorted unique KRW markets")
        if not self.provider_article_id or len(self.provider_article_id) > 120:
            raise ExternalNewsError("provider_article_id is invalid")
        if self.identity_sha256 != _identity(self.provider_article_id):
            raise ExternalNewsError("news identity digest is invalid")
        if not isinstance(self.reference_score, int) or not 0 <= self.reference_score <= 100:
            raise ExternalNewsError("reference_score must be an integer from 0 to 100")
        if self.provider != "finnhub" or self.schema_version != SCHEMA_VERSION:
            raise ExternalNewsError("unsupported external news provider/schema")
        if self.executable is not False:
            raise ExternalNewsError("external news is reference-only")


def parse_finnhub_news(
    payload: Any,
    *,
    observed_at: str,
    known_markets: Iterable[str],
) -> list[NewsReferenceSignal]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ExternalNewsError("Finnhub response is invalid JSON") from exc
    if not isinstance(payload, list):
        raise ExternalNewsError("Finnhub response must be a list")
    observed = _timestamp(observed_at, "observed_at")
    signals: list[NewsReferenceSignal] = []
    identities: set[str] = set()
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        try:
            title = _clean_text(item.get("headline"), "headline", limit=500)
            source = _clean_text(item.get("source"), "source", limit=120)
            url = _safe_url(item.get("url"))
            article_id = str(item.get("id", "")).strip()
            epoch = item.get("datetime")
            if not isinstance(epoch, (int, float)) or epoch <= 0:
                raise ExternalNewsError("Finnhub datetime must be a positive epoch")
            published = datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            markets = _affected_markets(title, known_markets)
            identity = _identity(article_id)
            if not article_id or identity in identities:
                continue
            identities.add(identity)
            published_dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
            observed_dt = datetime.fromisoformat(observed.replace("Z", "+00:00"))
            signed_age_seconds = int((observed_dt - published_dt).total_seconds())
            if signed_age_seconds < -MAX_FUTURE_SKEW_SECONDS:
                continue
            if signed_age_seconds > MAX_ARTICLE_AGE_SECONDS:
                continue
            age_seconds = max(0, signed_age_seconds)
            score = 20 + (35 if markets else 0) + (20 if age_seconds <= 3600 else 0)
            signals.append(
                NewsReferenceSignal(
                    observed_at=observed,
                    published_at=published,
                    title=title,
                    source_name=source,
                    url=url,
                    provider_article_id=article_id,
                    affected_markets=markets,
                    identity_sha256=identity,
                    reference_score=min(score, 100),
                )
            )
        except (ExternalNewsError, OverflowError, OSError, ValueError):
            continue
    return sorted(signals, key=lambda signal: (signal.published_at, signal.identity_sha256), reverse=True)


class FinnhubNewsClient:
    """Small read-only client that keeps the API token out of URLs and errors."""

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 10.0,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        if not api_key or any(character.isspace() for character in api_key):
            raise ExternalNewsError("Finnhub API key is missing or invalid")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._opener = opener

    def fetch(self) -> Any:
        request = urllib.request.Request(
            FINNHUB_ENDPOINT,
            headers={
                "Accept": "application/json",
                "User-Agent": "bithumb-coin-trader/0.1",
                "X-Finnhub-Token": self._api_key,
            },
            method="GET",
        )
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                body = response.read(2_000_001)
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code == 429:
                raise ExternalNewsError("Finnhub rate limit exceeded") from None
            raise ExternalNewsError(f"Finnhub HTTP failure ({exc.code})") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ExternalNewsError("Finnhub request failed") from None
        if len(body) > 2_000_000:
            raise ExternalNewsError("Finnhub response is too large")
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExternalNewsError("Finnhub response is invalid JSON") from exc


def _decode(raw: bytes) -> list[NewsReferenceSignal]:
    if raw and not raw.endswith(b"\n"):
        raise ExternalNewsError("news store has an unterminated record")
    signals: list[NewsReferenceSignal] = []
    identities: set[str] = set()
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        try:
            payload = json.loads(line)
            signal = NewsReferenceSignal(**payload)
        except (json.JSONDecodeError, TypeError, ExternalNewsError) as exc:
            raise ExternalNewsError(f"news line {line_number} is invalid") from exc
        if signal.identity_sha256 in identities:
            raise ExternalNewsError(f"news line {line_number} duplicates an identity")
        identities.add(signal.identity_sha256)
        signals.append(signal)
    return signals


def _read_descriptor(descriptor: int) -> list[NewsReferenceSignal]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while chunk := os.read(descriptor, 1024 * 1024):
        chunks.append(chunk)
    return _decode(b"".join(chunks))


def read_news_reference_signals(path: str | Path) -> list[NewsReferenceSignal]:
    source = Path(path)
    if not source.exists():
        return []
    descriptor = os.open(source, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        return _read_descriptor(descriptor)
    finally:
        os.close(descriptor)


def append_news_reference_signals(
    path: str | Path, signals: Sequence[NewsReferenceSignal]
) -> list[NewsReferenceSignal]:
    if not signals:
        return []
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        existing = _read_descriptor(descriptor)
        known = {signal.identity_sha256 for signal in existing}
        unique = [signal for signal in signals if signal.identity_sha256 not in known]
        deduplicated: list[NewsReferenceSignal] = []
        for signal in unique:
            if signal.identity_sha256 not in {item.identity_sha256 for item in deduplicated}:
                deduplicated.append(signal)
        if not deduplicated:
            return []
        payload = "".join(_canonical_json(asdict(signal)) + "\n" for signal in deduplicated).encode("utf-8")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("news append made no progress")
            view = view[written:]
        os.fsync(descriptor)
        return deduplicated
    finally:
        os.close(descriptor)


def format_news_lines(signals: Sequence[NewsReferenceSignal], *, limit: int = 3) -> list[str]:
    def discord_text(value: str) -> str:
        escaped = value.replace("@", "@\u200b")
        for marker in ("\\", "`", "*", "_", "~", "[", "]"):
            escaped = escaped.replace(marker, f"\\{marker}")
        return escaped

    return [
        f"[외부 뉴스/참고] {discord_text(signal.title)} · "
        f"{discord_text(signal.source_name)} · <{signal.url}>"
        for signal in signals[: max(0, limit)]
    ]
