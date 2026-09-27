"""Append-only, hash-chained candidate lifecycle evidence registry."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping


class CandidateLifecycle(str, Enum):
    HYPOTHESIS = "HYPOTHESIS"
    RETROSPECTIVE_EXPERIMENT = "RETROSPECTIVE_EXPERIMENT"
    ROBUSTNESS_TESTED = "ROBUSTNESS_TESTED"
    CANDIDATE = "CANDIDATE"
    FROZEN = "FROZEN"
    PAPER = "PAPER"
    PROSPECTIVE_COMPLETE = "PROSPECTIVE_COMPLETE"
    LIVE_ELIGIBLE = "LIVE_ELIGIBLE"


class CandidateLifecycleError(ValueError):
    """Raised when an append-only candidate transition fails its evidence gate."""


_NEXT_STATUS = {
    CandidateLifecycle.HYPOTHESIS: CandidateLifecycle.RETROSPECTIVE_EXPERIMENT,
    CandidateLifecycle.RETROSPECTIVE_EXPERIMENT: CandidateLifecycle.ROBUSTNESS_TESTED,
    CandidateLifecycle.ROBUSTNESS_TESTED: CandidateLifecycle.CANDIDATE,
    CandidateLifecycle.CANDIDATE: CandidateLifecycle.FROZEN,
    CandidateLifecycle.FROZEN: CandidateLifecycle.PAPER,
    CandidateLifecycle.PAPER: CandidateLifecycle.PROSPECTIVE_COMPLETE,
    CandidateLifecycle.PROSPECTIVE_COMPLETE: CandidateLifecycle.LIVE_ELIGIBLE,
}
_EXTERNAL_ROLE_TOKENS = ("EXTERNAL", "HYPOTHESIS_GENERATION_ONLY")


class CandidateRegistry:
    """Append-only JSONL lifecycle ledger; it records evidence, never executes trades."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self.verify_ledger()

    def create_hypothesis(self, candidate_id: str, definition: Mapping[str, Any]) -> dict[str, Any]:
        if not candidate_id.strip():
            raise CandidateLifecycleError("candidate_id must be non-empty")
        _validate_hypothesis_definition(definition)
        origin = str(definition["origin"])
        tags = definition.get("origin_tags", [])
        if _is_external_origin(origin, tags) and "HYPOTHESIS_GENERATION_ONLY" not in {
            str(tag).upper() for tag in tags
        }:
            raise CandidateLifecycleError("external-origin hypotheses must be tagged HYPOTHESIS_GENERATION_ONLY")
        with self._locked_file() as fd:
            events = self._read_events_fd(fd)
            if any(event["candidate_id"] == candidate_id for event in events):
                raise CandidateLifecycleError(f"candidate {candidate_id!r} already exists")
            return self._append_event(
                fd,
                events,
                candidate_id,
                None,
                CandidateLifecycle.HYPOTHESIS,
                {"definition": dict(definition)},
            )

    def transition(
        self,
        candidate_id: str,
        target: CandidateLifecycle | str,
        evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            target_status = CandidateLifecycle(target)
        except ValueError as exc:
            raise CandidateLifecycleError(f"unknown lifecycle status: {target}") from exc
        with self._locked_file() as fd:
            events = self._read_events_fd(fd)
            history = [event for event in events if event["candidate_id"] == candidate_id]
            if not history:
                raise CandidateLifecycleError(f"candidate {candidate_id!r} is not registered")
            current = CandidateLifecycle(history[-1]["to_status"])
            expected = _NEXT_STATUS.get(current)
            if target_status != expected:
                raise CandidateLifecycleError(
                    f"invalid transition for {candidate_id}: {current.value} -> {target_status.value}; "
                    f"expected {expected.value if expected else 'no further transition'}"
                )
            _validate_transition_evidence(target_status, evidence)
            if target_status == CandidateLifecycle.ROBUSTNESS_TESTED and _contains_external_role(
                history[-1]["evidence"].get("dataset_roles", [])
            ):
                raise CandidateLifecycleError(
                    "external/HYPOTHESIS_GENERATION_ONLY experiments cannot advance beyond retrospective research"
                )
            if target_status != CandidateLifecycle.RETROSPECTIVE_EXPERIMENT:
                roles = evidence.get("dataset_roles", [])
                if _contains_external_role(roles):
                    raise CandidateLifecycleError(
                        "external/HYPOTHESIS_GENERATION_ONLY datasets cannot advance candidate lifecycle"
                    )
            return self._append_event(
                fd,
                events,
                candidate_id,
                current,
                target_status,
                dict(evidence),
            )

    def events_for(self, candidate_id: str) -> list[dict[str, Any]]:
        return [event for event in self.verify_ledger() if event["candidate_id"] == candidate_id]

    def verify_ledger(self) -> list[dict[str, Any]]:
        with self._locked_file() as fd:
            return self._read_events_fd(fd)

    @staticmethod
    def verify_events(events: list[dict[str, Any]]) -> None:
        """Verify a portable candidate event chain included in a readiness bundle."""
        previous_by_candidate: dict[str, dict[str, Any]] = {}
        sequence_by_candidate: dict[str, int] = {}
        for event in events:
            candidate_id = event.get("candidate_id")
            if not isinstance(candidate_id, str) or not candidate_id:
                raise CandidateLifecycleError("candidate event has no candidate_id")
            previous = previous_by_candidate.get(candidate_id)
            expected_sequence = sequence_by_candidate.get(candidate_id, 0)
            if event.get("sequence") != expected_sequence:
                raise CandidateLifecycleError("candidate event sequence is not contiguous")
            if previous is None:
                if event.get("from_status") is not None or event.get("to_status") != CandidateLifecycle.HYPOTHESIS.value:
                    raise CandidateLifecycleError("candidate chain must begin at HYPOTHESIS")
                if event.get("previous_hash") != "0" * 64:
                    raise CandidateLifecycleError("initial candidate event has an invalid previous hash")
            else:
                if event.get("from_status") != previous["to_status"]:
                    raise CandidateLifecycleError("candidate event has a broken state transition")
                if (
                    previous["to_status"] == CandidateLifecycle.RETROSPECTIVE_EXPERIMENT.value
                    and event.get("to_status") == CandidateLifecycle.ROBUSTNESS_TESTED.value
                    and _contains_external_role(previous["evidence"].get("dataset_roles", []))
                ):
                    raise CandidateLifecycleError(
                        "external/HYPOTHESIS_GENERATION_ONLY experiments cannot advance beyond retrospective research"
                    )
                if event.get("previous_hash") != previous["event_hash"]:
                    raise CandidateLifecycleError("candidate event hash chain is broken")
                try:
                    expected = _NEXT_STATUS[CandidateLifecycle(previous["to_status"])].value
                except (KeyError, ValueError) as exc:
                    raise CandidateLifecycleError("candidate event continues after a terminal state") from exc
                if event.get("to_status") != expected:
                    raise CandidateLifecycleError("candidate lifecycle transition is invalid")
            _validate_event_payload(event)
            if event.get("event_hash") != _event_hash(event):
                raise CandidateLifecycleError("candidate event hash mismatch")
            previous_by_candidate[candidate_id] = event
            sequence_by_candidate[candidate_id] = expected_sequence + 1

    def _append_event(
        self,
        fd: int,
        events: list[dict[str, Any]],
        candidate_id: str,
        from_status: CandidateLifecycle | None,
        to_status: CandidateLifecycle,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        history = [event for event in events if event["candidate_id"] == candidate_id]
        previous_hash = history[-1]["event_hash"] if history else "0" * 64
        event: dict[str, Any] = {
            "schema_version": 1,
            "candidate_id": candidate_id,
            "sequence": len(history),
            "from_status": from_status.value if from_status else None,
            "to_status": to_status.value,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "previous_hash": previous_hash,
            "evidence": evidence,
        }
        event["event_hash"] = _event_hash(event)
        _validate_event_payload(event)
        encoded = (json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
        offset = 0
        while offset < len(encoded):
            offset += os.write(fd, encoded[offset:])
        os.fsync(fd)
        return event

    def _locked_file(self):
        class LockedFile:
            def __init__(self, path: Path) -> None:
                self.path = path
                self.fd: int | None = None

            def __enter__(self) -> int:
                self.fd = os.open(self.path, os.O_RDWR | os.O_APPEND)
                fcntl.flock(self.fd, fcntl.LOCK_EX)
                return self.fd

            def __exit__(self, *_: Any) -> None:
                assert self.fd is not None
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                os.close(self.fd)

        return LockedFile(self.path)

    @staticmethod
    def _read_events_fd(fd: int) -> list[dict[str, Any]]:
        os.lseek(fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(fd), "r", encoding="utf-8") as stream:
            events = []
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    raise CandidateLifecycleError(f"blank line in append-only candidate ledger at {line_number}")
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise CandidateLifecycleError(f"invalid candidate ledger JSON at line {line_number}") from exc
                if not isinstance(event, dict):
                    raise CandidateLifecycleError(f"candidate ledger line {line_number} is not an object")
                events.append(event)
        CandidateRegistry.verify_events(events)
        return events


def _validate_hypothesis_definition(definition: Mapping[str, Any]) -> None:
    if not isinstance(definition, Mapping):
        raise CandidateLifecycleError("hypothesis definition must be an object")
    required = (
        "hypothesis_id",
        "origin",
        "economic_intuition",
        "required_data",
        "feature_definitions",
        "entry_concept",
        "exit_concept",
        "risk_concept",
        "known_confounders",
        "falsification_criteria",
    )
    missing = [name for name in required if name not in definition or definition[name] in (None, "", [], {})]
    if missing:
        raise CandidateLifecycleError(f"hypothesis definition fields missing: {', '.join(missing)}")
    for name in (
        "hypothesis_id", "origin", "economic_intuition", "entry_concept", "exit_concept", "risk_concept"
    ):
        if not isinstance(definition[name], str) or not definition[name].strip():
            raise CandidateLifecycleError(f"hypothesis {name} must be a non-empty string")
    for name in ("required_data", "known_confounders", "falsification_criteria"):
        if not isinstance(definition[name], list) or any(not isinstance(item, str) or not item.strip() for item in definition[name]):
            raise CandidateLifecycleError(f"hypothesis {name} must be a non-empty list of strings")
    if not isinstance(definition["feature_definitions"], dict) or not definition["feature_definitions"]:
        raise CandidateLifecycleError("hypothesis feature_definitions must be a non-empty object")
    origin_tags = definition.get("origin_tags", [])
    if not isinstance(origin_tags, list) or any(not isinstance(tag, str) for tag in origin_tags):
        raise CandidateLifecycleError("origin_tags must be a list of strings")
    if _is_external_origin(definition["origin"], origin_tags) and "HYPOTHESIS_GENERATION_ONLY" not in {
        str(tag).upper() for tag in origin_tags
    }:
        raise CandidateLifecycleError("external-origin hypotheses must be tagged HYPOTHESIS_GENERATION_ONLY")


def _validate_transition_evidence(target: CandidateLifecycle, evidence: Mapping[str, Any]) -> None:
    requirements: dict[CandidateLifecycle, tuple[str, ...]] = {
        CandidateLifecycle.RETROSPECTIVE_EXPERIMENT: (
            "experiment_id", "dataset_manifest_sha256", "result_manifest_sha256", "code_commit",
            "strategy_config_sha256", "feature_definition_sha256", "cost_model_sha256",
            "latency_assumptions_sha256", "provenance_sha256", "metrics_sha256", "dataset_roles",
            "training_range", "validation_range", "random_seed", "deterministic_rerun",
            "purge_embargo_seconds",
        ),
        CandidateLifecycle.ROBUSTNESS_TESTED: (
            "robustness_report_sha256", "baseline_report_sha256", "placebo_report_sha256", "walk_forward_status", "cost_scenarios",
        ),
        CandidateLifecycle.CANDIDATE: (
            "promotion_report_sha256", "acceptance_rules_sha256", "decision",
        ),
        CandidateLifecycle.FROZEN: (
            "freeze_hash", "candidate_freeze_sha256", "source_research_sha256",
        ),
        CandidateLifecycle.PAPER: (
            "paper_run_id", "explicit_authorization_sha256", "backend", "private_api_enabled", "live_enabled",
        ),
        CandidateLifecycle.PROSPECTIVE_COMPLETE: ("paper_report_sha256", "outcome"),
        CandidateLifecycle.LIVE_ELIGIBLE: ("security_review_sha256", "explicit_live_go_sha256"),
    }
    missing = [name for name in requirements[target] if name not in evidence]
    if missing:
        raise CandidateLifecycleError(f"{target.value} evidence fields missing: {', '.join(missing)}")
    hash_fields = [name for name in requirements[target] if name.endswith("_sha256") or name in {"freeze_hash", "code_commit"}]
    for name in hash_fields:
        expected_lengths = {40, 64} if name == "code_commit" else {64}
        value = evidence[name]
        if not isinstance(value, str) or len(value) not in expected_lengths or re.fullmatch(r"[0-9a-fA-F]+", value) is None:
            raise CandidateLifecycleError(f"{target.value} evidence {name} must be a SHA-256 or Git commit hash")
    if target == CandidateLifecycle.RETROSPECTIVE_EXPERIMENT:
        if not isinstance(evidence["dataset_roles"], list) or not evidence["dataset_roles"]:
            raise CandidateLifecycleError("retrospective evidence requires dataset roles")
        if not isinstance(evidence["experiment_id"], str) or not evidence["experiment_id"].strip():
            raise CandidateLifecycleError("retrospective experiment_id must be non-empty")
        _, training_end = _validate_utc_range("training_range", evidence["training_range"])
        validation_start, _ = _validate_utc_range("validation_range", evidence["validation_range"])
        if training_end > validation_start:
            raise CandidateLifecycleError("training and validation ranges must be chronological and non-overlapping")
        seed = evidence["random_seed"]
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise CandidateLifecycleError("random_seed must be a non-negative integer")
        rerun_status = evidence["deterministic_rerun"]
        if not isinstance(rerun_status, str) or rerun_status not in {"PASS", "CONTROLLED_NONDETERMINISM"}:
            raise CandidateLifecycleError("deterministic_rerun must be PASS or CONTROLLED_NONDETERMINISM")
        if rerun_status == "CONTROLLED_NONDETERMINISM" and not evidence.get("nondeterminism_description"):
            raise CandidateLifecycleError("controlled nondeterminism requires a description")
        purge = evidence["purge_embargo_seconds"]
        if isinstance(purge, bool) or not isinstance(purge, (int, float)) or not math.isfinite(purge) or purge < 0:
            raise CandidateLifecycleError("purge_embargo_seconds must be non-negative")
    elif target == CandidateLifecycle.ROBUSTNESS_TESTED:
        if evidence["walk_forward_status"] != "PASS" or not isinstance(evidence["cost_scenarios"], list) or len(evidence["cost_scenarios"]) < 2:
            raise CandidateLifecycleError("robustness evidence requires PASS walk-forward and at least two cost scenarios")
    elif target == CandidateLifecycle.CANDIDATE and evidence["decision"] != "PASS":
        raise CandidateLifecycleError("candidate promotion requires an explicit PASS decision")
    elif target == CandidateLifecycle.PAPER:
        if evidence["backend"] != "PAPER" or evidence["private_api_enabled"] is not False or evidence["live_enabled"] is not False:
            raise CandidateLifecycleError("PAPER evidence must keep live/private execution disabled")
    elif target == CandidateLifecycle.PROSPECTIVE_COMPLETE and evidence["outcome"] not in {"PASS", "FAIL"}:
        raise CandidateLifecycleError("prospective outcome must be historically PASS or FAIL")


def _validate_event_payload(event: Mapping[str, Any]) -> None:
    if event.get("schema_version") != 1:
        raise CandidateLifecycleError("unsupported candidate event schema")
    timestamp = event.get("timestamp_utc")
    if not isinstance(timestamp, str):
        raise CandidateLifecycleError("candidate event timestamp is missing")
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CandidateLifecycleError("candidate event timestamp is invalid") from exc
    offset = parsed_timestamp.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise CandidateLifecycleError("candidate event timestamp must be UTC")
    try:
        target = CandidateLifecycle(event["to_status"])
    except (KeyError, ValueError) as exc:
        raise CandidateLifecycleError("candidate event has an unknown status") from exc
    evidence = event.get("evidence")
    if not isinstance(evidence, dict):
        raise CandidateLifecycleError("candidate event evidence must be an object")
    if target == CandidateLifecycle.HYPOTHESIS:
        _validate_hypothesis_definition(evidence.get("definition", {}))
        return
    _validate_transition_evidence(target, evidence)
    if target != CandidateLifecycle.RETROSPECTIVE_EXPERIMENT and _contains_external_role(
        evidence.get("dataset_roles", [])
    ):
        raise CandidateLifecycleError("external hypothesis-generation data cannot advance candidates")


def _contains_external_role(roles: Any) -> bool:
    if not isinstance(roles, (list, tuple, set)):
        return True
    return any(any(token in str(role).upper() for token in _EXTERNAL_ROLE_TOKENS) for role in roles)


def _is_external_origin(origin: Any, tags: list[Any]) -> bool:
    text = " ".join([str(origin), *(str(tag) for tag in tags)]).upper()
    return any(token in text for token in ("EXTERNAL", "BITMEX", "AOA", "EXPERT"))


def _validate_utc_range(name: str, value: Any) -> tuple[datetime, datetime]:
    if not isinstance(value, dict) or set(value) != {"start_utc", "end_utc"}:
        raise CandidateLifecycleError(f"{name} must contain start_utc and end_utc")
    parsed: list[datetime] = []
    for field in ("start_utc", "end_utc"):
        item = value[field]
        if not isinstance(item, str) or not item.endswith("Z"):
            raise CandidateLifecycleError(f"{name}.{field} must be an ISO-8601 UTC timestamp ending in Z")
        try:
            stamp = datetime.fromisoformat(item[:-1] + "+00:00")
        except ValueError as exc:
            raise CandidateLifecycleError(f"{name}.{field} is not a valid timestamp") from exc
        if stamp.utcoffset() is None:
            raise CandidateLifecycleError(f"{name}.{field} must include UTC timezone")
        parsed.append(stamp)
    if parsed[0] >= parsed[1]:
        raise CandidateLifecycleError(f"{name} must have start_utc earlier than end_utc")
    return parsed[0], parsed[1]


def _event_hash(event: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in event.items() if key != "event_hash"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
