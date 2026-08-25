"""Dependency-free, observation-only Bithumb WebSocket client foundation.

The module deliberately has no dependency on the execution, state, or fill
ledger layers.  Private stream events are observations: callers may use the
returned reconciliation hints to schedule an authoritative REST read, but a
WebSocket message never mutates trading state or submits an order.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import random
import re
import socket
import ssl
import struct
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol, TypeAlias
from urllib.parse import urlsplit


PUBLIC_URL = "wss://ws-api.bithumb.com/websocket/v1"
PRIVATE_URL = "wss://ws-api.bithumb.com/websocket/v2/private"
_MARKET_RE = re.compile(r"^[A-Z0-9]+-[A-Z0-9]+$")
_CURRENCY_RE = re.compile(r"^[A-Z0-9]{1,20}$")
_MAX_MESSAGE_BYTES = 4 * 1024 * 1024


class BithumbWebSocketError(RuntimeError):
    """Base class for WebSocket transport and observation failures."""


class WebSocketHandshakeError(BithumbWebSocketError):
    """The server did not complete a valid RFC 6455 upgrade."""


class WebSocketProtocolError(BithumbWebSocketError):
    """An invalid or unsupported RFC 6455 frame was received."""


class ObservationValidationError(BithumbWebSocketError):
    """A Bithumb stream message was malformed or unsafe to consume."""


def _markets(codes: Sequence[str], *, allow_empty: bool = False) -> list[str]:
    result = list(codes)
    if not result and not allow_empty:
        raise ValueError("at least one market code is required")
    if any(not isinstance(code, str) or not _MARKET_RE.fullmatch(code) for code in result):
        raise ValueError("market codes must be uppercase values such as KRW-BTC")
    if len(set(result)) != len(result):
        raise ValueError("market codes must be unique")
    return result


def _ticket(ticket: str | None) -> str:
    value = ticket or str(uuid.uuid4())
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 256:
        raise ValueError("ticket must be a non-empty string of at most 256 bytes")
    return value


def build_public_subscription(
    codes: Sequence[str],
    *,
    ticket: str | None = None,
    ticker: bool = True,
    orderbook: bool = True,
    realtime_only: bool = True,
) -> list[dict[str, Any]]:
    """Build the official Public v1 DEFAULT-format subscription payload."""

    markets = _markets(codes)
    streams: list[dict[str, Any]] = []
    for stream_type, enabled in (("ticker", ticker), ("orderbook", orderbook)):
        if enabled:
            stream: dict[str, Any] = {"type": stream_type, "codes": markets.copy()}
            if realtime_only:
                stream["is_only_realtime"] = True
            streams.append(stream)
    if not streams:
        raise ValueError("at least one public stream must be selected")
    return [{"ticket": _ticket(ticket)}, *streams, {"format": "DEFAULT"}]


def build_private_subscription(
    codes: Sequence[str] = (),
    *,
    ticket: str | None = None,
    my_order: bool = True,
    my_asset: bool = True,
) -> list[dict[str, Any]]:
    """Build the official Private v2 DEFAULT-format observation request."""

    markets = _markets(codes, allow_empty=True)
    streams: list[dict[str, Any]] = []
    if my_order:
        streams.append({"type": "myOrder", "codes": markets.copy()})
    if my_asset:
        streams.append({"type": "myAsset"})
    if not streams:
        raise ValueError("at least one private stream must be selected")
    return [{"ticket": _ticket(ticket)}, *streams, {"format": "DEFAULT"}]


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def create_hs256_jwt(
    access_key: str,
    secret_key: str,
    *,
    nonce: str | None = None,
    timestamp_ms: int | None = None,
) -> str:
    """Create the API 2.0 JWT used in the Private v2 Authorization header."""

    if not access_key or not secret_key:
        raise ValueError("access_key and secret_key are required")
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int) or timestamp_ms <= 0:
        raise ValueError("timestamp_ms must be a positive integer")
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "access_key": access_key,
        "nonce": nonce or str(uuid.uuid4()),
        "timestamp": timestamp_ms,
    }
    encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode())
    encoded_payload = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = hmac.new(secret_key.encode(), signing_input, hashlib.sha256).digest()
    return f"{signing_input.decode('ascii')}.{_b64url(signature)}"


@dataclass(frozen=True, slots=True)
class WebSocketFrame:
    fin: bool
    opcode: int
    payload: bytes


def encode_frame(
    payload: bytes | str,
    *,
    opcode: int = 0x1,
    mask: bool = True,
    mask_key: bytes | None = None,
) -> bytes:
    """Encode one RFC 6455 frame; client frames are masked by default."""

    raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    if opcode not in {0x0, 0x1, 0x2, 0x8, 0x9, 0xA}:
        raise ValueError("unsupported WebSocket opcode")
    if opcode >= 0x8 and len(raw) > 125:
        raise ValueError("control frame payload cannot exceed 125 bytes")
    key = mask_key if mask_key is not None else (os.urandom(4) if mask else b"")
    if mask and len(key) != 4:
        raise ValueError("mask_key must contain exactly four bytes")
    length = len(raw)
    marker = 0x80 if mask else 0
    if length < 126:
        header = bytes((0x80 | opcode, marker | length))
    elif length <= 0xFFFF:
        header = bytes((0x80 | opcode, marker | 126)) + struct.pack("!H", length)
    else:
        header = bytes((0x80 | opcode, marker | 127)) + struct.pack("!Q", length)
    if not mask:
        return header + raw
    masked = bytes(value ^ key[index % 4] for index, value in enumerate(raw))
    return header + key + masked


def decode_frame(data: bytes, *, require_unmasked: bool = False) -> tuple[WebSocketFrame, int]:
    """Decode one complete frame and return it with the consumed byte count."""

    if len(data) < 2:
        raise WebSocketProtocolError("incomplete frame header")
    first, second = data[0], data[1]
    if first & 0x70:
        raise WebSocketProtocolError("WebSocket extensions were not negotiated")
    fin, opcode, masked = bool(first & 0x80), first & 0x0F, bool(second & 0x80)
    if opcode not in {0x0, 0x1, 0x2, 0x8, 0x9, 0xA}:
        raise WebSocketProtocolError("unsupported WebSocket opcode")
    offset = 2
    length = second & 0x7F
    if length == 126:
        if len(data) < 4:
            raise WebSocketProtocolError("incomplete extended frame length")
        length, offset = struct.unpack("!H", data[2:4])[0], 4
    elif length == 127:
        if len(data) < 10:
            raise WebSocketProtocolError("incomplete extended frame length")
        length, offset = struct.unpack("!Q", data[2:10])[0], 10
        if length & (1 << 63):
            raise WebSocketProtocolError("invalid 64-bit frame length")
    if length > _MAX_MESSAGE_BYTES:
        raise WebSocketProtocolError("WebSocket frame exceeds observation size limit")
    if opcode >= 0x8 and (not fin or length > 125):
        raise WebSocketProtocolError("invalid control frame")
    if require_unmasked and masked:
        raise WebSocketProtocolError("server frames must not be masked")
    key = b""
    if masked:
        if len(data) < offset + 4:
            raise WebSocketProtocolError("incomplete masking key")
        key, offset = data[offset : offset + 4], offset + 4
    end = offset + length
    if len(data) < end:
        raise WebSocketProtocolError("incomplete frame payload")
    payload = data[offset:end]
    if masked:
        payload = bytes(value ^ key[index % 4] for index, value in enumerate(payload))
    return WebSocketFrame(fin=fin, opcode=opcode, payload=payload), end


class WebSocketTransport:
    """Small RFC 6455 TLS transport without permessage-deflate negotiation."""

    def __init__(self, *, timeout: float = 15.0, max_message_bytes: int = _MAX_MESSAGE_BYTES):
        if timeout <= 0 or max_message_bytes <= 0:
            raise ValueError("timeout and max_message_bytes must be positive")
        self.timeout = timeout
        self.max_message_bytes = max_message_bytes
        self._socket: socket.socket | None = None
        self._buffer = b""

    def connect(self, url: str, headers: Mapping[str, str] | None = None) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "wss" or not parsed.hostname:
            raise ValueError("only wss:// endpoints are supported")
        port = parsed.port or 443
        raw_socket = socket.create_connection((parsed.hostname, port), self.timeout)
        tls_socket = ssl.create_default_context().wrap_socket(
            raw_socket, server_hostname=parsed.hostname
        )
        tls_socket.settimeout(self.timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        request_headers = {
            "Host": parsed.hostname if port == 443 else f"{parsed.hostname}:{port}",
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Key": key,
            "Sec-WebSocket-Version": "13",
        }
        for name, value in (headers or {}).items():
            if "\r" in name or "\n" in name or "\r" in value or "\n" in value:
                tls_socket.close()
                raise ValueError("WebSocket headers cannot contain newlines")
            request_headers[name] = value
        request = f"GET {path} HTTP/1.1\r\n" + "".join(
            f"{name}: {value}\r\n" for name, value in request_headers.items()
        ) + "\r\n"
        try:
            tls_socket.sendall(request.encode("ascii"))
            response, remainder = self._read_http_headers(tls_socket)
            self._validate_handshake(response, key)
        except Exception:
            tls_socket.close()
            raise
        self._socket = tls_socket
        self._buffer = remainder

    @staticmethod
    def _read_http_headers(stream: socket.socket) -> tuple[bytes, bytes]:
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = stream.recv(4096)
            if not chunk:
                raise WebSocketHandshakeError("connection closed during handshake")
            response += chunk
            if len(response) > 64 * 1024:
                raise WebSocketHandshakeError("handshake headers are too large")
        headers, remainder = response.split(b"\r\n\r\n", 1)
        return headers + b"\r\n\r\n", remainder

    @staticmethod
    def _validate_handshake(response: bytes, key: str) -> None:
        header_block = response.split(b"\r\n\r\n", 1)[0]
        lines = header_block.decode("iso-8859-1").split("\r\n")
        if len(lines[0].split()) < 2 or lines[0].split()[1] != "101":
            raise WebSocketHandshakeError(f"upgrade rejected: {lines[0]}")
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode("ascii")
        if not hmac.compare_digest(headers.get("sec-websocket-accept", ""), expected):
            raise WebSocketHandshakeError("invalid Sec-WebSocket-Accept header")
        if "upgrade" not in headers.get("connection", "").lower():
            raise WebSocketHandshakeError("missing Connection: Upgrade response")
        if headers.get("upgrade", "").lower() != "websocket":
            raise WebSocketHandshakeError("missing Upgrade: websocket response")

    def send_text(self, text: str) -> None:
        self._require_socket().sendall(encode_frame(text))

    def send_json(self, value: Any) -> None:
        self.send_text(json.dumps(value, separators=(",", ":"), ensure_ascii=False))

    def send_ping(self, payload: bytes = b"health") -> None:
        """Send a masked client PING to keep an otherwise idle stream alive."""
        self._require_socket().sendall(encode_frame(payload, opcode=0x9))

    def receive_text(self) -> str:
        fragments: list[bytes] = []
        message_opcode: int | None = None
        while True:
            frame = self._receive_frame()
            if frame.opcode == 0x8:
                raise EOFError("WebSocket peer closed the connection")
            if frame.opcode == 0x9:
                self._require_socket().sendall(encode_frame(frame.payload, opcode=0xA))
                continue
            if frame.opcode == 0xA:
                continue
            if frame.opcode in {0x1, 0x2}:
                if message_opcode is not None:
                    raise WebSocketProtocolError("new data frame during fragmented message")
                message_opcode = frame.opcode
            elif frame.opcode == 0x0 and message_opcode is None:
                raise WebSocketProtocolError("unexpected continuation frame")
            fragments.append(frame.payload)
            if sum(map(len, fragments)) > self.max_message_bytes:
                raise WebSocketProtocolError("WebSocket message exceeds observation size limit")
            if not frame.fin:
                continue
            if message_opcode not in {0x1, 0x2}:
                raise WebSocketProtocolError("Bithumb observation has no data frame")
            try:
                return b"".join(fragments).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise WebSocketProtocolError("Bithumb data frame is not UTF-8 JSON") from exc

    def _receive_frame(self) -> WebSocketFrame:
        while True:
            try:
                frame, consumed = decode_frame(self._buffer, require_unmasked=True)
            except WebSocketProtocolError as exc:
                if not str(exc).startswith("incomplete"):
                    raise
                chunk = self._require_socket().recv(64 * 1024)
                if not chunk:
                    raise EOFError("WebSocket connection closed") from exc
                self._buffer += chunk
                if len(self._buffer) > self.max_message_bytes + 32:
                    raise WebSocketProtocolError("WebSocket receive buffer exceeds limit")
                continue
            self._buffer = self._buffer[consumed:]
            return frame

    def close(self) -> None:
        stream, self._socket = self._socket, None
        if stream is None:
            return
        try:
            stream.sendall(encode_frame(b"", opcode=0x8))
        except OSError:
            pass
        finally:
            stream.close()

    def _require_socket(self) -> socket.socket:
        if self._socket is None:
            raise BithumbWebSocketError("WebSocket transport is not connected")
        return self._socket


def _decimal(
    value: Any,
    field_name: str,
    *,
    zero_allowed: bool = True,
    negative_allowed: bool = False,
) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ObservationValidationError(f"{field_name} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ObservationValidationError(f"{field_name} must be numeric") from exc
    if (
        not result.is_finite()
        or (not negative_allowed and result < 0)
        or (not zero_allowed and result == 0)
    ):
        raise ObservationValidationError(f"{field_name} is outside its valid range")
    return result


def _integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ObservationValidationError(f"{field_name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ObservationValidationError(f"{field_name} must be an integer") from exc
    if result <= 0:
        raise ObservationValidationError(f"{field_name} must be positive")
    return result


def _market(value: Any) -> str:
    if not isinstance(value, str) or not _MARKET_RE.fullmatch(value):
        raise ObservationValidationError("code must be an uppercase market code")
    return value


@dataclass(frozen=True, slots=True)
class TickerObservation:
    code: str
    trade_price: Decimal
    trade_volume: Decimal
    timestamp_ms: int
    stream_type: str
    signed_change_rate: Decimal | None = None
    acc_trade_price_24h: Decimal | None = None


@dataclass(frozen=True, slots=True)
class OrderbookLevel:
    ask_price: Decimal
    bid_price: Decimal
    ask_size: Decimal
    bid_size: Decimal


@dataclass(frozen=True, slots=True)
class OrderbookObservation:
    code: str
    timestamp_us: int
    levels: tuple[OrderbookLevel, ...]
    stream_type: str


@dataclass(frozen=True, slots=True)
class MyOrderObservation:
    code: str
    order_id: str
    client_order_id: str | None
    side: Literal["buy", "sell"]
    order_type: str
    state: Literal["wait", "trade", "done", "cancel"]
    order_timestamp_ms: int
    timestamp_ms: int
    order_price: Decimal
    order_quantity: Decimal
    order_amount: Decimal
    trade_id: str | None = None
    trade_price: Decimal | None = None
    trade_quantity: Decimal | None = None
    executed_quantity: Decimal | None = None
    remaining_quantity: Decimal | None = None
    cancel_type: str | None = None


@dataclass(frozen=True, slots=True)
class AssetBalance:
    currency: str
    balance: Decimal
    locked: Decimal


@dataclass(frozen=True, slots=True)
class MyAssetObservation:
    assets: tuple[AssetBalance, ...]
    asset_timestamp_ms: int
    timestamp_ms: int
    stream_type: str


Observation: TypeAlias = (
    TickerObservation | OrderbookObservation | MyOrderObservation | MyAssetObservation
)


@dataclass(frozen=True, slots=True)
class ReconciliationHint:
    scope: Literal["order", "assets"]
    reason: str
    code: str | None = None
    order_id: str | None = None
    client_order_id: str | None = None


@dataclass(frozen=True, slots=True)
class ObservationEvent:
    observation: Observation
    received_at: datetime
    reconciliation_hints: tuple[ReconciliationHint, ...] = ()


def _optional_decimal(
    payload: Mapping[str, Any], name: str, *, negative_allowed: bool = False
) -> Decimal | None:
    return (
        _decimal(payload[name], name, negative_allowed=negative_allowed)
        if name in payload
        else None
    )


def _stream_type(payload: Mapping[str, Any], *, private: bool = False) -> str:
    value = payload.get("stream_type")
    allowed = {"REALTIME"} if private else {"SNAPSHOT", "REALTIME"}
    if value not in allowed:
        raise ObservationValidationError(f"stream_type must be one of {sorted(allowed)!r}")
    return value


def parse_observation(message: str | bytes | Mapping[str, Any]) -> ObservationEvent:
    """Validate one DEFAULT-format Bithumb message and produce read-side hints."""

    if isinstance(message, bytes):
        try:
            message = message.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ObservationValidationError("message is not UTF-8") from exc
    if isinstance(message, str):
        try:
            payload = json.loads(message)
        except json.JSONDecodeError as exc:
            raise ObservationValidationError("message is not valid JSON") from exc
    else:
        payload = dict(message)
    if not isinstance(payload, Mapping):
        raise ObservationValidationError("message must be a JSON object")
    message_type = payload.get("type")
    if message_type == "ticker":
        observation: Observation = TickerObservation(
            code=_market(payload.get("code")),
            trade_price=_decimal(payload.get("trade_price"), "trade_price", zero_allowed=False),
            trade_volume=_decimal(payload.get("trade_volume"), "trade_volume"),
            timestamp_ms=_integer(payload.get("timestamp"), "timestamp"),
            stream_type=_stream_type(payload),
            signed_change_rate=_optional_decimal(
                payload, "signed_change_rate", negative_allowed=True
            ),
            acc_trade_price_24h=_optional_decimal(payload, "acc_trade_price_24h"),
        )
        hints: tuple[ReconciliationHint, ...] = ()
    elif message_type == "orderbook":
        units = payload.get("orderbook_units")
        if not isinstance(units, list) or not units:
            raise ObservationValidationError("orderbook_units must be a non-empty list")
        levels = []
        for unit in units:
            if not isinstance(unit, Mapping):
                raise ObservationValidationError("orderbook unit must be an object")
            levels.append(
                OrderbookLevel(
                    ask_price=_decimal(unit.get("ask_price"), "ask_price", zero_allowed=False),
                    bid_price=_decimal(unit.get("bid_price"), "bid_price", zero_allowed=False),
                    ask_size=_decimal(unit.get("ask_size"), "ask_size"),
                    bid_size=_decimal(unit.get("bid_size"), "bid_size"),
                )
            )
        observation = OrderbookObservation(
            code=_market(payload.get("code")),
            timestamp_us=_integer(payload.get("timestamp"), "timestamp"),
            levels=tuple(levels),
            stream_type=_stream_type(payload),
        )
        hints = ()
    elif message_type == "myOrder":
        order_id = payload.get("order_id")
        side, state = payload.get("side"), payload.get("state")
        if not isinstance(order_id, str) or not order_id:
            raise ObservationValidationError("order_id must be a non-empty string")
        if side not in {"buy", "sell"}:
            raise ObservationValidationError("side must be buy or sell")
        if state not in {"wait", "trade", "done", "cancel"}:
            raise ObservationValidationError("state is not a supported Private v2 state")
        trade_id = payload.get("trade_id")
        if state == "trade" and (not isinstance(trade_id, str) or not trade_id):
            raise ObservationValidationError("trade state requires trade_id")
        client_order_id = payload.get("client_order_id")
        if client_order_id is not None and not isinstance(client_order_id, str):
            raise ObservationValidationError("client_order_id must be a string when present")
        order_type = payload.get("order_type")
        if order_type not in {"limit", "price", "market", "best"}:
            raise ObservationValidationError("order_type is not supported")
        if state == "trade":
            for required_trade_field in (
                "trade_price",
                "trade_quantity",
                "executed_quantity",
                "remaining_quantity",
            ):
                if required_trade_field not in payload:
                    raise ObservationValidationError(
                        f"trade state requires {required_trade_field}"
                    )
        observation = MyOrderObservation(
            code=_market(payload.get("code")),
            order_id=order_id,
            client_order_id=client_order_id,
            side=side,
            order_type=order_type,
            state=state,
            order_timestamp_ms=_integer(payload.get("order_timestamp"), "order_timestamp"),
            timestamp_ms=_integer(payload.get("timestamp"), "timestamp"),
            order_price=_decimal(payload.get("order_price"), "order_price"),
            order_quantity=_decimal(payload.get("order_quantity"), "order_quantity"),
            order_amount=_decimal(payload.get("order_amount"), "order_amount"),
            trade_id=trade_id,
            trade_price=_optional_decimal(payload, "trade_price"),
            trade_quantity=_optional_decimal(payload, "trade_quantity"),
            executed_quantity=_optional_decimal(payload, "executed_quantity"),
            remaining_quantity=_optional_decimal(payload, "remaining_quantity"),
            cancel_type=payload.get("cancel_type"),
        )
        hints = (
            ReconciliationHint(
                scope="order",
                reason=f"private_websocket_{state}",
                code=observation.code,
                order_id=observation.order_id,
                client_order_id=observation.client_order_id,
            ),
        )
    elif message_type == "myAsset":
        raw_assets = payload.get("assets")
        if not isinstance(raw_assets, list) or not raw_assets:
            raise ObservationValidationError("assets must be a non-empty list")
        assets = []
        for asset in raw_assets:
            if not isinstance(asset, Mapping):
                raise ObservationValidationError("asset must be an object")
            currency = asset.get("currency")
            if not isinstance(currency, str) or not _CURRENCY_RE.fullmatch(currency):
                raise ObservationValidationError("currency must be uppercase")
            assets.append(
                AssetBalance(
                    currency=currency,
                    balance=_decimal(asset.get("balance"), "balance"),
                    locked=_decimal(asset.get("locked"), "locked"),
                )
            )
        observation = MyAssetObservation(
            assets=tuple(assets),
            asset_timestamp_ms=_integer(payload.get("asset_timestamp"), "asset_timestamp"),
            timestamp_ms=_integer(payload.get("timestamp"), "timestamp"),
            stream_type=_stream_type(payload, private=True),
        )
        hints = (ReconciliationHint(scope="assets", reason="private_websocket_asset_change"),)
    else:
        raise ObservationValidationError(f"unsupported observation type: {message_type!r}")
    return ObservationEvent(
        observation=observation,
        received_at=datetime.now(timezone.utc),
        reconciliation_hints=hints,
    )


@dataclass(frozen=True, slots=True)
class WebSocketHealth:
    connected: bool = False
    reconnect_count: int = 0
    message_count: int = 0
    validation_error_count: int = 0
    last_connected_at: datetime | None = None
    last_message_at: datetime | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class ObservationSnapshot:
    tickers: Mapping[str, TickerObservation]
    orderbooks: Mapping[str, OrderbookObservation]
    orders: Mapping[str, MyOrderObservation]
    assets: Mapping[str, AssetBalance]
    health: WebSocketHealth


class ObservationCache:
    """Thread-safe, process-local cache of the latest validated observations."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tickers: dict[str, TickerObservation] = {}
        self._orderbooks: dict[str, OrderbookObservation] = {}
        self._orders: dict[str, MyOrderObservation] = {}
        self._assets: dict[str, AssetBalance] = {}
        self._health = WebSocketHealth()

    def record(self, event: ObservationEvent) -> None:
        with self._lock:
            observation = event.observation
            if isinstance(observation, TickerObservation):
                self._tickers[observation.code] = observation
            elif isinstance(observation, OrderbookObservation):
                self._orderbooks[observation.code] = observation
            elif isinstance(observation, MyOrderObservation):
                self._orders[observation.order_id] = observation
            else:
                for asset in observation.assets:
                    self._assets[asset.currency] = asset
            self._health = replace(
                self._health,
                message_count=self._health.message_count + 1,
                last_message_at=event.received_at,
                last_error=None,
            )

    def connected(self, at: datetime | None = None) -> None:
        now = at or datetime.now(timezone.utc)
        with self._lock:
            self._health = replace(
                self._health, connected=True, last_connected_at=now, last_error=None
            )

    def disconnected(self, error: BaseException | str) -> None:
        with self._lock:
            self._health = replace(
                self._health,
                connected=False,
                reconnect_count=self._health.reconnect_count + 1,
                last_error=str(error),
            )

    def validation_error(self, error: BaseException | str) -> None:
        with self._lock:
            self._health = replace(
                self._health,
                validation_error_count=self._health.validation_error_count + 1,
                last_error=str(error),
            )

    def snapshot(self) -> ObservationSnapshot:
        with self._lock:
            return ObservationSnapshot(
                tickers=dict(self._tickers),
                orderbooks=dict(self._orderbooks),
                orders=dict(self._orders),
                assets=dict(self._assets),
                health=self._health,
            )


