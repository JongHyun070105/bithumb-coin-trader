"""Pytest configuration — TEST-HARNESS NETWORK BLOCK.

SCOPE: This is a test-harness network block, NOT an OS-level sandbox.
It blocks common Python-level network paths used in this codebase.
It does NOT prevent: subprocess curl/wget/aws calls, C extension sockets,
async CFFI transports, or OS-level circumvention.

Label: TEST-HARNESS NETWORK BLOCK
NOT: NETWORK IMPOSSIBLE
"""
from __future__ import annotations

import socket
import subprocess
import sys
import pytest


# ─── socket-level block ─────────────────────────────────────────────────────

_orig_connect = socket.socket.connect
_orig_connect_ex = socket.socket.connect_ex

_ALLOWED_HOSTS = frozenset({
    "localhost",
    "127.0.0.1",
    "::1",
})


def _blocked_connect(self, address):
    """Block all non-loopback TCP/UDP connections."""
    if isinstance(address, tuple):
        host = address[0]
        if host in _ALLOWED_HOSTS:
            return _orig_connect(self, address)
    raise RuntimeError(
        f"[TEST-HARNESS NETWORK BLOCK] socket.connect() is forbidden during offline tests. "
        f"Attempted connect to {address!r}. "
        f"This is a test-harness block only — not an OS-level sandbox."
    )


def _blocked_connect_ex(self, address):
    if isinstance(address, tuple):
        host = address[0]
        if host in _ALLOWED_HOSTS:
            return _orig_connect_ex(self, address)
    raise RuntimeError(
        f"[TEST-HARNESS NETWORK BLOCK] socket.connect_ex() is forbidden during offline tests. "
        f"Attempted connect to {address!r}."
    )


# Also block socket.create_connection used by http.client, requests, etc.
_orig_create_connection = socket.create_connection


def _blocked_create_connection(address, *args, **kwargs):
    if isinstance(address, tuple):
        host = address[0]
        if host in _ALLOWED_HOSTS:
            return _orig_create_connection(address, *args, **kwargs)
    raise RuntimeError(
        f"[TEST-HARNESS NETWORK BLOCK] socket.create_connection() is forbidden. "
        f"Attempted connection to {address!r}."
    )


@pytest.fixture(autouse=True)
def offline_network_guard(monkeypatch):
    """TEST-HARNESS NETWORK BLOCK: blocks socket-level outbound connections.

    This fixture is autouse=True so all tests are covered.

    SCOPE: Blocks socket.connect, socket.connect_ex, socket.create_connection.
    Does NOT block: subprocess, C extensions, async CFFI transports.
    Tests using subprocess network tools (curl, wget, aws) are separately blocked
    by _blocked_subprocess (optional fixture).
    """
    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_connect_ex)
    monkeypatch.setattr(socket, "create_connection", _blocked_create_connection)


# ─── subprocess network block (opt-in) ──────────────────────────────────────

_BLOCKED_NETWORK_TOOLS = frozenset({
    "curl", "wget", "aws", "aws2", "nc", "ncat", "python3 -c", "python -c"
})


@pytest.fixture
def block_subprocess_network(monkeypatch):
    """Optional fixture: blocks subprocess calls to known network tools.

    Use in tests that concern themselves with subprocess-level isolation.
    Does NOT block general subprocess usage (needed for test helpers).
    """
    _orig_run = subprocess.run
    _orig_popen = subprocess.Popen

    def _blocked_run(args, **kwargs):
        if isinstance(args, (list, tuple)) and args:
            cmd = str(args[0]).lower()
            for blocked in ("curl", "wget", "aws"):
                if blocked in cmd:
                    raise RuntimeError(
                        f"[TEST-HARNESS NETWORK BLOCK] subprocess call to '{args[0]}' is blocked."
                    )
        return _orig_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _blocked_run)
