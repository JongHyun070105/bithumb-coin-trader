"""Integration tests for localhost-only read-only Dashboard API server."""

import concurrent.futures
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from bithumb_coin_trader.dashboard_api import (
    DashboardApiError,
    SnapshotCache,
    run_dashboard_api,
)


@pytest.fixture
def valid_snapshot_data():
    return {
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


@pytest.fixture
def valid_snapshot_file(tmp_path: Path, valid_snapshot_data):
    file_path = tmp_path / "valid_snapshot.json"
    with file_path.open("w", encoding="utf-8") as f:
        json.dump(valid_snapshot_data, f)
    return file_path


@pytest.fixture
def live_server(valid_snapshot_file):
    server = run_dashboard_api(valid_snapshot_file, host="127.0.0.1", port=0)
    actual_port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{actual_port}"
    yield base_url

    server.shutdown()
    server.server_close()


def _request(url: str, method: str = "GET", headers: dict | None = None) -> tuple[int, dict, dict]:
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8")) if resp.length != 0 else {}
            resp_headers = dict(resp.headers)
            return resp.status, data, resp_headers
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        data = json.loads(body) if body.strip() else {}
        resp_headers = dict(e.headers)
        return e.code, data, resp_headers


def _get(url: str, headers: dict | None = None) -> tuple[int, dict]:
    status, data, _ = _request(url, method="GET", headers=headers)
    return status, data


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
    for method in ["POST", "PUT", "PATCH", "DELETE"]:
        status, data, _ = _request(f"{live_server}/api/trading/snapshot", method=method)
        assert status == 405
        assert "메서드는 지원되지 않습니다" in data["error"]
        assert data["allowed"] == ["GET", "OPTIONS"]


def test_api_rejects_bind_to_all_interfaces(valid_snapshot_file):
    with pytest.raises(DashboardApiError, match="0.0.0.0 또는 외부 인터페이스 바인딩은 엄격히 금지됩니다"):
        run_dashboard_api(valid_snapshot_file, host="0.0.0.0", port=8765)


def test_api_rejects_external_hosts(valid_snapshot_file):
    for bad_host in ["192.168.1.10", "example.com", "ec2-1-2-3-4.compute.amazonaws.com", "::1"]:
        with pytest.raises(DashboardApiError, match="보안 위반"):
            run_dashboard_api(valid_snapshot_file, host=bad_host, port=8765)


def test_cors_no_wildcard_on_any_response(live_server):
    status, _, headers = _request(f"{live_server}/api/health")
    assert headers.get("Access-Control-Allow-Origin") != "*"


def test_cors_localhost_origin_allowed(live_server):
    # Origin from http://localhost:4177 should be echoed
    status, data, headers = _request(
        f"{live_server}/api/trading/snapshot",
        method="GET",
        headers={"Origin": "http://localhost:4177"},
    )
    assert status == 200
    assert headers.get("Access-Control-Allow-Origin") == "http://localhost:4177"
    assert headers.get("Vary") == "Origin"


def test_cors_127_0_0_1_origin_allowed(live_server):
    # Origin from http://127.0.0.1:5173 should be echoed
    status, data, headers = _request(
        f"{live_server}/api/portfolio",
        method="GET",
        headers={"Origin": "http://127.0.0.1:5173"},
    )
    assert status == 200
    assert headers.get("Access-Control-Allow-Origin") == "http://127.0.0.1:5173"
    assert headers.get("Vary") == "Origin"


def test_cors_external_origin_receives_no_acao(live_server):
    # External evil origin GET must not receive Access-Control-Allow-Origin
    status, data, headers = _request(
        f"{live_server}/api/trading/snapshot",
        method="GET",
        headers={"Origin": "http://evil.example"},
    )
    assert status == 200
    assert "Access-Control-Allow-Origin" not in headers
    assert headers.get("Vary") == "Origin"


def test_cors_external_origin_options_rejected_403(live_server):
    # Preflight OPTIONS from evil origin must be rejected with 403 Forbidden
    status, data, headers = _request(
        f"{live_server}/api/trading/snapshot",
        method="OPTIONS",
        headers={"Origin": "http://evil.example"},
    )
    assert status == 403
    assert "Access-Control-Allow-Origin" not in headers


def test_cors_localhost_options_preflight_allowed(live_server):
    status, _, headers = _request(
        f"{live_server}/api/trading/snapshot",
        method="OPTIONS",
        headers={"Origin": "http://localhost:4177"},
    )
    assert status == 204
    assert headers.get("Access-Control-Allow-Origin") == "http://localhost:4177"


def test_api_health_degraded_when_snapshot_corrupted(tmp_path: Path):
    corrupt_file = tmp_path / "corrupt.json"
    corrupt_file.write_text("{ broken json", encoding="utf-8")

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

        snap_status, snap_data = _get(f"{base}/api/trading/snapshot")
        assert snap_status == 503
        assert "스냅샷 로드 불가" in snap_data["error"]
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("mutator,err_keyword", [
    (lambda d: d["portfolio"].update({"equity": "abc"}), "portfolio.equity"),
    (lambda d: d["botStatus"].update({"orderExecution": "INVALID"}), "botStatus.orderExecution"),
    (lambda d: d["positions"].append({"id": ""}), "positions[0].id"),
    (lambda d: d.update({"schemaVersion": 999}), "schemaVersion"),
    (lambda d: d.update({"dailyBaseline": {"equity": 100, "netCashFlow": 0, "tradingDay": "invalid", "timeZone": "Asia/Seoul"}}), "tradingDay"),
    (lambda d: d.update({"timestamp": "invalid_timestamp"}), "timestamp"),
])
def test_api_health_degraded_when_snapshot_contract_invalid(tmp_path: Path, valid_snapshot_data, mutator, err_keyword):
    mutator(valid_snapshot_data)
    bad_file = tmp_path / "bad_snapshot.json"
    with bad_file.open("w", encoding="utf-8") as f:
        json.dump(valid_snapshot_data, f)

    server = run_dashboard_api(bad_file, host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        base = f"http://127.0.0.1:{port}"
        status, data = _get(f"{base}/api/health")
        assert status == 200
        assert data["status"] == "degraded"
        assert data["snapshotAvailable"] is False
        assert err_keyword in data["error"]

        snap_status, snap_data = _get(f"{base}/api/trading/snapshot")
        assert snap_status == 503
        assert "계약 검증 실패" in snap_data["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_snapshot_cache_concurrent_access(valid_snapshot_file):
    cache = SnapshotCache(valid_snapshot_file)

    def read_cache():
        for _ in range(50):
            data, err, mtime = cache.get_snapshot()
            assert err is None
            assert data is not None
            assert data["schemaVersion"] == 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(read_cache) for _ in range(8)]
        for f in concurrent.futures.as_completed(futures):
            f.result()
