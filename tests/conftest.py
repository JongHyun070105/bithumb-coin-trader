"""Pytest configuration and strict offline network isolation guard."""

import socket
import pytest

_orig_connect = socket.socket.connect


def _forbidden_connect(self, address):
    raise RuntimeError(
        f"Network connection strictly forbidden during offline tests! Attempted connect to {address}"
    )


@pytest.fixture(autouse=True)
def offline_network_guard(monkeypatch):
    """Guarantees that no test can accidentally connect to any external host."""
    monkeypatch.setattr(socket.socket, "connect", _forbidden_connect)
