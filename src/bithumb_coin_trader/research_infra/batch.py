"""Resumable, content-addressed orchestration for governed experiments.

The batch runner writes every attempt beneath an identity-derived directory.
Manifests and terminal records are write-once; fold records are append-only and
hash chained so an interrupted run can resume without replacing prior evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable, Mapping, Sequence

from ..models import Candle
from .costs import SpotCostScenario
from .result_schema import ResultStatus
from .walk_forward_runner import (
    TrainOnlyTargetWeightStrategy,
    WalkForwardFoldRun,
    WalkForwardReport,
    fold_run_from_dict,
    run_walk_forward,
)


class ResearchBatchError(ValueError):
    """Raised for invalid batch inputs or conflicting persisted evidence."""


StrategyFactory = Callable[[int, Mapping[str, Any]], TrainOnlyTargetWeightStrategy]


@dataclass(frozen=True, slots=True)
class BatchExperiment:
    candidate_family: str
    strategy_id: str
    strategy_factory: StrategyFactory
    strategy_config: Mapping[str, Any]
    feature_config: Mapping[str, Any]
    parameter_sets: tuple[Mapping[str, Any], ...] = ({},)
    seed: int = 0


@dataclass(frozen=True, slots=True)
class _WorkItem:
    spec: BatchExperiment
    parameters: Mapping[str, Any]
    strategy_config: Mapping[str, Any]
    identity: Mapping[str, Any]
    experiment_id: str


def run_research_batch(
    *,
    candles: Sequence[Candle],
    dataset_id: str,
    dataset_sha256: str,
    code_revision: str,
    dataset_provenance: Mapping[str, Any] | None = None,
    experiments: Sequence[BatchExperiment],
    cost_scenarios: Sequence[SpotCostScenario],
    output_dir: Path,
    n_folds: int,
    window_mode: str,
    purge_s: float,
    embargo_s: float,
    max_experiments: int = 100,
    max_fold_cost_runs: int = 2_000,
    retry_failed: bool = False,
) -> dict[str, Any]:
    """Run or resume a bounded grid of strategy/config/cost/fold experiments."""
    if not experiments:
        raise ResearchBatchError("at least one experiment definition is required")
    if n_folds <= 0:
        raise ResearchBatchError("n_folds must be positive")
    if not cost_scenarios or len({item.name for item in cost_scenarios}) != len(cost_scenarios):
        raise ResearchBatchError("cost scenarios must be non-empty and uniquely named")
    if max_experiments <= 0 or max_fold_cost_runs <= 0:
        raise ResearchBatchError("experiment limits must be positive")

    fold_config = {
        "n_folds": n_folds,
        "window_mode": window_mode,
        "purge_s": purge_s,
        "embargo_s": embargo_s,
    }
    cost_payload = [item.to_dict() for item in cost_scenarios]
    items = _expand_experiments(
        experiments=experiments,
        dataset_id=dataset_id,
        dataset_sha256=dataset_sha256,
        code_revision=code_revision,
        dataset_provenance=dataset_provenance or {},
        cost_payload=cost_payload,
        fold_config=fold_config,
    )
    if len(items) > max_experiments:
        raise ResearchBatchError(
            f"experiment grid has {len(items)} runs; limit is {max_experiments}"
        )
    cells = len(items) * n_folds * len(cost_scenarios)
    if cells > max_fold_cost_runs:
        raise ResearchBatchError(
            f"experiment grid has {cells} fold/cost runs; limit is {max_fold_cost_runs}"
        )

    batch_identity = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "dataset_sha256": dataset_sha256,
        "code_revision": code_revision,
        "dataset_provenance": dict(dataset_provenance or {}),
        "experiments": [dict(item.identity) for item in items],
        "cost_scenarios": cost_payload,
        "fold_config": fold_config,
    }
    batch_id = _identity("batch", batch_identity)
    root = Path(output_dir) / "batches" / batch_id
    root.mkdir(parents=True, exist_ok=True)
    _ensure_identity_manifest(root / "manifest.json", batch_id, batch_identity)

    run_summaries: list[dict[str, Any]] = []
    for item in items:
        summary = _run_experiment(
            root=root,
            batch_id=batch_id,
            item=item,
            candles=candles,
            dataset_id=dataset_id,
            dataset_sha256=dataset_sha256,
            code_revision=code_revision,
            cost_scenarios=cost_scenarios,
            fold_config=fold_config,
            retry_failed=retry_failed,
        )
        run_summaries.append(summary)

    status = (
        "NEEDS_RETRY" if any(run["status"] == "NEEDS_RETRY" for run in run_summaries)
        else "PARTIAL" if any(run["status"] != "COMPLETE" for run in run_summaries)
        else "COMPLETE"
    )
    report = {
        "schema_version": 1,
        "batch_id": batch_id,
        "batch_identity_sha256": _sha256(_canonical(batch_identity)),
        "status": status,
        "experiment_count": len(items),
        "completed_count": sum(run["status"] == "COMPLETE" for run in run_summaries),
        "runs": run_summaries,
        "baseline_comparisons": _baseline_comparisons(run_summaries),
    }
    if status == "COMPLETE":
        _write_once(root / "aggregate_report.json", _json_bytes(report))
    else:
        _append_event(root / "batch_events.jsonl", "BATCH_PARTIAL", report)
    return report


def _expand_experiments(
    *,
    experiments: Sequence[BatchExperiment],
    dataset_id: str,
    dataset_sha256: str,
    code_revision: str,
    dataset_provenance: Mapping[str, Any],
    cost_payload: Sequence[Mapping[str, Any]],
    fold_config: Mapping[str, Any],
) -> list[_WorkItem]:
    items: list[_WorkItem] = []
    for spec in experiments:
        if not spec.candidate_family.strip() or not spec.strategy_id.strip():
            raise ResearchBatchError("candidate_family and strategy_id must be non-empty")
        if not spec.parameter_sets:
            raise ResearchBatchError(f"{spec.strategy_id} has no parameter sets")
        for parameters in spec.parameter_sets:
            if not isinstance(parameters, Mapping):
                raise ResearchBatchError("each parameter set must be a JSON object")
            strategy_config = {
                **dict(spec.strategy_config),
                "parameters": dict(parameters),
            }
            identity = {
                "dataset_id": dataset_id,
                "dataset_sha256": dataset_sha256,
                "code_revision": code_revision,
                "dataset_provenance": dict(dataset_provenance),
                "candidate_family": spec.candidate_family,
                "strategy_id": spec.strategy_id,
                "strategy_config": strategy_config,
                "feature_config": dict(spec.feature_config),
                "cost_config": list(cost_payload),
                "fold_config": dict(fold_config),
                "seed": spec.seed,
            }
            _canonical(identity)
            items.append(_WorkItem(
                spec=spec,
                parameters=dict(parameters),
                strategy_config=strategy_config,
                identity=identity,
                experiment_id=_identity("exp", identity),
            ))
    return items


def _run_experiment(
    *,
    root: Path,
    batch_id: str,
    item: _WorkItem,
    candles: Sequence[Candle],
    dataset_id: str,
    dataset_sha256: str,
    code_revision: str,
    cost_scenarios: Sequence[SpotCostScenario],
    fold_config: Mapping[str, Any],
    retry_failed: bool,
) -> dict[str, Any]:
    run_root = root / "runs" / item.experiment_id
    run_root.mkdir(parents=True, exist_ok=True)
    attempts = sorted(
        (path for path in run_root.glob("attempt-[0-9][0-9][0-9][0-9]")),
        key=lambda path: path.name,
    )
    attempt_path: Path | None = attempts[-1] if attempts else None

    if attempt_path is not None:
        _verify_attempt_manifest(attempt_path / "manifest.json", item, batch_id)
        with _exclusive_lock(attempt_path / ".lock"):
            events = _read_events(attempt_path / "events.jsonl")
            complete = _read_complete(attempt_path)
            if complete is not None:
                _ensure_completion_event(attempt_path, complete, events)
                return _completed_summary(item, attempt_path, complete)
            failed = any(event["kind"] == "ATTEMPT_FAILED" for event in events)
            if failed:
                if not retry_failed:
                    return {
                        "experiment_id": item.experiment_id,
                        "candidate_family": item.spec.candidate_family,
                        "strategy_id": item.spec.strategy_id,
                        "status": "NEEDS_RETRY",
                    }
                attempt_path = None

    if attempt_path is None:
        attempt_number = int(attempts[-1].name[-4:]) + 1 if attempts else 1
        attempt_path = run_root / f"attempt-{attempt_number:04d}"
        attempt_path.mkdir(parents=False, exist_ok=False)
        manifest = {
            "schema_version": 1,
            "batch_id": batch_id,
            "experiment_id": item.experiment_id,
            "attempt": attempt_number,
            "identity": dict(item.identity),
            "created_utc": datetime.now(timezone.utc).isoformat(),
        }
        _write_once(attempt_path / "manifest.json", _json_bytes(manifest))

    with _exclusive_lock(attempt_path / ".lock"):
        events = _read_events(attempt_path / "events.jsonl")
        complete = _read_complete(attempt_path)
        if complete is not None:
            _ensure_completion_event(attempt_path, complete, events)
            return _completed_summary(item, attempt_path, complete)
        cached: dict[tuple[int, str], WalkForwardFoldRun] = {}
        for event in events:
            if event["kind"] == "FOLD_COMPLETED":
                fold_run = fold_run_from_dict(event["fold_run"])
                key = (fold_run.fold_id, fold_run.result.cost_scenario)
                if key in cached:
                    raise ResearchBatchError(f"duplicate persisted fold event: {key}")
                cached[key] = fold_run
        if any(event["kind"] == "ATTEMPT_FAILED" for event in events):
            return {
                "experiment_id": item.experiment_id,
                "candidate_family": item.spec.candidate_family,
                "strategy_id": item.spec.strategy_id,
                "status": "NEEDS_RETRY",
            }

        try:
            report = run_walk_forward(
                candles,
                lambda seed: item.spec.strategy_factory(seed, item.parameters),
                cost_scenarios,
                dataset_id=dataset_id,
                dataset_sha256=dataset_sha256,
                code_revision=code_revision,
                candidate_family=item.spec.candidate_family,
                strategy_id=item.spec.strategy_id,
                strategy_config=item.strategy_config,
                feature_config=dict(item.spec.feature_config),
                n_folds=int(fold_config["n_folds"]),
                window_mode=str(fold_config["window_mode"]),  # type: ignore[arg-type]
                purge_s=float(fold_config["purge_s"]),
                embargo_s=float(fold_config["embargo_s"]),
                seed=item.spec.seed,
                resume_completed=cached,
                on_fold_complete=lambda run: _append_event(
                    attempt_path / "events.jsonl",
                    "FOLD_COMPLETED",
                    {"fold_run": run.to_dict()},
                ),
            )
        except Exception as exc:
            _append_event(
                attempt_path / "events.jsonl",
                "ATTEMPT_FAILED",
                {"error_type": type(exc).__name__, "failure_reason": str(exc)},
            )
            return {
                "experiment_id": item.experiment_id,
                "candidate_family": item.spec.candidate_family,
                "strategy_id": item.spec.strategy_id,
                "attempt": int(attempt_path.name[-4:]),
                "status": "FAILED",
                "failure_reason": str(exc),
            }

        payload = report.to_dict()
        _write_once(attempt_path / "metrics.json", _json_bytes(payload))
        _write_once(attempt_path / "run_result.json", _json_bytes(payload))
        completion = {
            "schema_version": 1,
            "experiment_id": item.experiment_id,
            "attempt": int(attempt_path.name[-4:]),
            "metrics_sha256": _sha256(_json_bytes(payload)),
            "fold_count": len(report.folds),
            "unsupported_fold_count": sum(
                run.result.status is ResultStatus.UNSUPPORTED for run in report.folds
            ),
            "completed_utc": datetime.now(timezone.utc).isoformat(),
        }
        _write_once(attempt_path / "complete.json", _json_bytes(completion))
        _append_event(attempt_path / "events.jsonl", "ATTEMPT_COMPLETED", completion)

    return {
        "experiment_id": item.experiment_id,
        "candidate_family": item.spec.candidate_family,
        "strategy_id": item.spec.strategy_id,
        "attempt": int(attempt_path.name[-4:]),
        "status": "COMPLETE",
        "fold_count": len(report.folds),
        "unsupported_fold_count": completion["unsupported_fold_count"],
        "metrics": str(attempt_path / "metrics.json"),
    }


def _completed_summary(
    item: _WorkItem, attempt_path: Path, completion: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "experiment_id": item.experiment_id,
        "candidate_family": item.spec.candidate_family,
        "strategy_id": item.spec.strategy_id,
        "attempt": int(attempt_path.name[-4:]),
        "status": "COMPLETE",
        "fold_count": completion["fold_count"],
        "unsupported_fold_count": completion["unsupported_fold_count"],
        "metrics": str(attempt_path / "metrics.json"),
    }


def _ensure_completion_event(
    attempt_path: Path,
    completion: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> None:
    terminal = [event for event in events if event["kind"] == "ATTEMPT_COMPLETED"]
    if not terminal:
        _append_event(attempt_path / "events.jsonl", "ATTEMPT_COMPLETED", completion)
        return
    if len(terminal) != 1 or any(
        terminal[0].get(key) != value for key, value in completion.items()
    ):
        raise ResearchBatchError(f"completion event does not match terminal manifest at {attempt_path}")


def _baseline_comparisons(runs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    baseline_ids = {"cash", "buy_and_hold", "randomized_placebo"}
    baselines = [
        run for run in runs
        if run.get("strategy_id") in baseline_ids and run.get("status") == "COMPLETE"
    ]
    comparisons: list[dict[str, Any]] = []
    for candidate in runs:
        if candidate.get("strategy_id") in baseline_ids or candidate.get("status") != "COMPLETE":
            continue
        try:
            candidate_metrics = json.loads(Path(candidate["metrics"]).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ResearchBatchError("completed run has unreadable metrics for baseline comparison") from exc
        candidate_rows = _fold_cost_index(candidate_metrics)
        for baseline in baselines:
            try:
                baseline_metrics = json.loads(Path(baseline["metrics"]).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
                raise ResearchBatchError("completed baseline has unreadable metrics") from exc
            baseline_rows = _fold_cost_index(baseline_metrics)
            for fold_cost in sorted(set(candidate_rows) & set(baseline_rows)):
                candidate_result = candidate_rows[fold_cost]
                baseline_result = baseline_rows[fold_cost]
                candidate_return = candidate_result.get("net_return")
                baseline_return = baseline_result.get("net_return")
                difference = (
                    candidate_return - baseline_return
                    if isinstance(candidate_return, (int, float))
                    and isinstance(baseline_return, (int, float))
                    else None
                )
                comparisons.append({
                    "candidate_experiment_id": candidate["experiment_id"],
                    "candidate_strategy_id": candidate["strategy_id"],
                    "baseline_experiment_id": baseline["experiment_id"],
                    "baseline_strategy_id": baseline["strategy_id"],
                    "fold": fold_cost[0],
                    "cost_scenario": fold_cost[1],
                    "candidate_net_return": candidate_return,
                    "baseline_net_return": baseline_return,
                    "net_return_difference": difference,
                    "status": (
                        "UNSUPPORTED"
                        if candidate_result.get("status") != ResultStatus.COMPLETED.value
                        or baseline_result.get("status") != ResultStatus.COMPLETED.value
                        else "COMPARABLE"
                    ),
                })
    return comparisons


def _fold_cost_index(metrics: Mapping[str, Any]) -> dict[tuple[int, str], Mapping[str, Any]]:
    rows: dict[tuple[int, str], Mapping[str, Any]] = {}
    for fold in metrics.get("folds", []):
        result = fold.get("result")
        if not isinstance(result, Mapping):
            raise ResearchBatchError("metrics fold is missing its standardized result")
        key = (int(result["fold"]), str(result["cost_scenario"]))
        if key in rows:
            raise ResearchBatchError(f"duplicate fold/cost result in metrics: {key}")
        rows[key] = result
    return rows


def _verify_attempt_manifest(path: Path, item: _WorkItem, batch_id: str) -> None:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchBatchError(f"invalid attempt manifest at {path}") from exc
    if (
        manifest.get("schema_version") != 1
        or manifest.get("batch_id") != batch_id
        or manifest.get("experiment_id") != item.experiment_id
        or manifest.get("identity") != dict(item.identity)
    ):
        raise ResearchBatchError(f"attempt manifest identity mismatch at {path}")


def _read_complete(attempt_path: Path) -> dict[str, Any] | None:
    path = attempt_path / "complete.json"
    if not path.exists():
        return None
    try:
        completion = json.loads(path.read_text(encoding="utf-8"))
        metrics_bytes = (attempt_path / "metrics.json").read_bytes()
        result_bytes = (attempt_path / "run_result.json").read_bytes()
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchBatchError(f"incomplete terminal record at {attempt_path}") from exc
    if completion.get("metrics_sha256") != _sha256(metrics_bytes) or metrics_bytes != result_bytes:
        raise ResearchBatchError(f"terminal metrics integrity mismatch at {attempt_path}")
    return completion


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    previous_hash = "0" * 64
    with path.open("r", encoding="utf-8") as stream:
        for sequence, line in enumerate(stream):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ResearchBatchError(f"invalid event log JSON at {path}:{sequence + 1}") from exc
            supplied_hash = event.pop("event_hash", None)
            if (
                event.get("sequence") != sequence
                or event.get("previous_hash") != previous_hash
                or supplied_hash != _sha256(_canonical(event))
            ):
                raise ResearchBatchError(f"event log hash chain is invalid at {path}:{sequence + 1}")
            event["event_hash"] = supplied_hash
            previous_hash = supplied_hash
            events.append(event)
    if events and events[-1]["kind"] == "ATTEMPT_COMPLETED":
        return events
    if any(event["kind"] == "ATTEMPT_COMPLETED" for event in events):
        raise ResearchBatchError(f"attempt event log contains records after completion at {path}")
    return events


def _append_event(path: Path, kind: str, payload: Mapping[str, Any]) -> None:
    event: dict[str, Any] = {
        "sequence": 0,
        "kind": kind,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        **dict(payload),
    }
    existing = _read_events(path)
    event["sequence"] = len(existing)
    event["previous_hash"] = existing[-1]["event_hash"] if existing else "0" * 64
    event["event_hash"] = _sha256(_canonical(event))
    encoded = (_canonical(event) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        offset = 0
        while offset < len(encoded):
            offset += os.write(fd, encoded[offset:])
        os.fsync(fd)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _ensure_identity_manifest(path: Path, identity: str, payload: Mapping[str, Any]) -> None:
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ResearchBatchError(f"invalid batch manifest at {path}") from exc
        if existing.get("batch_id") != identity or existing.get("identity") != dict(payload):
            raise ResearchBatchError("content identity collides with existing batch evidence")
        return
    manifest = {
        "schema_version": 1,
        "batch_id": identity,
        "identity": dict(payload),
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    _write_once(path, _json_bytes(manifest))


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ResearchBatchError(f"refusing to overwrite prior evidence: {path}")
        return
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp_path, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ResearchBatchError(f"refusing to overwrite prior evidence: {path}")
        parent_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        temp_path.unlink(missing_ok=True)


class _exclusive_lock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> None:
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)

    def __exit__(self, *_: Any) -> None:
        assert self.fd is not None
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def _identity(prefix: str, value: Mapping[str, Any]) -> str:
    return f"{prefix}_" + _sha256(_canonical(value))


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ResearchBatchError("batch identity and evidence must be finite JSON data") from exc


def _json_bytes(value: Any) -> bytes:
    return (_canonical(value) + "\n").encode("utf-8")


def _sha256(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()