@dataclass(frozen=True, slots=True)
class ReconnectBackoff:
    initial_seconds: float = 1.0
    maximum_seconds: float = 60.0
    multiplier: float = 2.0
    jitter_ratio: float = 0.2

    def __post_init__(self) -> None:
        if self.initial_seconds <= 0 or self.maximum_seconds < self.initial_seconds:
            raise ValueError("backoff bounds are invalid")
        if self.multiplier < 1 or not 0 <= self.jitter_ratio <= 1:
            raise ValueError("backoff multiplier or jitter is invalid")

    def delay(self, attempt: int, *, random_fraction: float = 0.5) -> float:
        if attempt < 0 or not 0 <= random_fraction <= 1:
            raise ValueError("attempt and random_fraction are outside their valid ranges")
        base = min(self.maximum_seconds, self.initial_seconds * self.multiplier**attempt)
        jitter = base * self.jitter_ratio * (2 * random_fraction - 1)
        return max(0.0, base + jitter)


class WebSocketClientTransport(Protocol):
    def connect(self, url: str, headers: Mapping[str, str] | None = None) -> None: ...
    def send_json(self, value: Any) -> None: ...
    def send_ping(self, payload: bytes = b"health") -> None: ...
    def receive_text(self) -> str: ...
    def close(self) -> None: ...


