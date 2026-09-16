"""Lightweight read-only local API for the dashboard.

Serves project status, research results, and evidence from
tracked filesystem artifacts.

Usage:
    .venv/bin/python -m bithumb_coin_trader.dashboard_api

Endpoints:
    GET /api/status      — Project scientific state
    GET /api/v2/research — V2 research results
    GET /api/v4/status   — V4 validation status
    GET /api/evidence    — Evidence artifacts
"""

from __future__ import annotations

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def get_status() -> dict[str, Any]:
    """Project scientific state."""
    return {
        "alpha": "UNPROVEN",
        "paper": "NOT STARTED",
        "live": "DISABLED",
        "private_api": "DISABLED",
        "v2_classification": "NO EXECUTABLE TAKER CANDIDATE",
        "v4_status": "RUNNING",
        "v4_final_verdict": "NOT YET AVAILABLE",
        "develop_sha": _get_git_sha(),
    }


def get_v2_research() -> dict[str, Any]:
    """V2 research results."""
    results_path = ROOT / "research-artifacts" / "v2-authoritative" / "reports" / "V2_FULLRES_DEV_RESULTS.json"
    if results_path.exists():
        data = _load_json(results_path)
        if data:
            return data
    return {"status": "NOT_AVAILABLE"}


def get_v4_status() -> dict[str, Any]:
    """V4 validation status."""
    obs_path = ROOT / "evidence" / "aws-validation-30h-20260915-v4" / "post-run" / "preliminary-observation.json"
    if obs_path.exists():
        return _load_json(obs_path) or {}
    return {"status": "RUNNING", "final_verdict": "NOT YET AVAILABLE"}


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


def _get_git_sha() -> str:
    head = ROOT / ".git" / "HEAD"
    if head.exists():
        content = head.read_text().strip()
        if content.startswith("ref:"):
            ref_path = ROOT / ".git" / content.split(" ", 1)[1]
            if ref_path.exists():
                return ref_path.read_text().strip()[:12]
        return content[:12]
    return "unknown"


class DashboardHandler(BaseHTTPRequestHandler):
    """Read-only HTTP handler for dashboard API."""

    def do_GET(self):
        routes = {
            "/api/status": get_status,
            "/api/v2/research": get_v2_research,
            "/api/v4/status": get_v4_status,
            "/api/evidence": get_evidence,
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
        pass  # Suppress logging


def main():
    port = 8787
    server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"Dashboard API running at http://127.0.0.1:{port}")
    print(f"Endpoints: /api/status, /api/v2/research, /api/v4/status, /api/evidence")
    print(f"Root: {ROOT}")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
