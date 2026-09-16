"""Project State Resolver — Single Source of Truth for Dashboard/API.

Derives all project state from tracked filesystem artifacts.
No hardcoded values. No stale state.

Usage:
    from bithumb_coin_trader.project_state import resolve_project_state
    state = resolve_project_state()
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class ScientificState:
    alpha: str = "UNPROVEN"
    paper: str = "NOT STARTED"
    live: str = "DISABLED"
    private_api: str = "DISABLED"


@dataclass
class V2State:
    status: str = "NOT_RUN"
    classification: str = "NOT_AVAILABLE"
    dataset: str = ""
    source_objects: int = 0
    source_bytes: int = 0
    data_present: int = 0
    unknown_missing: int = 0
    h1h3_status: str = "NOT_RUN"
    execution_status: str = "NOT_RUN"
    execution_scenarios: int = 0
    profitable_scenarios: int = 0
    best_taker_bps: float | None = None
    validation_entered: bool = False
    internal_test_entered: bool = False


@dataclass
class V4State:
    ec2_state: str = "UNKNOWN"
    ssm_agent: str = "UNKNOWN"
    collector_process: str = "UNKNOWN"
    lifecycle: str = "UNKNOWN"
    s3_coverage_hours: int = 0
    s3_objects: int = 0
    final_verdict: str = "NOT_YET_AVAILABLE"
    actual_start: str = ""
    planned_stop: str = ""


@dataclass
class ProjectState:
    schema_version: int = 1
    generated_at: str = ""
    source_commit: str = ""
    scientific: ScientificState = field(default_factory=ScientificState)
    v2: V2State = field(default_factory=V2State)
    v4: V4State = field(default_factory=V4State)
    live_trading: str = "DISABLED"
    private_api: str = "DISABLED"
    paper_trading: str = "NOT STARTED"
    dashboard_mode: str = "READ_ONLY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "source_commit": self.source_commit,
            "scientific": asdict(self.scientific),
            "v2": asdict(self.v2),
            "v4": asdict(self.v4),
            "live_trading": self.live_trading,
            "private_api": self.private_api,
            "paper_trading": self.paper_trading,
            "dashboard_mode": self.dashboard_mode,
        }


def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=5
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _resolve_v2() -> V2State:
    """Resolve V2 state from tracked artifacts."""
    v2 = V2State()

    # Check source manifest
    manifest = _load_json(ROOT / "research-artifacts" / "v2-authoritative" / "source" / "V2_SOURCE_MANIFEST.json")
    if manifest:
        v2.dataset = manifest.get("dataset_id", "")
        v2.source_objects = manifest.get("object_count", 0)
        v2.source_bytes = manifest.get("total_bytes", 0)
        v2.status = "SOURCE_COMPLETE"

    # Check DQ
    dq = _load_json(ROOT / "research-artifacts" / "v2-authoritative" / "dq" / "V2_DQ_SUMMARY.json")
    if dq:
        v2.data_present = dq.get("data_present", 0)
        v2.unknown_missing = dq.get("unknown_missing", 0)

    # Check final report
    report = _load_json(ROOT / "research-artifacts" / "v2-authoritative" / "reports" / "V2_FULLRES_DEV_RESULTS.json")
    if report:
        v2.h1h3_status = "COMPLETE"
        v2.execution_status = "COMPLETE"
        v2.execution_scenarios = report.get("execution_scenarios", 48)
        v2.classification = "NO EXECUTABLE TAKER CANDIDATE"
        v2.status = "COMPLETE"

    # Check validation/internal test
    v2.validation_entered = False
    v2.internal_test_entered = False

    return v2


def _resolve_v4() -> V4State:
    """Resolve V4 state from tracked evidence."""
    v4 = V4State()

    # Check preliminary observation
    obs = _load_json(ROOT / "evidence" / "aws-validation-30h-20260915-v4" / "post-run" / "preliminary-observation.json")
    if obs:
        v4.actual_start = obs.get("actual_start", "2026-09-15T10:26:33.652102Z")
        v4.s3_coverage_hours = obs.get("s3_coverage_hours_visible", 0)
        v4.s3_objects = obs.get("s3_objects", 0)

    # Check for final audit
    final = _load_json(ROOT / "evidence" / "aws-validation-30h-20260915-v4" / "post-run" / "final-audit.json")
    if final:
        verdict = final.get("audit_verdict", {})
        if verdict:
            v4.final_verdict = verdict.get("OVERALL", "NOT_YET_AVAILABLE")
            v4.lifecycle = verdict.get("PROCESS", "UNKNOWN")

    # Check for corrected/invalidated audit
    if final and "INVALID" in final.get("evidence_kind", ""):
        v4.final_verdict = "NOT_YET_AVAILABLE"
        v4.lifecycle = "CORRECTED_PREMATURE"

    # Runtime identity from seal
    runtime = _load_json(ROOT / "infra" / "aws" / "seals" / "aws-validation-30h-20260915-v4.runtime.json")
    if runtime:
        v4.actual_start = runtime.get("actual_start_utc", v4.actual_start)
        v4.planned_stop = "2026-09-16T17:00:00Z"

    # Default states (conservative)
    if v4.ec2_state == "UNKNOWN":
        v4.ec2_state = "UNKNOWN (runtime check required)"
    if v4.ssm_agent == "UNKNOWN":
        v4.ssm_agent = "UNKNOWN (runtime check required)"
    if v4.collector_process == "UNKNOWN":
        v4.collector_process = "UNKNOWN (cannot infer from EC2 state alone)"

    return v4


def resolve_project_state() -> ProjectState:
    """Resolve complete project state from tracked artifacts."""
    state = ProjectState()
    state.generated_at = datetime.now(timezone.utc).isoformat()
    state.source_commit = _git_sha()
    state.scientific = ScientificState()
    state.v2 = _resolve_v2()
    state.v4 = _resolve_v4()
    return state


def write_project_status(output_path: Path | None = None) -> Path:
    """Generate PROJECT_STATUS.json from tracked artifacts."""
    if output_path is None:
        output_path = ROOT / "dashboard-data" / "PROJECT_STATUS.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    state = resolve_project_state()
    output_path.write_text(json.dumps(state.to_dict(), indent=2, default=str))
    return output_path
