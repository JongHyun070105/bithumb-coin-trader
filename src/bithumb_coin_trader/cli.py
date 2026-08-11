from __future__ import annotations

import argparse
import fcntl
import json
import os
import shlex
import subprocess
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from .backtest import BacktestResult, Backtester
from .config import TradingSettings
from .data import dataset_manifest, fetch_daily_candles, load_candles_csv, save_candles_csv
from .discord_notify import (
    DEFAULT_SOURCE_CRON_ENV,
    DEFAULT_CONFIG_PATH,
    DiscordNotifier,
    TradeEvent,
    TradeNotification,
    configured_discord_target,
    save_local_target,
    status_test_notification,
    target_from_crontab,
)
from .models import Candle, Signal
from .mcp_client import McpStdioClient
from .paper import PaperEngine, PaperError, PaperState, load_paper_state, verify_audit
from .readiness import PaperEvidence, assess_live_readiness
from .research import ProjectResearchReport, run_chronological_research
from .state import load_state
from .strategy import TrendBreakoutStrategy


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PAPER_STATE = PROJECT_ROOT / "state" / "paper.json"
DEFAULT_PAPER_AUDIT = PROJECT_ROOT / "state" / "paper.jsonl"
DEFAULT_PAPER_LOCK = PROJECT_ROOT / "state" / "paper.lock"
DEFAULT_LIVE_STATE = PROJECT_ROOT / "state" / "live.json"
DEFAULT_RESEARCH_REPORT = PROJECT_ROOT / "reports" / "krw-btc-daily-baseline-2026-08-10.json"
PAPER_CRON_MARKER = "# bithumb-coin-trader-paper"


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another paper run is already active") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _metric(result: BacktestResult) -> dict[str, Any]:
    return {
        "initial_equity_krw": round(result.initial_equity, 2),
        "final_equity_krw": round(result.final_equity, 2),
        "total_return": round(result.total_return, 8),
        "max_drawdown": round(result.max_drawdown, 8),
        "sharpe": round(result.sharpe, 6),
        "trade_count": result.trade_count,
        "win_rate": round(result.win_rate, 8),
        "exposure": round(result.exposure, 8),
    }


def _report(report: ProjectResearchReport) -> dict[str, Any]:
    return {
        "fold_count": len(report.folds),
        "compounded_return": round(report.compounded_return, 8),
        "maximum_drawdown": round(report.maximum_drawdown, 8),
        "mean_sharpe": round(report.mean_sharpe, 6),
        "trade_count": report.trade_count,
        "weighted_win_rate": round(report.weighted_win_rate, 8),
        "profitable_folds": sum(fold.result.total_return > 0 for fold in report.folds),
        "folds": [
            {
                "fold": fold.fold + 1,
                "train": [fold.train_start, fold.train_end],
                "test": [fold.test_start, fold.test_end],
                **_metric(fold.result),
            }
            for fold in report.folds
        ],
    }


def _manifest(candles: Sequence[Candle]) -> dict[str, Any]:
    manifest = dataset_manifest(candles)
    return {
        "schema_version": manifest.schema_version,
        "market": manifest.market,
        "candle_count": manifest.candle_count,
        "start_at": manifest.start_at.isoformat() if manifest.start_at else None,
        "end_at": manifest.end_at.isoformat() if manifest.end_at else None,
        "sha256": manifest.sha256,
    }


def _load_or_fetch(args: argparse.Namespace) -> list[Candle]:
    if args.input:
        return load_candles_csv(args.input)
    return fetch_daily_candles(args.market, args.count, timeout=args.timeout)


def _promotion_status(base: ProjectResearchReport, stress: ProjectResearchReport) -> dict[str, Any]:
    checks = {
        "at_least_six_folds": len(base.folds) >= 6,
        "at_least_thirty_oos_trades": base.trade_count >= 30,
        "majority_profitable_folds": sum(f.result.total_return > 0 for f in base.folds) > len(base.folds) / 2,
        "positive_oos_return_after_costs": base.compounded_return > 0,
        "positive_double_cost_stress": stress.compounded_return > 0,
    }
    return {
        "status": "PAPER_CANDIDATE" if all(checks.values()) else "RESEARCH_ONLY",
        "checks": checks,
    }


