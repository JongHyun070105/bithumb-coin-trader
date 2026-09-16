"""Lightweight read-only local API for the dashboard.

Derives all state from tracked filesystem artifacts via ProjectStateResolver.
No hardcoded values.

Usage:
    .venv/bin/python -m bithumb_coin_trader.dashboard_api
"""

from __future__ import annotations

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from .project_state import resolve_project_state, write_project_status

ROOT = Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def get_status() -> dict[str, Any]:
    """Project status — derived from tracked artifacts."""
    state = resolve_project_state()
    return state.to_dict()


def get_v2_research() -> dict[str, Any]:
    """Full V2 research results."""
    path = ROOT / "research-artifacts" / "v2-authoritative" / "reports" / "V2_FULLRES_DEV_RESULTS.json"
    if path.exists():
        return _load_json(path) or {"status": "NOT_AVAILABLE"}
    return {"status": "NOT_AVAILABLE"}


def get_v2_summary() -> dict[str, Any]:
    """Compact V2 summary for dashboard."""
    path = ROOT / "research-artifacts" / "v2-authoritative" / "reports" / "V2_DASHBOARD_SUMMARY.json"
    return _load_json(path) or {"status": "NOT_AVAILABLE"}


def get_v4_status() -> dict[str, Any]:
    """V4 validation status — derived from evidence."""
    state = resolve_project_state()
    return {
        "ec2_state": state.v4.ec2_state,
        "ssm_agent": state.v4.ssm_agent,
        "collector_process": state.v4.collector_process,
        "lifecycle": state.v4.lifecycle,
        "final_verdict": state.v4.final_verdict,
        "actual_start": state.v4.actual_start,
        "planned_stop": state.v4.planned_stop,
        "s3_coverage_hours": state.v4.s3_coverage_hours,
    }


def get_evidence() -> dict[str, Any]:
    """Evidence artifacts summary."""
    evidence_dir = ROOT / "evidence"
    result: dict[str, Any] = {"artifacts": []}
    if evidence_dir.exists():
        for p in sorted(evidence_dir.rglob("*.json")):
            result["artifacts"].append({
                "path": str(p.relative_to(ROOT)),
                "size_bytes": p.stat().st_size,
            })
    return result


def get_datasets() -> dict[str, Any]:
    """Dataset registry."""
    path = ROOT / "research-data" / "dataset_registry.json"
    return _load_json(path) or {"datasets": []}


def get_safety() -> dict[str, Any]:
    """Safety state."""
    state = resolve_project_state()
    return {
        "live_trading": state.live_trading,
        "private_api": state.private_api,
        "paper_trading": state.paper_trading,
        "dashboard_mode": state.dashboard_mode,
        "alpha": state.scientific.alpha,
    }


class DashboardHandler(BaseHTTPRequestHandler):
    """Read-only HTTP handler for dashboard API."""

    def do_GET(self):
        routes = {
            "/api/status": get_status,
            "/api/v2/research": get_v2_research,
            "/api/v2/summary": get_v2_summary,
            "/api/v4/status": get_v4_status,
            "/api/evidence": get_evidence,
            "/api/datasets": get_datasets,
            "/api/safety": get_safety,
        }
        handler = routes.get(self.path)
        if handler:
            data = handler()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data, indent=2, default=str).encode())
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error": "not found"}')

    def log_message(self, format, *args):
        pass


def main():
    port = 8787
    # Generate initial status file
    status_path = write_project_status()
    print(f"Generated: {status_path}")

    server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"Dashboard API: http://127.0.0.1:{port}")
    print(f"Endpoints: /api/status, /api/v2/research, /api/v2/summary, /api/v4/status, /api/evidence, /api/datasets, /api/safety")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