class TransportFactory(Protocol):
    def __call__(self) -> WebSocketClientTransport: ...


ObservationCallback: TypeAlias = Callable[[ObservationEvent], None]


class BithumbWebSocketObserver:
    """Reconnectable observation loop; never imports or calls execution code."""

    def __init__(
        self,
        subscription: Sequence[Mapping[str, Any]],
        *,
        private: bool = False,
        access_key: str | None = None,
        secret_key: str | None = None,
        callback: ObservationCallback | None = None,
        cache: ObservationCache | None = None,
        backoff: ReconnectBackoff | None = None,
        transport_factory: TransportFactory = WebSocketTransport,
    ) -> None:
        if private and (not access_key or not secret_key):
            raise ValueError("Private v2 requires API 2.0 access and secret keys")
        self.subscription = [dict(field) for field in subscription]
        self.private = private
        self.access_key = access_key
        self.secret_key = secret_key
        self.callback = callback
        self.cache = cache or ObservationCache()
        self.backoff = backoff or ReconnectBackoff()
        self.transport_factory = transport_factory

    def run_forever(self, stop_event: threading.Event) -> None:
        attempt = 0
        while not stop_event.is_set():
            transport = self.transport_factory()
            try:
                headers: dict[str, str] = {}
                if self.private:
                    assert self.access_key is not None and self.secret_key is not None
                    headers["Authorization"] = (
                        "Bearer " + create_hs256_jwt(self.access_key, self.secret_key)
                    )
                transport.connect(PRIVATE_URL if self.private else PUBLIC_URL, headers)
                transport.send_json(self.subscription)
                self.cache.connected()
                while not stop_event.is_set():
                    try:
                        event = parse_observation(transport.receive_text())
                    except socket.timeout:
                        transport.send_ping()
                        continue
                    except ObservationValidationError as exc:
                        self.cache.validation_error(exc)
                        continue
                    self.cache.record(event)
                    attempt = 0
                    if self.callback is not None:
                        self.callback(event)
            except (OSError, EOFError, BithumbWebSocketError) as exc:
                self.cache.disconnected(exc)
                if stop_event.wait(
                    self.backoff.delay(attempt, random_fraction=random.random())
                ):
                    break
                attempt += 1
            finally:
                transport.close()