def command_fetch(args: argparse.Namespace) -> int:
    candles = fetch_daily_candles(args.market, args.count, timeout=args.timeout)
    save_candles_csv(args.output, candles)
    print(
        json.dumps(
            {
                "market": args.market,
                "candles": len(candles),
                "output": str(args.output),
                "dataset": _manifest(candles),
            }
        )
    )
    return 0


def command_research(args: argparse.Namespace) -> int:
    candles = _load_or_fetch(args)
    base_settings = TradingSettings()
    stress_settings = TradingSettings(fee_rate=0.005, slippage_bps=10)
    base = run_chronological_research(
        candles,
        train_size=args.train_size,
        test_size=args.test_size,
        settings=base_settings,
        allow_short=False,
    )
    stress = run_chronological_research(
        candles,
        train_size=args.train_size,
        test_size=args.test_size,
        settings=stress_settings,
        allow_short=False,
    )
    strategy = TrendBreakoutStrategy()
    full = Backtester(base_settings, allow_short=False).run(candles, strategy.generate(candles))
    payload = {
        "market": candles[0].market,
        "period": [candles[0].timestamp.isoformat(), candles[-1].timestamp.isoformat()],
        "candles": len(candles),
        "dataset": _manifest(candles),
        "mode": "bithumb_spot_long_flat",
        "full_sample_context_only": _metric(full),
        "walk_forward": _report(base),
        "double_cost_stress": _report(stress),
        "promotion": _promotion_status(base, stress),
        "warning": "Backtests are research evidence, not a profit guarantee or live-trading approval.",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_signal(args: argparse.Namespace) -> int:
    candles = _load_or_fetch(args)
    strategy = TrendBreakoutStrategy()
    research_signal = strategy.generate(candles)[-1]
    payload = {
        "market": candles[-1].market,
        "candle_timestamp": candles[-1].timestamp.isoformat(),
        "research_signal": research_signal.name,
        "bithumb_spot_target": research_signal.name,
        "order_submitted": False,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_discord_setup(args: argparse.Namespace) -> int:
    result = subprocess.run(
        ["crontab", "-l"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError("could not read the current user crontab")
    target = target_from_crontab(result.stdout, source_env=args.source_cron_env)
    destination = save_local_target(target, env_path=args.config_path)
    print(
        json.dumps(
            {
                "configured": True,
                "source": args.source_cron_env,
                "destination": str(destination),
                "target": "discord:<configured>",
            }
        )
    )
    return 0


def command_discord_test(_args: argparse.Namespace) -> int:
    sent = DiscordNotifier().send(status_test_notification())
    print(json.dumps({"sent": sent, "order_submitted": False}))
    return 0 if sent else 2


def _paper_result_payload(result: Any) -> dict[str, Any]:
    payload = asdict(result)
    payload["state"] = asdict(result.state)
    payload["order_submitted"] = False
    payload["mode"] = "paper"
    return payload


def command_paper_run(args: argparse.Namespace) -> int:
    with _exclusive_lock(args.lock_path):
        candles = fetch_daily_candles(
            args.market,
            args.count,
            timeout=args.timeout,
        )
        engine = PaperEngine(args.state_path, args.audit_path)
        paper_state = load_paper_state(args.state_path)
        prior = paper_state.last_decision_at
        pending_indices: list[int] = []
        if prior is not None:
            prior_at = datetime.fromisoformat(prior.replace("Z", "+00:00")).astimezone(UTC)
            pending_indices = [
                index
                for index in range(len(candles) - 1)
                if candles[index].timestamp.astimezone(UTC) > prior_at
            ]
        strategy = TrendBreakoutStrategy()
        if pending_indices:
            signals = strategy.generate(
                candles,
                initial_position=(
                    Signal.LONG
                    if paper_state.strategy_position == "long"
                    else Signal.FLAT
                ),
                start_index=pending_indices[0],
            )
        else:
            signals = strategy.generate(candles)
        if pending_indices:
            results = [
                engine.process(candles[: index + 2], signals[: index + 2])
                for index in pending_indices
            ]
            result = results[-1]
        else:
            result = engine.process(candles, signals)
            results = [result] if result.processed else []
    sent = False
    if args.notify and result.processed:
        sent = DiscordNotifier().send(
            TradeNotification(
                event=TradeEvent.PAPER,
                market=result.state.market,
                side="bid" if result.action == "buy" else "ask" if result.action == "sell" else None,
                notional_krw=(
                    str(
                        Decimal(result.state.cost_basis_krw)
                        - Decimal(result.fee_krw)
                    )
                    if result.action == "buy"
                    else None
                ),
                volume=result.state.quantity if result.action == "buy" else None,
                detail=(
                    f"action={result.action}, equity={result.equity_krw} KRW, "
                    f"decision={result.decision_at}, catch_up={len(results)}; 실주문 없음"
                ),
            )
        )
    payload = _paper_result_payload(result)
    payload["processed_decisions"] = len(results)
    payload["discord_sent"] = sent
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _paper_evidence(state_path: Path, audit_path: Path) -> PaperEvidence:
    state = load_paper_state(state_path)
    observed = datetime.now(UTC)
    try:
        evidence = verify_audit(audit_path, state_path)
    except PaperError:
        return PaperEvidence(
            started_at=observed,
            observed_at=observed,
            decision_count=state.decision_count,
            completed_round_trips=0,
            accounting_mismatches=1,
        )
    started = (
        datetime.fromisoformat(evidence.first_decision_at.replace("Z", "+00:00"))
        if evidence.first_decision_at
        else observed
    )
    last_execution = (
        datetime.fromisoformat(state.last_execution_at.replace("Z", "+00:00"))
        if state.last_execution_at
        else observed
    )
    return PaperEvidence(
        started_at=started.astimezone(UTC),
        observed_at=last_execution.astimezone(UTC),
        decision_count=evidence.decision_count,
        completed_round_trips=evidence.round_trip_count,
        accounting_mismatches=0,
    )


def command_paper_status(args: argparse.Namespace) -> int:
    state = load_paper_state(args.state_path)
    evidence = _paper_evidence(args.state_path, args.audit_path)
    payload = {
        "mode": "paper",
        "order_submitted": False,
        "state": asdict(state),
        "evidence": {
            "started_at": evidence.started_at.isoformat(),
            "observed_at": evidence.observed_at.isoformat(),
            "decision_count": evidence.decision_count,
            "completed_round_trips": evidence.completed_round_trips,
            "accounting_mismatches": evidence.accounting_mismatches,
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if evidence.accounting_mismatches == 0 else 2


class _UnavailableProbe:
    def call_read_tool(self, _name: str, _arguments: Any = None) -> Any:
        raise RuntimeError("read-only MCP probe unavailable")


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def command_live_readiness(args: argparse.Namespace) -> int:
    environment = dict(os.environ)
    try:
        target = configured_discord_target()
    except ValueError:
        target = None
    if target:
        environment["BITHUMB_DISCORD_TARGET"] = target
    inputs = {
        "research_report": _read_json_object(args.research_report),
        "paper": _paper_evidence(args.paper_state_path, args.paper_audit_path),
        "bot_state": load_state(args.live_state_path),
        "env": environment,
        "market": args.market,
    }
    if args.probe_mcp:
        try:
            with McpStdioClient(timeout=args.timeout) as probe:
                report = assess_live_readiness(**inputs, mcp_probe=probe)
        except Exception:
            report = assess_live_readiness(**inputs, mcp_probe=_UnavailableProbe())
    else:
        report = assess_live_readiness(**inputs)
    payload = report.as_dict()
    payload["order_submitted"] = False
    payload["note"] = "READY is a review gate, not permission to place an order."
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if report.ready else 2


def command_paper_schedule_install(args: argparse.Namespace) -> int:
    root = args.project_root.resolve()
    executable = root / ".venv" / "bin" / "bithumb-trader"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError(f"paper runner is not executable: {executable}")
    state_dir = root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["crontab", "-l"], check=False, capture_output=True, text=True, timeout=10
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError("could not read the current user crontab")
    existing = result.stdout if result.returncode == 0 else ""
    retained = [line for line in existing.splitlines() if PAPER_CRON_MARKER not in line]
    command = (
        f"10 * * * * cd {shlex.quote(str(root))} && "
        f"{shlex.quote(str(executable))} paper-run --notify "
        f">> {shlex.quote(str(state_dir / 'paper-cron.log'))} 2>&1 {PAPER_CRON_MARKER}"
    )
    updated = "\n".join([*retained, command]).rstrip() + "\n"
    installed = subprocess.run(
        ["crontab", "-"], input=updated, check=False, capture_output=True, text=True, timeout=10
    )
    if installed.returncode != 0:
        raise RuntimeError("could not install the paper schedule")
    print(
        json.dumps(
            {
                "installed": True,
                "schedule": "hourly at :10; at most one decision per completed day",
                "mode": "paper",
                "order_submitted": False,
            }
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Research-first Bithumb coin trader")
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="fetch public daily candles to an explicit CSV path")
    fetch.add_argument("--market", default="KRW-BTC")
    fetch.add_argument("--count", type=int, default=1_000)
    fetch.add_argument("--timeout", type=float, default=20.0)
    fetch.add_argument("--output", type=Path, required=True)
    fetch.set_defaults(handler=command_fetch)

    research = subparsers.add_parser("research", help="run fixed-parameter walk-forward research")
    research.add_argument("--market", default="KRW-BTC")
    research.add_argument("--count", type=int, default=1_000)
    research.add_argument("--timeout", type=float, default=20.0)
    research.add_argument("--input", type=Path)
    research.add_argument("--train-size", type=int, default=400)
    research.add_argument("--test-size", type=int, default=100)
    research.set_defaults(handler=command_research)

    signal = subparsers.add_parser("signal", help="print the current research signal without ordering")
    signal.add_argument("--market", default="KRW-BTC")
    signal.add_argument("--count", type=int, default=200)
    signal.add_argument("--timeout", type=float, default=20.0)
    signal.add_argument("--input", type=Path)
    signal.set_defaults(handler=command_signal)

    discord_setup = subparsers.add_parser(
        "discord-setup", help="reuse an existing Discord target from the user crontab"
    )
    discord_setup.add_argument(
        "--source-cron-env", default=DEFAULT_SOURCE_CRON_ENV
    )
    discord_setup.add_argument(
        "--config-path", type=Path, default=DEFAULT_CONFIG_PATH
    )
    discord_setup.set_defaults(handler=command_discord_setup)

    discord_test = subparsers.add_parser(
        "discord-test", help="send a finance-chat connection test without ordering"
    )
    discord_test.set_defaults(handler=command_discord_test)

    paper_run = subparsers.add_parser(
        "paper-run", help="process exactly one completed daily paper decision"
    )
    paper_run.add_argument("--market", default="KRW-BTC")
    paper_run.add_argument("--count", type=int, default=1_000)
    paper_run.add_argument("--timeout", type=float, default=20.0)
    paper_run.add_argument("--state-path", type=Path, default=DEFAULT_PAPER_STATE)
    paper_run.add_argument("--audit-path", type=Path, default=DEFAULT_PAPER_AUDIT)
    paper_run.add_argument("--lock-path", type=Path, default=DEFAULT_PAPER_LOCK)
    paper_run.add_argument("--notify", action="store_true")
    paper_run.set_defaults(handler=command_paper_run)

    paper_status = subparsers.add_parser(
        "paper-status", help="show persisted paper evidence without ordering"
    )
    paper_status.add_argument("--state-path", type=Path, default=DEFAULT_PAPER_STATE)
    paper_status.add_argument("--audit-path", type=Path, default=DEFAULT_PAPER_AUDIT)
    paper_status.set_defaults(handler=command_paper_status)

    readiness = subparsers.add_parser(
        "live-readiness", help="fail-closed live preparation report; never places an order"
    )
    readiness.add_argument("--market", default="KRW-BTC")
    readiness.add_argument("--research-report", type=Path, default=DEFAULT_RESEARCH_REPORT)
    readiness.add_argument("--paper-state-path", type=Path, default=DEFAULT_PAPER_STATE)
    readiness.add_argument("--paper-audit-path", type=Path, default=DEFAULT_PAPER_AUDIT)
    readiness.add_argument("--live-state-path", type=Path, default=DEFAULT_LIVE_STATE)
    readiness.add_argument("--probe-mcp", action="store_true")
    readiness.add_argument("--timeout", type=float, default=30.0)
    readiness.set_defaults(handler=command_live_readiness)

    schedule = subparsers.add_parser(
        "paper-schedule-install", help="install an idempotent daily paper-only cron entry"
    )
    schedule.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    schedule.set_defaults(handler=command_paper_schedule_install)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
