"""Localhost-only read-only HTTP API server for Bithumb Coin Trader Dashboard.

STRICT SAFETY BOUNDARIES:
- Localhost only (127.0.0.1, localhost, ::1). Explicitly rejects 0.0.0.0 or external interfaces.
- Read-only endpoints only (GET /api/*).
- Mutation methods (POST, PUT, PATCH, DELETE) unconditionally return 405 Method Not Allowed.
- Zero external network connections, zero exchange API connections, zero AWS access.
- Validates snapshot schemaVersion == 1 before serving.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
API_VERSION = "0.3.0"
SUPPORTED_SCHEMA_VERSION = 1


class DashboardApiError(ValueError):
    """Raised when server configuration is unsafe or invalid."""


class SnapshotCache:
    """Thread-safe snapshot reader caching by file mtime."""

    def __init__(self, snapshot_path: Path) -> None:
        self.path = Path(snapshot_path)
        self._cached_data: dict[str, Any] | None = None
        self._cached_mtime: float = -1.0
        self._load_error: str | None = None

    def get_snapshot(self) -> tuple[dict[str, Any] | None, str | None, float]:
        """Returns (snapshot_data, error_message, last_modified_timestamp)."""
        if not self.path.exists():
            return None, f"스냅샷 파일이 존재하지 않습니다: {self.path}", 0.0

        try:
            current_mtime = self.path.stat().st_mtime
            if self._cached_data is not None and current_mtime == self._cached_mtime:
                return self._cached_data, None, current_mtime

            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                self._load_error = "스냅샷 루트가 JSON 객체가 아닙니다."
                return None, self._load_error, current_mtime

            schema_ver = data.get("schemaVersion") or data.get("schema_version")
            if schema_ver != SUPPORTED_SCHEMA_VERSION:
                self._load_error = f"지원하지 않는 스키마 버전입니다 (지원: {SUPPORTED_SCHEMA_VERSION}, 실제: {schema_ver})"
                return None, self._load_error, current_mtime

            required_keys = {"timestamp", "mode", "source", "portfolio", "positions"}
            missing = required_keys - set(data.keys())
            if missing:
                self._load_error = f"필수 최상위 필드 누락: {', '.join(sorted(missing))}"
                return None, self._load_error, current_mtime

            self._cached_data = data
            self._cached_mtime = current_mtime
            self._load_error = None
            return self._cached_data, None, current_mtime

        except json.JSONDecodeError as exc:
            self._load_error = f"스냅샷 JSON 파싱 오류: {exc}"
            return None, self._load_error, 0.0
        except Exception as exc:
            self._load_error = f"스냅샷 파일 읽기 실패: {exc}"
            return None, self._load_error, 0.0


def create_handler_class(cache: SnapshotCache):
    class DashboardApiHandler(BaseHTTPRequestHandler):
        server_version = f"BithumbCoinTrader-LocalApi/{API_VERSION}"

        def _send_json(self, status_code: int, payload: Mapping[str, Any] | Sequence[Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            # Allow local browser development access (e.g. Vite on 4177 or 5173 connecting to 8765)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Accept, Content-Type")
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Accept, Content-Type")
            self.send_header("Access-Control-Max-Age", "86400")
            self.end_headers()

        def do_GET(self) -> None:
            clean_path = self.path.split("?")[0].rstrip("/")
            if clean_path == "":
                clean_path = "/"

            if clean_path == "/api/health":
                data, err, mtime = cache.get_snapshot()
                status = "ok" if err is None and data is not None else "degraded"
                health_payload = {
                    "status": status,
                    "version": API_VERSION,
                    "readOnly": True,
                    "snapshotAvailable": data is not None,
                    "schemaVersion": SUPPORTED_SCHEMA_VERSION,
                    "lastModified": (
                        Path(cache.path).stat().st_mtime if cache.path.exists() else None
                    ),
                    "error": err,
                }
                status_code = HTTPStatus.OK if status == "ok" else HTTPStatus.OK
                self._send_json(status_code, health_payload)
                return

            data, err, _ = cache.get_snapshot()
            if err is not None or data is None:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": f"스냅샷 로드 불가: {err}", "status": "degraded"},
                )
                return

            routes = {
                "/api/trading/snapshot": lambda: data,
                "/api/portfolio": lambda: data.get("portfolio", {}),
                "/api/positions": lambda: data.get("positions", []),
                "/api/trades": lambda: data.get("recentTrades", []),
                "/api/performance": lambda: data.get("performance", {}),
                "/api/bot/status": lambda: data.get("botStatus", {}),
            }

            if clean_path in routes:
                self._send_json(HTTPStatus.OK, routes[clean_path]())
            else:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error": f"존재하지 않는 엔드포인트: {clean_path}"},
                )

        # Reject all mutation methods unconditionally
        def do_POST(self) -> None:
            self._reject_mutation()

        def do_PUT(self) -> None:
            self._reject_mutation()

        def do_PATCH(self) -> None:
            self._reject_mutation()

        def do_DELETE(self) -> None:
            self._reject_mutation()

        def _reject_mutation(self) -> None:
            self._send_json(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {
                    "error": f"{self.command} 메서드는 지원되지 않습니다. 읽기 전용(GET) API입니다.",
                    "allowed": ["GET", "OPTIONS"],
                },
            )

        def log_message(self, format: str, *args: Any) -> None:
            # Suppress normal verbose request stdout unless debugging
            if os.environ.get("DASHBOARD_API_DEBUG") == "1":
                super().log_message(format, *args)

    return DashboardApiHandler


def run_dashboard_api(
    snapshot_path: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    """Initialize and start the localhost-only ThreadingHTTPServer."""
    host_clean = host.strip().lower()
    if host_clean not in ALLOWED_HOSTS:
        raise DashboardApiError(
            f"보안 위반: 서버는 localhost({', '.join(sorted(ALLOWED_HOSTS))})에서만 바인딩할 수 있습니다. "
            f"(입력 호스트: {host!r}). 0.0.0.0 또는 외부 인터페이스 바인딩은 엄격히 금지됩니다."
        )

    cache = SnapshotCache(snapshot_path)
    handler_class = create_handler_class(cache)
    server = ThreadingHTTPServer((host_clean, port), handler_class)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Localhost-only read-only trading dashboard API server.")
    parser.add_argument(
        "--snapshot",
        required=True,
        type=Path,
        help="Path to TradingSnapshot JSON file.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address to bind. Must be 127.0.0.1, localhost, or ::1 (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        default=8765,
        type=int,
        help="Port to listen on (default: 8765).",
    )

    args = parser.parse_args()

    try:
        server = run_dashboard_api(
            snapshot_path=args.snapshot,
            host=args.host,
            port=args.port,
        )
        print(f"Bithumb Coin Trader Local API running on http://{args.host}:{args.port}")
        print(f"Serving snapshot: {args.snapshot}")
        print("Press Ctrl+C to terminate.")
        server.serve_forever()
    except DashboardApiError as exc:
        print(f"API Server Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nShutting down local API server...")
        sys.exit(0)


if __name__ == "__main__":
    main()
