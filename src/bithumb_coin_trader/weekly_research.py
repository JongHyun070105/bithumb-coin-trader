"""Validator-gated weekly research orchestration, isolated from live execution."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from .data import fetch_minute_candles, save_candles_csv
from .discord_notify import send_discord_message
from .models import Candle


@dataclass(frozen=True, slots=True)
class WeeklyResearchResult:
    week: str
    status: str
    validation_passed: bool
    research_candidate: str
    can_promote: bool
    artifact_dir: str
    detail: str


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _reserve_week(path: Path, week: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"week": week, "status": "running"}, handle, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return True


def _subprocess_env(project_root: Path) -> dict[str, str]:
    """Build a credential-free environment for public-data research subprocesses."""
    return {
        "HOME": str(Path.home()),
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONPATH": os.pathsep.join((str(project_root / "src"), str(project_root))),
        "BITHUMB_NEW_ENTRIES": "false",
    }


def run_weekly_research(
    project_root: Path,
    *,
    observed_at: datetime | None = None,
    fetcher: Callable[..., Sequence[Candle]] = fetch_minute_candles,
    saver: Callable[[str | Path, Iterable[Candle]], None] = save_candles_csv,
    command_runner: CommandRunner = subprocess.run,
    notify: Callable[[str], bool] = send_discord_message,
) -> WeeklyResearchResult:
    """Run one public-data research cycle; never opens holdout or changes live policy."""
    now = observed_at or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    iso = now.isocalendar()
    week = f"{iso.year}-W{iso.week:02d}"
    research_root = project_root / "state" / "research" / week
    marker = research_root / "weekly-run.json"
    if not _reserve_week(marker, week):
        return WeeklyResearchResult(
            week=week,
            status="skipped_duplicate",
            validation_passed=False,
            research_candidate="unchanged",
            can_promote=False,
            artifact_dir=str(research_root),
            detail="this ISO week was already attempted",
        )

    dataset = research_root / "krw-btc-30m.csv"
    result_path = research_root / "result.json"
    report_path = research_root / "report.json"
    validation_path = research_root / "validation.json"
    try:
        candles = tuple(fetcher("KRW-BTC", 30, 45_000))
        if len(candles) != 45_000:
            raise RuntimeError(f"expected 45000 completed candles, received {len(candles)}")
        saver(dataset, candles)
        environment = _subprocess_env(project_root)
        research = command_runner(
            [
                sys.executable,
                str(project_root / "scripts" / "run_winrate_research.py"),
                "--input", str(dataset),
                "--output", str(result_path),
                "--report", str(report_path),
            ],
            cwd=project_root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=7_200,
        )
        if research.returncode != 0:
            raise RuntimeError(f"research runner failed with exit {research.returncode}")
        validation = command_runner(
            [
                sys.executable,
                str(project_root / "scripts" / "validate_winrate_research.py"),
                "--input", str(dataset),
                "--report", str(result_path),
                "--mirror", str(report_path),
                "--output", str(validation_path),
            ],
            cwd=project_root,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=7_200,
        )
        report = json.loads(result_path.read_text(encoding="utf-8"))
        checked = json.loads(validation_path.read_text(encoding="utf-8"))
        passed = validation.returncode == 0 and checked.get("passed") is True
        selection = report.get("selection", {})
        candidate = str(selection.get("research_candidate", "cash"))
        can_promote = bool(selection.get("can_promote", False)) and passed
        if can_promote:
            raise RuntimeError("weekly automation refuses promotable output; manual review is required")
        outcome = WeeklyResearchResult(
            week=week,
            status="completed" if passed else "validation_failed",
            validation_passed=passed,
            research_candidate=candidate,
            can_promote=False,
            artifact_dir=str(research_root),
            detail="research-only; holdout unopened; automatic promotion forbidden",
        )
    except Exception as exc:
        outcome = WeeklyResearchResult(
            week=week,
            status="failed",
            validation_passed=False,
            research_candidate="unchanged",
            can_promote=False,
            artifact_dir=str(research_root),
            detail=f"{type(exc).__name__}: {exc}",
        )

    _atomic_json(marker, asdict(outcome))
    try:
        notify(
            "## 🧪 [BITHUMB] 주간 전략 연구\n"
            f"- 주차: `{outcome.week}`\n"
            f"- 상태: `{outcome.status}`\n"
            f"- 독립 검증: `{'통과' if outcome.validation_passed else '실패/미실행'}`\n"
            f"- 선택: `{outcome.research_candidate}`\n"
            "- 실거래 자동 승격: `금지`\n"
            f"- 상세: {outcome.detail}"
        )
    except Exception:
        # Research evidence is already persisted; notification is best-effort.
        pass
    return outcome
