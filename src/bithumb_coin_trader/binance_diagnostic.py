"""Bounded, sanitized diagnostics for Binance public WebSocket connectivity."""

from __future__ import annotations

import asyncio
import os
import re
import socket
import ssl
import time
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlsplit
from urllib.request import getproxies

import websockets

from .cross_market_collector import BINANCE_WS_URL


_PRODUCTION_ENDPOINT = urlsplit(BINANCE_WS_URL)
BINANCE_HOST = _PRODUCTION_ENDPOINT.hostname or ""
BINANCE_PORT = _PRODUCTION_ENDPOINT.port or 443
OFFICIAL_BINANCE_PORTS = (443, 9443)
BINANCE_SYMBOLS = ("btcusdt", "ethusdt", "solusdt", "xrpusdt")
_PROXY_VARIABLES = (
    "WSS_PROXY",
    "wss_proxy",
    "WS_PROXY",
    "ws_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
)
_URL_USERINFO = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s]+@")


def sanitize_detail(value: object) -> str:
    """Remove URL userinfo and bound exception text before persistence."""
    return _URL_USERINFO.sub(r"\g<scheme>[REDACTED]@", str(value))[:500]


def _proxy_descriptor(value: str) -> dict[str, object]:
    parsed = urlsplit(value)
    descriptor: dict[str, object] = {"present": True}
    if parsed.scheme:
        descriptor["scheme"] = parsed.scheme.lower()
    if parsed.hostname:
        descriptor["host"] = parsed.hostname
    try:
        if parsed.port is not None:
            descriptor["port"] = parsed.port
    except ValueError:
        descriptor["port_valid"] = False
    return descriptor


