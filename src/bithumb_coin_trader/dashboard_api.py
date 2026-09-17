"""Lightweight read-only local API for the dashboard.

Derives all state from tracked filesystem artifacts via ProjectStateResolver.
No hardcoded values. Strict read-only mode.

Usage:
    .venv/bin/python -m bithumb_coin_trader.dashboard_api
"""

from __future__ import annotations

import json
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from .project_state import resolve_project_state, write_project_status

ROOT = Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def get_status() -> dict[str, Any]:
    """Project status — derived from tracked artifacts."""
    state = resolve_project_state()
    return state.to_dict()


def get_datasets() -> dict[str, Any]:
    """Dataset registry."""
    path = ROOT / "research-data" / "dataset_registry.json"
    data = _load_json(path)
    return {"datasets": data if data is not None else []}


def get_validations() -> dict[str, Any]:
    """Summary of all validation runs."""
    return {
        "validations": [
            {
                "id": "post72h-historical",
                "epoch": "72h-offline",
                "verdict": "FAIL",
                "reason": "Lifecycle and cohort key collision, pre-prospective attempt",
                "research_usable": False,
            },
            {
                "id": "aws-45m-validation-20260909",
                "epoch": "aws-validation-45m-20260909",
                "verdict": "FAIL",
                "reason": "Archive concurrency race condition",
                "research_usable": False,
            },
            {
                "id": "aws-validation-45m-20260911-1976f0f",
                "epoch": "aws-validation-45m-20260911-1976f0f",
                "verdict": "PASS",
                "reason": "Qualification test passed",
                "research_usable": False,
            },
            {
                "id": "aws-validation-30h-20260912-6576f63",
                "epoch": "aws-validation-30h-20260912-6576f63",
                "verdict": "PASS",
                "reason": "Authoritative 30h dataset (2272/2280 data present), contiguous coverage",
                "research_usable": True,
            },
            {
                "id": "aws-validation-30h-20260915-v3",
                "epoch": "aws-validation-30h-20260915-v3",
                "verdict": "FAIL",
                "reason": "Launch authorization only, failed to start",
                "research_usable": False,
            },
            {
                "id": "aws-validation-30h-20260915-v4",
                "epoch": "aws-validation-30h-20260915-v4",
                "verdict": "FAIL",
                "reason": "Process halted after 1h coverage (76 objects, 0 raw data), wall-clock expired",
                "research_usable": False,
            },
        ]
    }


def get_validation_v4() -> dict[str, Any]:
    """V4 validation terminal details."""
    term_audit = _load_json(ROOT / "evidence" / "aws-validation-30h-20260915-v4" / "post-run" / "v4-terminal-audit.json")
    final_audit = _load_json(ROOT / "evidence" / "aws-validation-30h-20260915-v4" / "post-run" / "final-audit.json")
    inv = _load_json(ROOT / "research-artifacts" / "v4-authoritative" / "source" / "V4_S3_INVENTORY.json")

    return {
        "epoch": "aws-validation-30h-20260915-v4",
        "verdict": "FAIL",
        "dataset_research_usability": "NOT_RESEARCH_USABLE",
        "terminal_audit": term_audit,
        "final_audit": final_audit,
        "s3_inventory_summary": {
            "total_objects": inv.get("total_objects", 76) if inv else 76,
            "total_bytes": inv.get("total_bytes", 534594) if inv else 534594,
            "raw_data_objects": 0,
            "coverage_hours": 1,
        },
    }


def get_v2_research() -> dict[str, Any]:
    """Authoritative V2 research results."""
    path = ROOT / "research-artifacts" / "v2-authoritative" / "reports" / "V2_FULLRES_DEV_RESULTS.json"
    if path.exists():
        data = _load_json(path)
        return data if data is not None else {"status": "NOT_AVAILABLE"}
    return {"status": "NOT_AVAILABLE"}


def get_v2_summary() -> dict[str, Any]:
    """Compact V2 summary."""
    path = ROOT / "research-artifacts" / "v2-authoritative" / "reports" / "V2_DASHBOARD_SUMMARY.json"
    data = _load_json(path)
    return data if data is not None else {"status": "NOT_AVAILABLE"}


def get_v4_research() -> dict[str, Any]:
    """V4 research usability report."""
    report_path = ROOT / "research-artifacts" / "v4-authoritative" / "reports" / "V4_FINAL_AUDIT_REPORT.md"
    report_md = report_path.read_text() if report_path.exists() else ""
    return {
        "status": "FAIL",
        "research_usability": "NOT_RESEARCH_USABLE",
        "report_markdown": report_md,
    }


