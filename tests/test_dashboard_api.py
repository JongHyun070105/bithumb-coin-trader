"""Integration tests for localhost-only read-only Dashboard API server."""

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from bithumb_coin_trader.dashboard_api import (
    DashboardApiError,
    run_dashboard_api,
)


@pytest.fixture
def valid_snapshot_file(tmp_path: Path):
    file_path = tmp_path / "valid_snapshot.json"
    data = {
        "schemaVersion": 1,
        "timestamp": "2026-09-08T08:00:00Z",
        "mode": "OFF",
        "source": {
            "kind": "local_snapshot",
            "label": "오프라인 FillLedger 스냅샷",
        },
        "portfolio": {
            "equity": 10000000.0,
            "cash": 8000000.0,
            "exposure": 2000000.0,
            "todayPnl": None,
            "todayReturnPct": None,
            "totalPnl": None,
            "totalReturnPct": None,
            "realizedPnl": 0.0,
            "unrealizedPnl": 0.0,
            "fees": 1000.0,
        },
        "positions": [],
        "recentTrades": [],
        "equityCurve": [],
        "dailyPerformance": [],
        "botStatus": {
            "mode": "OFF",
            "strategy": None,
            "marketData": "READY",
            "orderExecution": "DISABLED",
            "riskGuard": "ACTIVE",
            "lastActivity": "2026-09-08T08:00:00Z",
            "uptimeSeconds": 100,
            "todayTrades": 0,
            "errors": 0,
        },
        "performance": {
            "return7d": None,
            "return30d": None,
            "totalReturn": None,
            "maxDrawdown": None,
            "winRate": None,
            "profitFactor": None,
            "averageTrade": None,
        },
        "today": {
            "realizedPnl": None,
            "unrealizedPnl": None,
            "fees": None,
            "trades": None,
            "wins": None,
            "losses": None,
            "exposurePct": 20.0,
        },
        "dailyBaseline": None,
    }
    with file_path.open("w", encoding="utf-8") as f:
        json.dump(data, f)
    return file_path


@pytest.fixture
def live_server(valid_snapshot_file):
    # Port 0 lets the OS pick an available ephemeral port
    server = run_dashboard_api(valid_snapshot_file, host="127.0.0.1", port=0)
    actual_port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{actual_port}"
    yield base_url

    server.shutdown()
    server.server_close()


def _get(url: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data
    except urllib.error.HTTPError as e:
        data = json.loads(e.read().decode("utf-8"))
        return e.code, data


def _send_method(url: str, method: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data
    except urllib.error.HTTPError as e:
        data = json.loads(e.read().decode("utf-8"))
        return e.code, data


def test_api_health_endpoint(live_server):
    status, data = _get(f"{live_server}/api/health")
    assert status == 200
    assert data["status"] == "ok"
    assert data["readOnly"] is True
    assert data["schemaVersion"] == 1
    assert data["snapshotAvailable"] is True


def test_api_get_all_read_only_endpoints(live_server):
    endpoints = [
        "/api/trading/snapshot",
        "/api/portfolio",
        "/api/positions",
        "/api/trades",
        "/api/performance",
        "/api/bot/status",
    ]
    for ep in endpoints:
        status, data = _get(f"{live_server}{ep}")
        assert status == 200, f"Failed on endpoint {ep}"
        assert isinstance(data, (dict, list)), f"Bad data type for {ep}"


def test_api_rejection_of_mutation_methods(live_server):
    # Check POST, PUT, DELETE, PATCH all return 405 Method Not Allowed
    for method in ["POST", "PUT", "PATCH", "DELETE"]:
        status, data = _send_method(f"{live_server}/api/trading/snapshot", method)
        assert status == 405
        assert "메서드는 지원되지 않습니다" in data["error"]
        assert data["allowed"] == ["GET", "OPTIONS"]


def test_api_rejects_bind_to_all_interfaces(valid_snapshot_file):
    with pytest.raises(DashboardApiError, match="0.0.0.0 또는 외부 인터페이스 바인딩은 엄격히 금지됩니다"):
        run_dashboard_api(valid_snapshot_file, host="0.0.0.0", port=8765)


def test_api_rejects_external_hosts(valid_snapshot_file):
    for bad_host in ["192.168.1.10", "example.com", "ec2-1-2-3-4.compute.amazonaws.com"]:
        with pytest.raises(DashboardApiError, match="보안 위반"):
            run_dashboard_api(valid_snapshot_file, host=bad_host, port=8765)


def test_api_health_degraded_when_snapshot_corrupted(tmp_path: Path):
    corrupt_file = tmp_path / "corrupt.json"
    with corrupt_file.open("w", encoding="utf-8") as f:
        f.write("{ broken json")

    server = run_dashboard_api(corrupt_file, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base = f"http://127.0.0.1:{port}"
        status, data = _get(f"{base}/api/health")
        assert status == 200
        assert data["status"] == "degraded"
        assert data["snapshotAvailable"] is False

        # Endpoint returns 503 Service Unavailable when snapshot is corrupt
        snap_status, snap_data = _get(f"{base}/api/trading/snapshot")
        assert snap_status == 503
        assert "스냅샷 로드 불가" in snap_data["error"]
    finally:
        server.shutdown()
        server.server_close()