def collect_proxy_metadata(
    environ: Mapping[str, str] | None = None,
    *,
    system_proxies: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return proxy routing metadata without values that may contain credentials."""
    source = os.environ if environ is None else environ
    environment: dict[str, object] = {}
    for name in _PROXY_VARIABLES:
        if name not in source:
            continue
        value = source[name]
        environment[name] = (
            {"present": True} if name.lower() == "no_proxy" else _proxy_descriptor(value)
        )
    detected = getproxies() if system_proxies is None else system_proxies
    sanitized_detected = {
        str(name): _proxy_descriptor(str(value))
        for name, value in sorted(detected.items())
    }
    return {"environment": environment, "getproxies": sanitized_detected}


def resolve_addresses(host: str = BINANCE_HOST, port: int = BINANCE_PORT) -> list[dict[str, object]]:
    """Resolve unique stream endpoint candidates while retaining address family."""
    candidates: list[dict[str, object]] = []
    seen: set[tuple[int, str]] = set()
    for family, socktype, proto, _canonname, sockaddr in socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    ):
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        address = str(sockaddr[0])
        key = (family, address)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "family": "IPv4" if family == socket.AF_INET else "IPv6",
                "address": address,
                "sockaddr": sockaddr,
                "socktype": socktype,
                "protocol": proto,
            }
        )
    return candidates


def _probe_tcp_tls_sync(candidate: Mapping[str, object], timeout: float) -> dict[str, object]:
    family_name = str(candidate["family"])
    family = socket.AF_INET if family_name == "IPv4" else socket.AF_INET6
    result: dict[str, object] = {"family": family_name, "address": candidate["address"]}
    raw_socket = socket.socket(family, socket.SOCK_STREAM)
    raw_socket.settimeout(timeout)
    started = time.monotonic()
    try:
        raw_socket.connect(candidate["sockaddr"])  # type: ignore[arg-type]
        result["tcp"] = {
            "status": "PASS",
            "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
        }
    except Exception as error:
        result["tcp"] = {
            "status": "FAIL",
            "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
            "exception_class": type(error).__name__,
            "exception_message": sanitize_detail(error),
        }
        result["tls"] = {"status": "NOT_RUN"}
        raw_socket.close()
        return result

    tls_started = time.monotonic()
    try:
        context = ssl.create_default_context()
        with context.wrap_socket(
            raw_socket,
            server_hostname=BINANCE_HOST,
            do_handshake_on_connect=False,
        ) as tls_socket:
            tls_socket.settimeout(timeout)
            tls_socket.do_handshake()
            result["tls"] = {
                "status": "PASS",
                "elapsed_ms": round((time.monotonic() - tls_started) * 1000.0, 3),
                "version": tls_socket.version(),
            }
    except Exception as error:
        result["tls"] = {
            "status": "FAIL",
            "elapsed_ms": round((time.monotonic() - tls_started) * 1000.0, 3),
            "exception_class": type(error).__name__,
            "exception_message": sanitize_detail(error),
        }
        raw_socket.close()
    return result


async def probe_tcp_tls(candidate: Mapping[str, object], timeout: float) -> dict[str, object]:
    return await asyncio.to_thread(_probe_tcp_tls_sync, candidate, timeout)


def _peer_family(peer: object) -> tuple[str | None, str | None]:
    if not isinstance(peer, tuple) or not peer:
        return None, None
    address = str(peer[0])
    try:
        family = "IPv6" if socket.inet_pton(socket.AF_INET6, address) else None
    except OSError:
        try:
            socket.inet_pton(socket.AF_INET, address)
            family = "IPv4"
        except OSError:
            family = None
    return family, address


async def probe_websocket(uri: str, proxy_mode: str, timeout: float) -> dict[str, object]:
    """Measure opening upgrade in default proxy-auto or forced-direct mode."""
    if proxy_mode not in {"auto", "direct"}:
        raise ValueError("proxy_mode must be auto or direct")
    started = time.monotonic()
    kwargs: dict[str, object] = {
        "open_timeout": timeout,
        "close_timeout": min(timeout, 5.0),
        "ping_interval": None,
        "proxy": True if proxy_mode == "auto" else None,
    }
    try:
        async with websockets.connect(uri, **kwargs) as connection:
            family, address = _peer_family(connection.remote_address)
            return {
                "status": "PASS",
                "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
                "selected_address_family": family,
                "peer_address": address,
            }
    except Exception as error:
        return {
            "status": "FAIL",
            "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
            "selected_address_family": None,
            "exception_class": type(error).__name__,
            "exception_message": sanitize_detail(error),
        }


async def run_diagnostic(
    *,
    timeout: float = 10.0,
    port: int = BINANCE_PORT,
    resolver: Callable[[str, int], list[dict[str, object]]] = resolve_addresses,
    transport_probe: Callable[[Mapping[str, object], float], Awaitable[dict[str, object]]] = probe_tcp_tls,
    websocket_probe: Callable[[str, str, float], Awaitable[dict[str, object]]] = probe_websocket,
    environ: Mapping[str, str] | None = None,
    system_proxies: Mapping[str, str] | None = None,
) -> dict[str, object]:
    if timeout <= 0 or timeout > 30:
        raise ValueError("timeout must be greater than zero and at most 30 seconds")
    if port not in OFFICIAL_BINANCE_PORTS:
        raise ValueError("port must be an official Binance stream port: 443 or 9443")
    started_at = time.time()
    try:
        candidates = resolver(BINANCE_HOST, port)
        dns: dict[str, object] = {"status": "PASS" if candidates else "FAIL", "candidates": candidates}
    except Exception as error:
        candidates = []
        dns = {
            "status": "FAIL",
            "candidates": [],
            "exception_class": type(error).__name__,
            "exception_message": sanitize_detail(error),
        }
    transport = [await transport_probe(candidate, timeout) for candidate in candidates]
    attempts: list[dict[str, object]] = []
    base = f"wss://{BINANCE_HOST}:{port}"
    symbol_uris = [(symbol, f"{base}/ws/{symbol}@trade") for symbol in BINANCE_SYMBOLS]
    combined_streams = "/".join(
        [f"{symbol}@trade" for symbol in BINANCE_SYMBOLS]
        + [f"{symbol}@depth20@100ms" for symbol in BINANCE_SYMBOLS]
    )
    for proxy_mode in ("auto", "direct"):
        for symbol, uri in symbol_uris:
            outcome = await websocket_probe(uri, proxy_mode, timeout)
            attempts.append({"kind": "symbol", "symbol": symbol.upper(), "proxy_mode": proxy_mode, "uri": uri, **outcome})
        combined_uri = f"{base}/stream?streams={combined_streams}"
        outcome = await websocket_probe(combined_uri, proxy_mode, timeout)
        attempts.append({"kind": "production_combined", "symbol": None, "proxy_mode": proxy_mode, "uri": combined_uri, **outcome})
    symbol_attempts = [attempt for attempt in attempts if attempt["kind"] == "symbol"]
    return {
        "schema_version": 1,
        "target": {"host": BINANCE_HOST, "port": port},
        "websockets_version": websockets.__version__,
        "started_at_unix": started_at,
        "elapsed_ms": round((time.time() - started_at) * 1000.0, 3),
        "proxy": collect_proxy_metadata(environ, system_proxies=system_proxies),
        "dns": dns,
        "transport": transport,
        "websocket_attempts": attempts,
        "all_symbol_handshakes_passed": bool(symbol_attempts)
        and all(attempt["status"] == "PASS" for attempt in symbol_attempts),
        "production_combined_passed": all(
            attempt["status"] == "PASS"
            for attempt in attempts
            if attempt["kind"] == "production_combined"
        ),
    }
