"""Minimal, dependency-free MCP client for the official Bithumb stdio server."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO


_PACKAGE = "@bithumb-official/bithumb-mcp@0.8.5"
DEFAULT_COMMAND = (
    "npx",
    "-y",
    _PACKAGE,
    "--modules",
    "account",
    "--read-only",
)
LIVE_COMMAND = (
    "npx",
    "-y",
    _PACKAGE,
    "--modules",
    "account,trade",
)
ALLOWED_CHILD_ENV = frozenset(
    {"PATH", "HOME", "BITHUMB_ACCESS_KEY", "BITHUMB_SECRET_KEY"}
)


def minimal_child_env(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    if overrides is not None and set(overrides) - ALLOWED_CHILD_ENV:
        unexpected = sorted(set(overrides) - ALLOWED_CHILD_ENV)
        raise ValueError(f"unsupported MCP child environment keys: {unexpected!r}")
    child_env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": os.environ.get("HOME", str(Path.home())),
    }
    for name in ("BITHUMB_ACCESS_KEY", "BITHUMB_SECRET_KEY"):
        if name in os.environ:
            child_env[name] = os.environ[name]
    if overrides is not None:
        child_env.update(overrides)
    return child_env

# This is deliberately an allow-list. Adding a new server tool does not silently
# grant it access through the read-only API.
READ_ONLY_TOOLS = frozenset(
    {
        "account_get_assets",
        "account_get_api_keys",
        "account_get_order_chance",
        "account_get_wallet_status",
        "trade_get_order",
        "trade_get_orders",
    }
)
WRITE_TOOLS = frozenset({"trade_place_order"})


class McpError(RuntimeError):
    """Base error raised by the MCP client."""


class McpProtocolError(McpError):
    """The server returned malformed or mismatched JSON-RPC data."""


class McpToolError(McpError):
    """The MCP server rejected a request or a tool reported an error."""


class UnsafeToolError(McpError):
    """A mutating tool was passed to the read-only call boundary."""


class McpStdioClient:
    """Synchronous JSON-RPC client using MCP's newline-delimited stdio transport."""

    def __init__(
        self,
        command: Sequence[str] = DEFAULT_COMMAND,
        *,
        env: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not command:
            raise ValueError("command must not be empty")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        minimal_child_env(env)
        self.command = tuple(command)
        self.env = dict(env) if env is not None else None
        self.timeout = timeout
        self._process: subprocess.Popen[str] | None = None
        self._request_id = 0
        self._lock = threading.Lock()
        self._stderr_lines: list[str] = []
        self._stderr_thread: threading.Thread | None = None

    def __enter__(self) -> McpStdioClient:
        self.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def stderr(self) -> str:
        return "".join(self._stderr_lines)

    def start(self) -> None:
        if self.is_running:
            return
        child_env = minimal_child_env(self.env)
        self._process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=child_env,
        )
        assert self._process.stderr is not None
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            args=(self._process.stderr,),
            daemon=True,
        )
        self._stderr_thread.start()
        try:
            self.request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "bithumb-coin-trader",
                        "version": "0.1.0",
                    },
                },
            )
            self.notify("notifications/initialized")
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        if not method:
            raise ValueError("method must not be empty")
        with self._lock:
            process = self._require_process()
            self._request_id += 1
            request_id = self._request_id
            message: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
            }
            if params is not None:
                message["params"] = dict(params)
            self._write_message(process, message)

            while True:
                response = self._read_message(process)
                # Ignore server notifications while waiting for our response.
                if "id" not in response:
                    continue
                if response["id"] != request_id:
                    raise McpProtocolError(
                        f"unexpected response id {response['id']!r}; expected {request_id!r}"
                    )
                if "error" in response:
                    error = response["error"]
                    if isinstance(error, Mapping):
                        detail = error.get("message", repr(error))
                    else:
                        detail = repr(error)
                    raise McpToolError(f"MCP {method} failed: {detail}")
                if "result" not in response:
                    raise McpProtocolError("JSON-RPC response has no result or error")
                return response["result"]

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = dict(params)
        with self._lock:
            self._write_message(self._require_process(), message)

    def list_tools(self) -> list[dict[str, Any]]:
        result = self.request("tools/list", {})
        if not isinstance(result, Mapping) or not isinstance(result.get("tools"), list):
            raise McpProtocolError("tools/list returned an invalid payload")
        return result["tools"]

    def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> Any:
        if name not in WRITE_TOOLS:
            raise UnsafeToolError(f"tool {name!r} is not approved for write calls")
        return self._call_tool(name, arguments)

    def _call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> Any:
        result = self.request(
            "tools/call",
            {"name": name, "arguments": dict(arguments or {})},
        )
        if isinstance(result, Mapping) and result.get("isError") is True:
            raise McpToolError(f"MCP tool {name!r} reported an error: {result.get('content')!r}")
        return result

    def call_read_tool(
        self, name: str, arguments: Mapping[str, Any] | None = None
    ) -> Any:
        if name not in READ_ONLY_TOOLS:
            raise UnsafeToolError(f"tool {name!r} is not approved for read-only calls")
        return self._call_tool(name, arguments)

    def _require_process(self) -> subprocess.Popen[str]:
        if not self.is_running:
            raise McpError("MCP server is not running; call start() first")
        assert self._process is not None
        return self._process

    def _write_message(
        self, process: subprocess.Popen[str], message: Mapping[str, Any]
    ) -> None:
        if process.stdin is None:
            raise McpError("MCP server stdin is unavailable")
        try:
            process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise McpError(f"failed to write to MCP server: {exc}") from exc

    def _read_message(self, process: subprocess.Popen[str]) -> dict[str, Any]:
        if process.stdout is None:
            raise McpError("MCP server stdout is unavailable")
        selector = selectors.DefaultSelector()
        try:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(self.timeout):
                raise McpError(f"MCP response timed out after {self.timeout:g}s")
            line = process.stdout.readline()
        finally:
            selector.close()
        if not line:
            code = process.poll()
            raise McpError(
                f"MCP server closed stdout (exit={code}); stderr={self.stderr[-1000:]!r}"
            )
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise McpProtocolError(f"invalid JSON from MCP server: {line[:200]!r}") from exc
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise McpProtocolError("MCP response is not a JSON-RPC 2.0 object")
        return message

    def _drain_stderr(self, stream: TextIO) -> None:
        for line in stream:
            self._stderr_lines.append(line)
            if len(self._stderr_lines) > 200:
                del self._stderr_lines[:100]