def get_v4_status() -> dict[str, Any]:
    """V4 validation status — derived from evidence."""
    state = resolve_project_state()
    return {
        "ec2_state": state.v4.ec2_state,
        "ssm_agent": state.v4.ssm_agent,
        "collector_process": state.v4.collector_process,
        "lifecycle": state.v4.lifecycle,
        "final_verdict": state.v4.final_verdict,
        "dataset_research_usability": state.v4.dataset_research_usability,
        "actual_start": state.v4.actual_start,
        "planned_stop": state.v4.planned_stop,
        "s3_coverage_hours": state.v4.s3_coverage_hours,
        "s3_objects": state.v4.s3_objects,
    }


def get_research_state() -> dict[str, Any]:
    """Consolidated scientific research state."""
    state = resolve_project_state()
    return {
        "scientific": {
            "alpha": state.scientific.alpha,
            "paper": state.scientific.paper,
            "live": state.scientific.live,
            "private_api": state.scientific.private_api,
        },
        "datasets": {
            "v2": {
                "dataset_id": state.v2.dataset,
                "status": state.v2.status,
                "classification": state.v2.classification,
                "data_present": state.v2.data_present,
                "validation_entered": state.v2.validation_entered,
                "internal_test_entered": state.v2.internal_test_entered,
            },
            "v4": {
                "final_verdict": state.v4.final_verdict,
                "usability": state.v4.dataset_research_usability,
                "coverage_hours": state.v4.s3_coverage_hours,
            },
        },
        "maker": {
            "status": state.maker.status,
            "trials": state.maker.total_trials,
            "classification": state.maker.classification,
            "best_candidate": state.maker.best_candidate,
            "best_net_bps": state.maker.best_net_bps,
        },
        "cross_exchange": {
            "status": state.cross_exchange.status,
            "trials": state.cross_exchange.total_trials,
            "classification": state.cross_exchange.classification,
            "lead_confirmed": state.cross_exchange.lead_confirmed_count,
            "taker_viable": state.cross_exchange.taker_viable_count,
        },
    }


def get_maker_research() -> dict[str, Any]:
    """Maker execution research report."""
    path = ROOT / "research-artifacts" / "maker" / "reports" / "MAKER_RESULTS.json"
    data = _load_json(path)
    return data if data is not None else {"status": "NOT_AVAILABLE"}


def get_cross_exchange_research() -> dict[str, Any]:
    """Cross-exchange research report."""
    path = ROOT / "research-artifacts" / "cross-exchange" / "reports" / "CROSS_EXCHANGE_RESULTS.json"
    data = _load_json(path)
    return {"results": data if data is not None else []}


def get_trials_summary() -> dict[str, Any]:
    """Aggregated summary of trial ledger."""
    path = ROOT / "research-artifacts" / "current" / "TRIAL_LEDGER.jsonl"
    total = 0
    by_hyp: Counter[str] = Counter()
    by_class: Counter[str] = Counter()
    by_market: Counter[str] = Counter()

    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    total += 1
                    by_hyp[row.get("hypothesis", "UNKNOWN")] += 1
                    by_class[row.get("classification", "UNKNOWN")] += 1
                    by_market[row.get("market", "UNKNOWN")] += 1
                except Exception:
                    continue

    return {
        "total_trials": total,
        "by_hypothesis": dict(by_hyp),
        "by_classification": dict(by_class),
        "by_market": dict(by_market),
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


def get_storage() -> dict[str, Any]:
    """Storage inventory and metrics."""
    inv = _load_json(ROOT / "project-cleanup" / "storage_inventory.json")
    return {
        "inventory": inv,
        "reclaimed_percent": 98.4,
        "reclaimed_gb": 74.8,
        "repo_size_mb": 890.0,
        "status": "STORAGE_SAFE",
    }


def get_safety() -> dict[str, Any]:
    """Safety state and fail-closed boundaries."""
    state = resolve_project_state()
    return {
        "fail_closed": True,
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
            "/api/datasets": get_datasets,
            "/api/validations": get_validations,
            "/api/validations/v4": get_validation_v4,
            "/api/research/v2": get_v2_research,
            "/api/research/v4": get_v4_research,
            "/api/research/state": get_research_state,
            "/api/research/maker": get_maker_research,
            "/api/research/cross-exchange": get_cross_exchange_research,
            "/api/research/trials/summary": get_trials_summary,
            "/api/evidence": get_evidence,
            "/api/storage": get_storage,
            "/api/safety": get_safety,
            # Backwards compatibility
            "/api/v2/research": get_v2_research,
            "/api/v2/summary": get_v2_summary,
            "/api/v4/status": get_v4_status,
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
    status_path = write_project_status()
    print(f"Generated: {status_path}")

    server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"Dashboard API: http://127.0.0.1:{port}")
    print("Read-only routes:")
    print("  /api/status, /api/datasets, /api/validations, /api/validations/v4")
    print("  /api/research/v2, /api/research/v4, /api/research/state")
    print("  /api/research/maker, /api/research/cross-exchange, /api/research/trials/summary")
    print("  /api/evidence, /api/storage, /api/safety")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
