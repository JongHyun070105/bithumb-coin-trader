#!/usr/bin/env python3
"""Fail-closed, offline auditor for Fresh30HTerminalBundle v2.

All trust anchors (run identity and the original sealed-manifest digest) are
passed independently on the command line. A bundle cannot establish its own
trust by rewriting its manifest and hash index.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any, Mapping, Sequence

PASS, FAIL, NOT_VERIFIABLE = "PASS", "FAIL", "NOT_VERIFIABLE"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FEED_STREAMS = {
    "bithumb": ("orderbook", "trade", "ticker"),
    "binance": ("trade", "orderbook"),
    "upbit": ("orderbook", "trade"),
}
REQUIRED_PAYLOADS = (
    "sealed/sealed-manifest.json", "sealed/identity.json", "sealed/runtime.json",
    "terminal/capture-manifest.json",
    "terminal/result.json", "terminal/collector-lifecycle.json",
    "terminal/collector-metrics.json", "terminal/systemd-terminal.json",
    "terminal/systemd-invocation.jsonl", "terminal/terminal-witness.json",
    "terminal/terminal-receipt.json", "terminal/s3-readback.json",
    "terminal/s3-readback/terminal-receipt.json", "terminal/cohorts.json",
    "terminal/receipt-inventory.json", "terminal/finalization-trace.json",
)


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    summary: str
    details: Mapping[str, Any]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo is not None else None


def qualifying_hours(actual_start: datetime, actual_end: datetime) -> tuple[list[str], str | None, str | None]:
    """Return fully contained UTC hours and touched partial edge hour IDs."""
    if actual_start.tzinfo is None or actual_end.tzinfo is None or actual_end <= actual_start:
        raise ValueError("start/end must be aware datetimes with end after start")
    start, end = actual_start.astimezone(timezone.utc), actual_end.astimezone(timezone.utc)
    start_floor = start.replace(minute=0, second=0, microsecond=0)
    end_floor = end.replace(minute=0, second=0, microsecond=0)
    # FeedHourCoverageTracker always marks the opening cohort TOUCHED_PARTIAL,
    # including an exact :00 start. freeze_shutdown also emits the ending
    # cohort; an exact-boundary end may therefore be a zero-duration partial.
    start_partial = _hour_id(start_floor)
    end_partial = _hour_id(end_floor)
    cursor = start_floor + timedelta(hours=1)
    hours: list[str] = []
    while cursor + timedelta(hours=1) <= end:
        hours.append(_hour_id(cursor))
        cursor += timedelta(hours=1)
    return hours, start_partial, end_partial


def _hour_id(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d_%H")


def expected_feed_ids(runtime: Mapping[str, Any]) -> list[str] | None:
    feeds = runtime.get("feeds")
    if not isinstance(feeds, Mapping):
        return None
    out: list[str] = []
    for exchange, field in (("bithumb", "bithumb_markets"), ("binance", "binance_symbols"), ("upbit", "upbit_markets")):
        values = feeds.get(field)
        if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
            return None
        for market in values:
            out.extend(f"{exchange}:{stream}:{market}" for stream in FEED_STREAMS[exchange])
    return out


class AuditorV2:
    def __init__(self, root: Path, *, run_id: str | None, epoch: str | None,
                 runtime_commit: str | None, runtime_tree: str | None,
                 sealed_manifest_sha256: str | None,
                 capture_manifest_sha256: str | None) -> None:
        self.root = root.resolve()
        self.run_id, self.epoch = run_id, epoch
        self.runtime_commit, self.runtime_tree = runtime_commit, runtime_tree
        self.anchor = sealed_manifest_sha256
        self.capture_anchor = capture_manifest_sha256
        self.checks: list[Check] = []
        self.manifest: dict[str, Any] | None = None
        self.payloads: dict[str, Path] = {}

    def add(self, name: str, status: str, summary: str, **details: Any) -> None:
        self.checks.append(Check(name, status, summary, details))

    def read_json(self, rel: str) -> dict[str, Any] | None:
        path = self.payloads.get(rel, self.root / rel)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def audit(self) -> dict[str, Any]:
        self._audit_index_and_manifest()
        self._audit_capture_manifest()
        identity = self.read_json("sealed/identity.json")
        sealed = self.read_json("sealed/sealed-manifest.json")
        runtime = self.read_json("sealed/runtime.json")
        self._audit_bundle_scope(identity, sealed)
        self._audit_identity(identity, sealed, runtime)
        result = self.read_json("terminal/result.json")
        lifecycle = self.read_json("terminal/collector-lifecycle.json")
        metrics = self.read_json("terminal/collector-metrics.json")
        systemd = self.read_json("terminal/systemd-terminal.json")
        witness = self.read_json("terminal/terminal-witness.json")
        readback = self.read_json("terminal/s3-readback.json")
        self._audit_duration(result, lifecycle, metrics, runtime, systemd)
        self._audit_exits(result)
        self._audit_systemd(systemd)
        self._audit_witness(identity, witness, readback)
        self._audit_collector_state(lifecycle, metrics)
        self._audit_observer(lifecycle, self.run_id)
        self._audit_redundancy(runtime, metrics)
        self._audit_cohorts(runtime, result, metrics)
        self._audit_receipt_scope()
        self._audit_receipt_immutability()
        self._audit_finalization()
        statuses = {c.status for c in self.checks if c.details.get("informational") is not True}
        overall = FAIL if FAIL in statuses else NOT_VERIFIABLE if NOT_VERIFIABLE in statuses else PASS
        return {
            "schema": "Fresh30HTerminalAudit", "version": 2,
            "overall_status": overall,
            "checks": [asdict(c) for c in self.checks],
        }

    def _audit_index_and_manifest(self) -> None:
        manifest_path = self.root / "bundle-manifest.json"
        index_path = self.root / "terminal/evidence-hash-index.json"
        if manifest_path.is_symlink() or index_path.is_symlink():
            self.add("bundle_integrity", FAIL, "Manifest and index must be regular local files, not symlinks.")
            return
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.add("bundle_integrity", NOT_VERIFIABLE, "Manifest or hash index is unavailable or malformed.", error=str(exc))
            return
        if not isinstance(manifest, dict) or manifest.get("schema") != "Fresh30HTerminalBundle" or manifest.get("version") != 2:
            self.add("bundle_integrity", FAIL, "Bundle schema/version is invalid.")
            return
        self.manifest = manifest
        failures: list[str] = []
        if any(path.is_symlink() for path in self.root.rglob("*")):
            failures.append("symlink_present_in_bundle")
        for key, expected in (("run_id", self.run_id), ("epoch", self.epoch),
                              ("runtime_commit", self.runtime_commit), ("runtime_tree", self.runtime_tree),
                              ("external_sealed_manifest_sha256", self.anchor),
                              ("external_capture_manifest_sha256", self.capture_anchor)):
            if expected is not None and manifest.get(key) != expected:
                failures.append(f"manifest_anchor_mismatch:{key}")
        if not isinstance(index, dict) or index.get("schema") != 1 or not isinstance(index.get("files"), list):
            self.add("bundle_integrity", FAIL, "Hash index schema is invalid.")
            return
        seen: set[str] = set()
        listed = set()
        for item in index["files"]:
            if not isinstance(item, dict):
                failures.append("invalid_entry"); continue
            rel = item.get("path")
            if not isinstance(rel, str) or not _safe_relpath(rel) or rel in seen:
                failures.append(f"unsafe_or_duplicate:{rel}"); continue
            seen.add(rel); listed.add(rel)
            path = self.root / rel
            try:
                if path.is_symlink() or not path.is_file():
                    failures.append(f"not_regular_file:{rel}"); continue
                actual_size = path.stat().st_size
                actual_sha = sha256_file(path)
            except OSError:
                failures.append(f"missing:{rel}"); continue
            if actual_size != item.get("size") or actual_sha != item.get("sha256") or not SHA256_RE.fullmatch(str(item.get("sha256", ""))):
                failures.append(f"hash_or_size_mismatch:{rel}")
            self.payloads[rel] = path
        actual = {p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file() and p.relative_to(self.root).as_posix() != "terminal/evidence-hash-index.json"}
        if actual != listed:
            failures.append("unindexed_or_extra_payload")
        declared = manifest.get("payloads") if isinstance(manifest, dict) else None
        declared_map = {item.get("path"): (item.get("size"), item.get("sha256")) for item in declared if isinstance(item, dict)} if isinstance(declared, list) else {}
        index_payloads = {item.get("path"): (item.get("size"), item.get("sha256")) for item in index["files"] if isinstance(item, dict) and item.get("path") != "bundle-manifest.json"}
        if declared_map != index_payloads:
            failures.append("manifest_payload_inventory_disagrees_with_index")
        missing_required = sorted(set(REQUIRED_PAYLOADS) - listed)
        status = FAIL if failures else NOT_VERIFIABLE if missing_required else PASS
        self.add("bundle_integrity", status, "Payload inventory, paths, and hashes are internally consistent." if status == PASS else "Required evidence is missing." if status == NOT_VERIFIABLE else "Bundle inventory or hashes fail integrity checks.", failures=failures, missing_required=missing_required)

    def _audit_capture_manifest(self) -> None:
        rel = "terminal/capture-manifest.json"
        path = self.payloads.get(rel, self.root / rel)
        if not self.capture_anchor or not SHA256_RE.fullmatch(self.capture_anchor):
            self.add("capture_provenance", NOT_VERIFIABLE, "Independent post-run capture-manifest SHA256 anchor was not supplied.")
            return
        if path.is_symlink() or not path.is_file():
            self.add("capture_provenance", NOT_VERIFIABLE, "Post-run capture manifest is missing or not a regular file.")
            return
        failures: list[str] = []
        missing: list[str] = []
        if sha256_file(path) != self.capture_anchor:
            failures.append("external_capture_manifest_hash_mismatch")
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.add("capture_provenance", FAIL, "Post-run capture manifest is malformed.", error=str(exc), failures=failures)
            return
        if not isinstance(manifest, dict) or manifest.get("schema") != "Fresh30HTerminalCapture" or manifest.get("version") != 1:
            self.add("capture_provenance", FAIL, "Post-run capture manifest schema/version is invalid.", failures=failures)
            return
        anchors = {
            "run_id": self.run_id, "epoch": self.epoch,
            "runtime_commit": self.runtime_commit, "runtime_tree": self.runtime_tree,
        }
        for key, expected in anchors.items():
            if not expected or manifest.get(key) != expected:
                failures.append(f"capture_manifest_anchor_mismatch:{key}")
        records = manifest.get("sources")
        if not isinstance(records, list):
            self.add("capture_provenance", FAIL, "Post-run capture source inventory is missing or malformed.", failures=failures)
            return
        source_paths: set[str] = set()
        for record in records:
            if not isinstance(record, dict):
                failures.append("malformed_source_record")
                continue
            source_rel = record.get("path")
            if not isinstance(source_rel, str) or not _safe_relpath(source_rel) or source_rel in source_paths:
                failures.append(f"unsafe_or_duplicate_capture_source:{source_rel}")
                continue
            source_paths.add(source_rel)
            source_path = self.payloads.get(source_rel, self.root / source_rel)
            expected_sha, expected_size = record.get("sha256"), record.get("size")
            if source_path.is_symlink() or not source_path.is_file():
                missing.append(f"captured_source_missing:{source_rel}")
                continue
            if (not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha)
                    or not isinstance(expected_size, int) or expected_size < 0):
                failures.append(f"captured_source_record_invalid:{source_rel}")
                continue
            if source_path.stat().st_size != expected_size or sha256_file(source_path) != expected_sha:
                failures.append(f"captured_source_hash_or_size_mismatch:{source_rel}")
        required_sources = {item for item in REQUIRED_PAYLOADS
                            if item.startswith("terminal/") and item != rel}
        required_sources.discard("terminal/evidence-hash-index.json")
        missing_required = sorted(required_sources - source_paths)
        if missing_required:
            missing.extend(f"required_capture_source_unlisted:{source_rel}" for source_rel in missing_required)
        actual_terminal_sources = {
            item.relative_to(self.root).as_posix()
            for item in self.root.rglob("*")
            if item.is_file() and item.relative_to(self.root).as_posix().startswith("terminal/")
            and item.relative_to(self.root).as_posix() not in {rel, "terminal/evidence-hash-index.json"}
        }
        unbound = sorted(actual_terminal_sources - source_paths)
        if unbound:
            failures.extend(f"terminal_payload_not_capture_bound:{source_rel}" for source_rel in unbound)
        status = FAIL if failures else NOT_VERIFIABLE if missing else PASS
        self.add("capture_provenance", status,
                 "External capture anchor binds the terminal evidence source inventory." if status == PASS else "A captured terminal source is missing or the source inventory is incomplete." if status == NOT_VERIFIABLE else "Post-run source inventory or external capture anchor is inconsistent.",
                 failures=failures, source_count=len(source_paths), missing_sources=missing,
                 unbound_sources=unbound,
                 trust_anchor="independent SHA256 supplied from the preserved post-run capture record")

    def _audit_bundle_scope(self, identity: dict[str, Any] | None,
                            sealed: dict[str, Any] | None) -> None:
        if identity is None or sealed is None:
            self.add("bundle_scope", NOT_VERIFIABLE, "Sealed identity or manifest is unavailable for allowlist validation.")
            return
        artifact_hashes = sealed.get("artifact_hashes", sealed.get("sealed_artifact_hashes"))
        if not isinstance(artifact_hashes, dict):
            self.add("bundle_scope", NOT_VERIFIABLE, "Sealed artifact allowlist is missing or malformed.")
            return
        artifact_names = set(artifact_hashes)
        if any(not isinstance(name, str) or not name or PurePosixPath(name).name != name
               or name in (".", "..") for name in artifact_names):
            self.add("bundle_scope", FAIL, "Sealed artifact allowlist contains an unsafe name.")
            return
        allowed_fixed = {
            "bundle-manifest.json", "terminal/evidence-hash-index.json",
            "sealed/sealed-manifest.json", "sealed/identity.json", "sealed/runtime.json",
        }
        actual_files = {
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*") if path.is_file()
        }
        unallowed: list[str] = []
        duplicate_artifacts: list[str] = []
        observed_artifacts: dict[str, list[str]] = {}
        for rel in actual_files:
            if rel in allowed_fixed:
                continue
            if rel.startswith("terminal/"):
                # The externally anchored capture manifest is the allowlist for
                # terminal sources; its exact completeness is checked separately.
                continue
            parts = PurePosixPath(rel).parts
            if len(parts) == 3 and parts[:2] == ("sealed", "artifacts") and parts[-1] in artifact_names:
                observed_artifacts.setdefault(parts[-1], []).append(rel)
            else:
                unallowed.append(rel)
        for name in artifact_names:
            paths = observed_artifacts.get(name, [])
            if len(paths) != 1:
                duplicate_artifacts.append(name)
        status = FAIL if unallowed or duplicate_artifacts else PASS
        self.add("bundle_scope", status,
                 "Bundle contains only the versioned payload scope and one copy of every sealed artifact." if status == PASS else "Bundle contains unallowlisted files or missing/duplicate sealed copies.",
                 unallowed=sorted(unallowed), missing_or_duplicate_artifacts=sorted(duplicate_artifacts))

    def _audit_identity(self, identity: dict[str, Any] | None, sealed: dict[str, Any] | None,
                        runtime: dict[str, Any] | None) -> None:
        if identity is None or sealed is None or runtime is None:
            self.add("runtime_identity", NOT_VERIFIABLE, "Sealed identity, manifest, and runtime configuration are required.")
            return
        failures: list[str] = []
        missing_artifacts: list[str] = []
        if not self.anchor or not SHA256_RE.fullmatch(self.anchor):
            self.add("runtime_identity", NOT_VERIFIABLE, "External original sealed-manifest SHA256 anchor was not supplied.")
            return
        observed_manifest_sha = sha256_file(self.payloads.get("sealed/sealed-manifest.json", self.root / "sealed/sealed-manifest.json"))
        if observed_manifest_sha != self.anchor:
            failures.append("sealed_manifest_anchor_mismatch")
        identity_sha = sealed.get("identity_sha256")
        identity_path = self.payloads.get("sealed/identity.json", self.root / "sealed/identity.json")
        if not isinstance(identity_sha, str) or sha256_file(identity_path) != identity_sha:
            failures.append("identity_sha256_mismatch")
        expected = {
            "run_id": self.run_id, "epoch": self.epoch,
            "software_commit_sha": self.runtime_commit, "software_tree_sha": self.runtime_tree,
        }
        aliases = {"software_commit_sha": ("software_commit_sha", "runtime_commit"),
                   "software_tree_sha": ("software_tree_sha", "runtime_tree")}
        observed: dict[str, Any] = {}
        for key, target in expected.items():
            names = aliases.get(key, (key,))
            value = next((identity.get(name) for name in names if identity.get(name) is not None), None)
            observed[key] = value
            if not target:
                failures.append(f"external_anchor_missing:{key}")
            elif value != target:
                failures.append(f"identity_mismatch:{key}")
        if runtime.get("runtime_software_commit") != self.runtime_commit:
            failures.append("runtime_config_commit_mismatch")
        pinned_hashes = sealed.get("artifact_hashes", sealed.get("sealed_artifact_hashes"))
        declared_hashes = identity.get("sealed_artifact_hashes")
        if not isinstance(pinned_hashes, dict) or not isinstance(declared_hashes, dict) or pinned_hashes != declared_hashes:
            failures.append("sealed_hash_maps_disagree")
        elif isinstance(pinned_hashes, dict):
            by_name = {Path(rel).name: self.payloads.get(rel, self.root / rel) for rel in self.payloads}
            for name, expected_sha in pinned_hashes.items():
                artifact_path = by_name.get(name)
                if artifact_path is None or not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha):
                    missing_artifacts.append(name)
                elif sha256_file(artifact_path) != expected_sha:
                    failures.append(f"sealed_artifact_copy_mismatch:{name}")
            runtime_names = [name for name in pinned_hashes if name.endswith(".runtime.json")]
            runtime_copy = self.payloads.get("sealed/runtime.json", self.root / "sealed/runtime.json")
            if len(runtime_names) != 1:
                failures.append("runtime_sealed_artifact_ambiguous")
            elif not runtime_copy.is_file():
                missing_artifacts.append("sealed/runtime.json")
            elif sha256_file(runtime_copy) != pinned_hashes.get(runtime_names[0], ""):
                failures.append("runtime_config_copy_not_bound_to_sealed_artifact")
        status = FAIL if failures else NOT_VERIFIABLE if missing_artifacts else PASS
        self.add("runtime_identity", status, "Sealed identity and runtime match independent run anchors." if status == PASS else "A required sealed source is missing." if status == NOT_VERIFIABLE else "Sealed identity or trust chain differs from independent anchors.", observed=observed, failures=failures, missing_artifacts=missing_artifacts, sealed_manifest_sha256=observed_manifest_sha)

    def _audit_duration(self, result: dict[str, Any] | None, lifecycle: dict[str, Any] | None,
                        metrics: dict[str, Any] | None, runtime: dict[str, Any] | None,
                        systemd: dict[str, Any] | None) -> None:
        if not all((result, lifecycle, metrics, runtime, systemd)):
            self.add("duration", NOT_VERIFIABLE, "Result, lifecycle, metrics, sealed runtime, or systemd evidence is missing."); return
        assert result is not None and lifecycle is not None and metrics is not None and runtime is not None and systemd is not None
        interval = self._load_cohort_interval()
        start, end = interval["start"], interval["end"]
        if start is None or end is None:
            self.add("duration", NOT_VERIFIABLE, "Frozen cohort journals do not establish collector start/end bounds.", missing=interval["missing"], errors=interval["errors"]); return
        supervisor_start = parse_utc(result.get("started_at"))
        systemd_start, systemd_stop = parse_utc(systemd.get("start_time")), parse_utc(systemd.get("stop_time"))
        metrics_start = parse_utc(metrics.get("collector_started_at"))
        supervisor_end = parse_utc(result.get("ended_at") or result.get("actual_end_time_utc"))
        seconds = runtime.get("duration_seconds")
        elapsed = (end - start).total_seconds() if start and end else None
        failures: list[str] = []
        missing: list[str] = []
        if not isinstance(seconds, (int, float)) or seconds <= 0 or elapsed is None:
            self.add("duration", NOT_VERIFIABLE, "Collector start, end, or sealed duration is missing.", start=start.isoformat() if start else None, end=end.isoformat() if end else None); return
        if supervisor_start is None or systemd_start is None or systemd_stop is None or metrics_start is None or supervisor_end is None:
            missing.append("supervisor_or_systemd_bounds")
        elif supervisor_start > start or (start - supervisor_start).total_seconds() > 60 or systemd_start > start or systemd_stop < supervisor_end or supervisor_end < end:
            failures.append("collector_bounds_outside_supervisor_or_systemd_interval")
        if metrics_start is not None and abs((metrics_start - start).total_seconds()) > 5:
            failures.append("collector_metrics_start_disagrees_with_frozen_journal")
        if elapsed < seconds or result.get("full_duration_satisfied") is False:
            failures.append("duration_short")
        # Lifecycle timestamps, when present, are corroboration, never a replacement for collector start.
        life_end = parse_utc(lifecycle.get("collector_stopped_at"))
        if life_end and abs((life_end - end).total_seconds()) > 5:
            failures.append("lifecycle_end_disagrees")
        status = FAIL if failures else NOT_VERIFIABLE if missing else PASS
        if interval["errors"]: failures.extend(interval["errors"])
        if interval["missing"]: missing.extend(interval["missing"])
        status = FAIL if failures else NOT_VERIFIABLE if missing else PASS
        self.add("duration", status, "Journal-derived collector interval meets the sealed duration requirement." if status == PASS else "Duration evidence is incomplete." if status == NOT_VERIFIABLE else "Duration evidence is inconsistent or short.", start=start.isoformat(), end=end.isoformat(), elapsed_seconds=elapsed, required_seconds=seconds, failures=failures, missing=missing, evidence_classification="RECONSTRUCTED_OBSERVATION", source="frozen coverage journals; corroborated by collector metrics, supervisor result and exact systemd InvocationID")

    def _load_cohort_interval(self) -> dict[str, Any]:
        bundle = self.read_json("terminal/cohorts.json")
        if bundle is None:
            return {"start": None, "end": None, "journals": {}, "errors": [], "missing": ["cohort_summary_missing"]}
        errors: list[str] = []
        missing: list[str] = []
        if bundle.get("evidence_classification") != "RECONSTRUCTED_OBSERVATION":
            errors.append("cohort_interval_not_labeled_reconstructed")
        rows = bundle.get("cohorts")
        partial_rows = bundle.get("partial_cohorts")
        if not isinstance(rows, list) or not isinstance(partial_rows, list):
            return {"start": None, "end": None, "journals": {}, "errors": errors, "missing": ["full_or_partial_cohort_list_missing"]}
        all_rows = rows + partial_rows
        row_ids = [row.get("cohort_id") for row in all_rows if isinstance(row, dict)]
        if len(row_ids) != len(all_rows) or len(set(row_ids)) != len(row_ids):
            errors.append("malformed_or_duplicate_normalized_cohort")
        source_rows = bundle.get("source_journals")
        if not isinstance(source_rows, list):
            return {"start": None, "end": None, "journals": {}, "errors": errors, "missing": ["source_journal_inventory_missing"]}
        sources = {row.get("cohort_id"): row for row in source_rows if isinstance(row, dict)}
        if len(sources) != len(source_rows) or set(sources) != set(row_ids):
            errors.append("source_journal_inventory_differs_from_cohorts")
        journals: dict[str, dict[str, Any]] = {}
        for cohort_id, source in sources.items():
            rel, expected_sha = source.get("path"), source.get("sha256")
            if not isinstance(rel, str) or not _safe_relpath(rel) or not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha):
                errors.append(f"invalid_source_journal_reference:{cohort_id}"); continue
            path = self.root / rel
            if not path.is_file() or path.is_symlink():
                missing.append(f"source_journal_missing:{cohort_id}"); continue
            if sha256_file(path) != expected_sha:
                errors.append(f"source_journal_hash_mismatch:{cohort_id}"); continue
            try:
                journal = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                errors.append(f"source_journal_invalid_json:{cohort_id}"); continue
            if not isinstance(journal, dict) or journal.get("schema_version") != 1 or journal.get("cohort_utc") != cohort_id or not isinstance(journal.get("observations"), list):
                errors.append(f"source_journal_schema_mismatch:{cohort_id}"); continue
            observations = journal["observations"]
            starts = {row.get("observation_start_utc") for row in observations if isinstance(row, dict)}
            ends = {row.get("observation_end_utc") for row in observations if isinstance(row, dict)}
            qualifications = {row.get("cohort_qualification") for row in observations if isinstance(row, dict)}
            if not observations or len(starts) != 1 or len(ends) != 1 or len(qualifications) != 1:
                errors.append(f"source_journal_interval_inconsistent:{cohort_id}"); continue
            start_dt = parse_utc(next(iter(starts)))
            end_dt = parse_utc(next(iter(ends)))
            if start_dt is None or end_dt is None or end_dt < start_dt:
                errors.append(f"source_journal_timestamp_invalid:{cohort_id}"); continue
            journals[cohort_id] = {"payload": journal, "observations": observations,
                                   "start": next(iter(starts)), "end": next(iter(ends)),
                                   "qualification": next(iter(qualifications)),
                                   "path": rel, "sha256": expected_sha}
        ordered = sorted(journals)
        start = parse_utc(journals[ordered[0]]["start"]) if ordered else None
        end = parse_utc(journals[ordered[-1]]["end"]) if ordered else None
        declared_start, declared_end = parse_utc(bundle.get("actual_start_utc")), parse_utc(bundle.get("actual_end_utc"))
        if start is None or declared_start is None:
            missing.append("journal_actual_start_missing")
        elif abs((start - declared_start).total_seconds()) > 0.001:
            errors.append("declared_start_differs_from_journal")
        if end is None or declared_end is None:
            missing.append("journal_actual_end_missing")
        elif abs((end - declared_end).total_seconds()) > 0.001:
            errors.append("declared_end_differs_from_journal")
        return {"start": start, "end": end, "journals": journals, "errors": errors, "missing": missing}

    def _audit_exits(self, result: dict[str, Any] | None) -> None:
        if result is None:
            self.add("component_exit_codes", NOT_VERIFIABLE, "Supervisor result is missing."); return
        collector = _integer(result.get("collector_exit_code"))
        scheduler = _integer(result.get("archive_scheduler_exit_code", result.get("scheduler_exit_code")))
        publisher = _integer(result.get("publisher_exit_code"))
        if None in (collector, scheduler, publisher):
            self.add("component_exit_codes", NOT_VERIFIABLE, "One or more component exit codes are missing."); return
        if result.get("overall_status") is None or result.get("full_duration_satisfied") is None:
            self.add("component_exit_codes", NOT_VERIFIABLE, "Supervisor status or full-duration flag is missing."); return
        started = result.get("archive_scheduler_started") is True and result.get("publisher_started") is True
        clean_shutdown = result.get("overall_status") == "PASS" and result.get("received_signal") in (None, "") and result.get("forced_timeout") is not True and result.get("timed_out") is not True
        publisher_ok = publisher == 0 or (publisher == -15 and result.get("publisher_stopped_after_collector") is True and clean_shutdown)
        scheduler_ok = scheduler == 0 or (scheduler == -15 and result.get("scheduler_stopped_after_collector") is True and clean_shutdown)
        ok = started and collector == 0 and publisher_ok and scheduler_ok and result.get("overall_status") == "PASS" and result.get("full_duration_satisfied") is True
        self.add("component_exit_codes", PASS if ok else FAIL, "Component exits satisfy exact normal shutdown semantics." if ok else "Component exit is missing, abnormal, or outside the narrow post-collector SIGTERM allowance.", collector=collector, scheduler=scheduler, publisher=publisher, started=started, clean_shutdown=clean_shutdown, supervisor_status=result.get("overall_status"), full_duration_satisfied=result.get("full_duration_satisfied"))

    def _audit_systemd(self, terminal: dict[str, Any] | None) -> None:
        raw_path = self.payloads.get("terminal/systemd-invocation.jsonl", self.root / "terminal/systemd-invocation.jsonl")
        if terminal is None:
            self.add("systemd_terminal", NOT_VERIFIABLE, "Parsed exact InvocationID evidence is missing."); return
        try:
            rows = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except (OSError, json.JSONDecodeError):
            self.add("systemd_terminal", NOT_VERIFIABLE, "Raw journal export is missing or invalid."); return
        invocation = terminal.get("InvocationID")
        if not isinstance(invocation, str) or not invocation or not rows:
            self.add("systemd_terminal", NOT_VERIFIABLE, "InvocationID or journal rows are missing."); return
        row_ids = {row.get("_SYSTEMD_INVOCATION_ID", row.get("INVOCATION_ID")) for row in rows if isinstance(row, dict)}
        if row_ids != {invocation}:
            self.add("systemd_terminal", FAIL, "Journal records are not all scoped to the exact invocation.", invocation_id=invocation, observed_ids=sorted(str(x) for x in row_ids)); return
        expected_unit = f"bitcoin-trader-30h-{self.run_id}.service" if self.run_id else None
        units = {row.get("_SYSTEMD_UNIT", row.get("UNIT")) for row in rows if isinstance(row, dict)}
        if not expected_unit or units != {expected_unit}:
            self.add("systemd_terminal", FAIL, "Journal records are not bound to the exact run-specific collector unit.", expected_unit=expected_unit, observed_units=sorted(str(x) for x in units)); return
        required = ("Result", "ExecMainStatus", "ExecMainCode", "MainPID", "NRestarts", "start_time", "stop_time", "runtime_duration_seconds", "watchdog_result", "received_signal")
        missing = [key for key in required if key not in terminal]
        if missing:
            self.add("systemd_terminal", NOT_VERIFIABLE, "Required terminal systemd fields are missing.", missing=missing, invocation_id=invocation); return
        start, stop = parse_utc(terminal.get("start_time")), parse_utc(terminal.get("stop_time"))
        result = str(terminal.get("Result", "")).lower()
        success = result == "success" and terminal.get("ExecMainStatus") == 0 and terminal.get("ExecMainCode") in ("exited", 1) and terminal.get("NRestarts") == 0 and terminal.get("runtime_duration_seconds", 0) > 0 and start and stop and stop >= start and terminal.get("received_signal") in (None, "") and terminal.get("watchdog_result") in ("success", "not_triggered", None)
        self.add("systemd_terminal", PASS if success else FAIL, "Durable InvocationID journal evidence records a clean terminal service." if success else "Systemd terminal fields report failure or inconsistency.", invocation_id=invocation, result=result, n_restarts=terminal.get("NRestarts"), main_pid=terminal.get("MainPID"))

    def _audit_witness(self, identity: dict[str, Any] | None, witness: dict[str, Any] | None,
                       readback: dict[str, Any] | None) -> None:
        if identity is None or witness is None or readback is None:
            self.add("terminal_witness_s3", NOT_VERIFIABLE, "Identity, witness, or exact S3 readback metadata is missing."); return
        local = self.payloads.get("terminal/terminal-receipt.json", self.root / "terminal/terminal-receipt.json")
        remote = self.payloads.get("terminal/s3-readback/terminal-receipt.json", self.root / "terminal/s3-readback/terminal-receipt.json")
        outcome = readback.get("outcome")
        if outcome in ("AccessDenied", "403", "timeout", "network_error", "expired_credentials"):
            self.add("terminal_witness_s3", NOT_VERIFIABLE, "S3 readback was denied or unavailable.", outcome=outcome); return
        if outcome in ("NoSuchKey", "NotFound", "404") and readback.get("confirmed_authorized") is True:
            self.add("terminal_witness_s3", FAIL, "Authorized exact-key GET confirmed the terminal object is absent.", outcome=outcome); return
        if outcome != "success":
            self.add("terminal_witness_s3", NOT_VERIFIABLE, "Exact S3 GET outcome is missing or ambiguous.", outcome=outcome); return
        try:
            local_bytes, remote_bytes = local.read_bytes(), remote.read_bytes()
        except OSError:
            self.add("terminal_witness_s3", NOT_VERIFIABLE, "Local or remote terminal receipt bytes are missing."); return
        mismatch = []
        missing = []
        if local_bytes != remote_bytes: mismatch.append("bytes_differ")
        local_sha, remote_sha = sha256_bytes(local_bytes), sha256_bytes(remote_bytes)
        if not readback.get("sha256") or not readback.get("VersionId"): missing.append("readback_hash_or_version_id_missing")
        elif local_sha != readback.get("sha256") or remote_sha != readback.get("sha256"): mismatch.append("sha256_mismatch")
        if len(local_bytes) != readback.get("byte_length"): mismatch.append("byte_length_mismatch")
        if witness.get("run_id") != identity.get("run_id") or witness.get("epoch") != identity.get("epoch"): mismatch.append("witness_identity_mismatch")
        expected_key = f"{str(identity.get('s3_prefix', '')).rstrip('/')}/terminal/terminal-receipt.json"
        if witness.get("s3_key") != expected_key or readback.get("key") != expected_key or readback.get("bucket") != identity.get("s3_bucket"):
            mismatch.append("terminal_s3_target_mismatch")
        versions = readback.get("version_ids")
        if not isinstance(versions, list):
            missing.append("terminal_version_inventory_missing")
        elif len(versions) != 1 or versions[0] != readback.get("VersionId"):
            mismatch.append("duplicate_or_conflicting_terminal_versions")
        if readback.get("http_status") != 200 or not readback.get("request_id") or not readback.get("caller_arn") or not readback.get("captured_at_utc") or not readback.get("etag"):
            missing.append("get_object_request_provenance_missing")
        # Witness's pre-upload boolean is informational and never accepted as proof.
        status = FAIL if mismatch else NOT_VERIFIABLE if missing else PASS
        self.add("terminal_witness_s3", status, "Exact S3 GET bytes, SHA256, and VersionId match local terminal receipt." if status == PASS else "Terminal receipt evidence is incomplete." if status == NOT_VERIFIABLE else "Terminal receipt parity is contradicted.", outcome=outcome, local_sha256=local_sha, remote_sha256=remote_sha, version_id=readback.get("VersionId"), witness_s3_uploaded=witness.get("s3_uploaded"), failures=mismatch, missing=missing)

    def _audit_collector_state(self, lifecycle: dict[str, Any] | None, metrics: dict[str, Any] | None) -> None:
        if lifecycle is None or metrics is None:
            self.add("collector_lifecycle_writer", NOT_VERIFIABLE, "Lifecycle or final collector metrics are missing."); return
        phase = lifecycle.get("phase")
        q = metrics.get("queue_size")
        unpersisted = metrics.get("unpersisted_event_count")
        writer_errors = _nested_sum(metrics.get("exchanges"), "writer_errors")
        dropped_events = _nested_sum(metrics.get("exchanges"), "queue_dropped_events")
        exchange_rows = metrics.get("exchanges")
        loop_stalls = [row.get("max_event_loop_lag_seconds") for row in exchange_rows.values() if isinstance(row, dict) and isinstance(row.get("max_event_loop_lag_seconds"), (int, float))] if isinstance(exchange_rows, dict) else []
        max_loop_stall = max(loop_stalls) if loop_stalls else None
        if phase not in ("COMPLETE", "COMPLETED") or lifecycle.get("final_manifest_flush_observed") is not True:
            status = FAIL if phase in ("FAILED", "ERROR") else NOT_VERIFIABLE
        elif None in (q, unpersisted, writer_errors, dropped_events):
            status = NOT_VERIFIABLE
        else:
            status = PASS if q == unpersisted == writer_errors == dropped_events == 0 else FAIL
        self.add("collector_lifecycle_writer", status, "Collector completion and final persistence state are verified." if status == PASS else "Collector lifecycle or final persistence evidence is incomplete or failed.", phase=phase, queue_depth=q, unpersisted=unpersisted, writer_errors=writer_errors, dropped_events=dropped_events, max_event_loop_lag_seconds=max_loop_stall)

    def _audit_redundancy(self, runtime: dict[str, Any] | None, metrics: dict[str, Any] | None) -> None:
        if runtime is None or metrics is None:
            self.add("bithumb_redundancy", NOT_VERIFIABLE, "Runtime or metrics evidence is missing."); return
        configured = runtime.get("bithumb_redundancy")
        observed = metrics.get("bithumb_physical_connections")
        exchanges = metrics.get("exchanges")
        bh = exchanges.get("bithumb") if isinstance(exchanges, dict) else None
        if not isinstance(configured, dict) or not isinstance(observed, dict) or not isinstance(bh, dict):
            self.add("bithumb_redundancy", NOT_VERIFIABLE, "Nested redundancy schema or live metrics are missing."); return
        count = configured.get("physical_connections")
        active = observed.get("active_count", observed.get("active_connections"))
        if active is None:
            active = sum(1 for state in observed.values() if isinstance(state, str) and state.upper() == "CONNECTED")
        conflicts = _first_value(bh, "conflicting_duplicate_frames", "conflicting_duplicates", "conflict_count")
        duplicates = _first_value(bh, "deduplicated_frames", "trade_duplicates", "duplicate_count")
        if None in (count, active, conflicts, duplicates):
            status = NOT_VERIFIABLE
        else:
            status = PASS if configured.get("mode") == "ACTIVE_ACTIVE" and count >= 2 and active >= 2 and conflicts == 0 and duplicates >= 0 else FAIL
        self.add("bithumb_redundancy", status, "Nested runtime and measured active-active redundancy satisfy the contract." if status == PASS else "Bithumb redundancy evidence is incomplete or violates the contract.", configured_connections=count, active_connections=active, conflicts=conflicts, duplicates=duplicates)

    def _audit_observer(self, lifecycle: dict[str, Any] | None, run_id: str | None) -> None:
        expected = f"bitcoin-trader-30h-{run_id}.service" if run_id else None
        observed = lifecycle.get("observer_unit_name") if lifecycle else None
        status = PASS if expected and observed == expected else NOT_VERIFIABLE
        self.add("observer_unit_diagnostic", status, "Observer snapshot is bound to the expected duration-specific unit." if status == PASS else "Observer unit evidence is missing or points at another unit; diagnostic only.", expected=expected, observed=observed, informational=True)

    def _audit_cohorts(self, runtime: dict[str, Any] | None, result: dict[str, Any] | None,
                       metrics: dict[str, Any] | None) -> None:
        cohorts = self.read_json("terminal/cohorts.json")
        if runtime is None or result is None or cohorts is None:
            self.add("cohorts_receipts", NOT_VERIFIABLE, "Runtime, result, or cohort inventory is missing."); return
        interval = self._load_cohort_interval()
        start, end = interval["start"], interval["end"]
        expected = expected_feed_ids(runtime)
        if start is None or end is None or expected is None:
            self.add("cohorts_receipts", NOT_VERIFIABLE, "Frozen journal bounds or exact feed universe are unavailable.", missing=interval["missing"], errors=interval["errors"]); return
        hours, start_partial, end_partial = qualifying_hours(start, end)
        full_rows, partial_rows = cohorts.get("cohorts"), cohorts.get("partial_cohorts")
        if not isinstance(full_rows, list) or not isinstance(partial_rows, list):
            self.add("cohorts_receipts", NOT_VERIFIABLE, "Full and partial cohort lists are required."); return
        by_id = {row.get("cohort_id"): row for row in full_rows + partial_rows if isinstance(row, dict)}
        if len(by_id) != len(full_rows) + len(partial_rows):
            self.add("cohorts_receipts", FAIL, "Duplicate or malformed cohort IDs."); return
        errors: list[str] = []
        unavailable: list[str] = list(interval["missing"])
        errors.extend(interval["errors"])
        expected_ids = set(hours) | {x for x in (start_partial, end_partial) if x is not None}
        unavailable.extend(f"missing_cohort:{h}" for h in sorted(expected_ids - set(by_id)))
        extras = sorted(set(by_id) - expected_ids)
        if extras: errors.extend(f"unexpected_cohort:{h}" for h in extras)
        expected_set = set(expected)
        expected_slots = 0
        journals = interval["journals"]
        for cohort_id, row in by_id.items():
            journal = journals.get(cohort_id)
            expected_qualification = "QUALIFYING_FULL_HOUR" if cohort_id in set(hours) else "TOUCHED_PARTIAL"
            if row.get("cohort_qualification") != expected_qualification:
                errors.append(f"cohort_qualification_mismatch:{cohort_id}")
            if journal is None:
                unavailable.append(f"journal_missing:{cohort_id}"); continue
            if row.get("journal_path") != journal["path"] or row.get("journal_sha256") != journal["sha256"]:
                errors.append(f"normalized_journal_reference_mismatch:{cohort_id}")
            if journal["qualification"] != expected_qualification:
                errors.append(f"journal_qualification_mismatch:{cohort_id}")
            if row.get("observation_start_utc") != journal["start"] or row.get("observation_end_utc") != journal["end"]:
                errors.append(f"normalized_interval_differs_from_journal:{cohort_id}")
            feed_ids = []
            for observation in journal["observations"]:
                if not isinstance(observation, dict): continue
                feed = observation.get("feed")
                if isinstance(feed, dict) and all(isinstance(feed.get(k), str) for k in ("exchange", "stream", "market")):
                    feed_ids.append(f"{feed['exchange']}:{feed['stream']}:{feed['market']}")
            if len(feed_ids) != len(expected) or set(feed_ids) != expected_set or len(set(feed_ids)) != len(feed_ids):
                errors.append(f"journal_feed_universe_mismatch:{cohort_id}")
            if journal["payload"].get("slot_count") != len(journal["observations"]):
                errors.append(f"journal_slot_count_mismatch:{cohort_id}")
        for hour in hours:
            row = by_id.get(hour)
            if not row: continue
            slots = row.get("feed_slots")
            feed_ids = [x.get("feed_id") for x in slots if isinstance(x, dict)] if isinstance(slots, list) else []
            missing_feeds = expected_set - set(feed_ids)
            if missing_feeds: unavailable.append(f"missing_feed_slots:{hour}:{len(missing_feeds)}")
            if set(feed_ids) - expected_set or len(set(feed_ids)) != len(feed_ids):
                errors.append(f"feed_universe_mismatch:{hour}")
            for slot in (slots if isinstance(slots, list) else []):
                if not isinstance(slot, dict) or not slot.get("local_receipt_path") or not slot.get("local_receipt_sha256"):
                    unavailable.append(f"slot_receipt_missing:{hour}"); continue
                slot_outcome = slot.get("s3_get_outcome")
                if slot_outcome in ("AccessDenied", "403", "timeout", "network_error", "expired_credentials"):
                    self.add("cohorts_receipts", NOT_VERIFIABLE, "A required slot S3 readback was denied or unavailable.", cohort=hour, feed_id=slot.get("feed_id"), outcome=slot_outcome); return
                if slot_outcome in ("NoSuchKey", "NotFound", "404") and slot.get("confirmed_authorized") is True:
                    errors.append(f"slot_object_missing:{hour}:{slot.get('feed_id')}")
                elif slot_outcome != "success" or not slot.get("version_id") or not slot.get("s3_receipt_sha256"):
                    unavailable.append(f"slot_receipt_parity_missing:{hour}:{slot.get('feed_id')}")
                elif slot.get("local_receipt_sha256") != slot.get("s3_receipt_sha256"):
                    errors.append(f"slot_receipt_hash_mismatch:{hour}:{slot.get('feed_id')}")
                else:
                    local_check = self._receipt_path_check(slot.get("local_receipt_path"), slot.get("local_receipt_sha256"))
                    remote_check = self._receipt_path_check(slot.get("s3_readback_path"), slot.get("s3_receipt_sha256"))
                    if "mismatch" in (local_check, remote_check): errors.append(f"slot_receipt_bytes_hash_mismatch:{hour}:{slot.get('feed_id')}")
                    elif "missing" in (local_check, remote_check): unavailable.append(f"slot_receipt_bytes_unavailable:{hour}:{slot.get('feed_id')}")
            expected_slots += len(expected)
            if not row.get("local_receipt_path") or not row.get("local_receipt_sha256"):
                unavailable.append(f"local_receipt_missing:{hour}")
            if row.get("s3_get_outcome") in ("AccessDenied", "403", "timeout", "network_error", "expired_credentials"):
                self.add("cohorts_receipts", NOT_VERIFIABLE, "A required cohort S3 readback was denied or unavailable.", cohort=hour, outcome=row.get("s3_get_outcome")); return
            if row.get("s3_get_outcome") in ("NoSuchKey", "NotFound", "404") and row.get("confirmed_authorized") is True:
                errors.append(f"cohort_object_missing:{hour}")
            elif row.get("s3_get_outcome") != "success" or not row.get("version_id") or not row.get("s3_receipt_sha256"):
                unavailable.append(f"cohort_receipt_parity_missing:{hour}")
            elif row.get("local_receipt_sha256") != row.get("s3_receipt_sha256"):
                errors.append(f"cohort_receipt_hash_mismatch:{hour}")
            else:
                local_check = self._receipt_path_check(row.get("local_receipt_path"), row.get("local_receipt_sha256"))
                remote_check = self._receipt_path_check(row.get("s3_readback_path"), row.get("s3_receipt_sha256"))
                if "mismatch" in (local_check, remote_check): errors.append(f"cohort_receipt_bytes_hash_mismatch:{hour}")
                elif "missing" in (local_check, remote_check): unavailable.append(f"cohort_receipt_bytes_unavailable:{hour}")
        status = FAIL if errors else NOT_VERIFIABLE if unavailable else PASS
        self.add("cohorts_receipts", status, "Exact journal-derived qualifying cohorts, feed universe, and receipt parity are verified." if status == PASS else "Required cohort/feed/receipt evidence is incomplete." if status == NOT_VERIFIABLE else "Qualifying cohort/feed/receipt contract has mismatches.", expected_hours=hours, start_partial=start_partial, end_partial=end_partial, expected_feed_slots=expected_slots, observed_hours=sorted(set(hours) & set(by_id)), failures=errors, missing=unavailable, evidence_classification="RECONSTRUCTED_OBSERVATION")

    def _audit_receipt_scope(self) -> None:
        inventory = self.read_json("terminal/receipt-inventory.json")
        if inventory is None or not isinstance(inventory.get("receipts"), list):
            self.add("receipt_scope", NOT_VERIFIABLE, "Typed receipt inventory is missing or malformed."); return
        durability = {
            "COHORT_RECEIPT": "BOTH_REQUIRED", "SLOT_RECEIPT": "BOTH_REQUIRED",
            "FILE_RECEIPT": "BOTH_REQUIRED", "SKIPPED_PARTIAL_HOUR_RECEIPT": "LOCAL_ONLY",
            "TERMINAL_RECEIPT": "BOTH_REQUIRED", "TIMESTAMPED_TERMINAL_RECEIPT": "OPTIONAL",
            "OTHER": "OPTIONAL",
        }
        errors: list[str] = []
        unavailable: list[str] = []
        unknown: list[str] = []
        declared_remote_keys: set[str] = set()
        remote_key_occurrences: list[str] = []
        required_remote_keys: set[str] = set()
        required_type_counts = {"COHORT_RECEIPT": 0, "SLOT_RECEIPT": 0, "TERMINAL_RECEIPT": 0}
        observed_slot_ids: set[tuple[Any, Any]] = set()
        for row in inventory["receipts"]:
            if not isinstance(row, dict): errors.append("malformed_receipt_entry"); continue
            kind = row.get("type")
            if kind not in durability: unknown.append(str(kind)); continue
            if kind in required_type_counts:
                required_type_counts[kind] += 1
            if kind == "SLOT_RECEIPT":
                slot_identity = (row.get("cohort_id"), row.get("feed_id"))
                if slot_identity in observed_slot_ids or None in slot_identity:
                    errors.append("duplicate_or_unbound_slot_receipt")
                observed_slot_ids.add(slot_identity)
            if row.get("durability") != durability[kind]: errors.append(f"wrong_durability:{kind}")
            if kind in ("COHORT_RECEIPT", "SLOT_RECEIPT", "FILE_RECEIPT", "TERMINAL_RECEIPT"):
                if not row.get("local_path") or not row.get("local_sha256"): unavailable.append(f"local_missing:{kind}")
                s3_key = row.get("s3_key")
                if isinstance(s3_key, str) and s3_key:
                    required_remote_keys.add(s3_key)
                    declared_remote_keys.add(s3_key)
                    remote_key_occurrences.append(s3_key)
                else:
                    unavailable.append(f"s3_key_missing:{kind}")
                outcome = row.get("s3_get_outcome")
                if outcome in ("AccessDenied", "403", "timeout", "network_error", "expired_credentials"):
                    self.add("receipt_scope", NOT_VERIFIABLE, "Required receipt S3 scope is unavailable.", receipt_type=kind, outcome=outcome); return
                if outcome in ("NoSuchKey", "NotFound", "404") and row.get("confirmed_authorized") is True:
                    errors.append(f"required_remote_missing:{kind}")
                elif outcome != "success" or not row.get("version_id") or not row.get("s3_sha256"):
                    unavailable.append(f"remote_parity_unavailable:{kind}")
                elif row.get("local_sha256") != row.get("s3_sha256"):
                    errors.append(f"remote_hash_mismatch:{kind}")
                local_check = self._receipt_path_check(row.get("local_path"), row.get("local_sha256"))
                remote_check = self._receipt_path_check(row.get("s3_readback_path"), row.get("s3_sha256"))
                if local_check == "missing": unavailable.append(f"local_bytes_missing:{kind}")
                elif local_check == "mismatch": errors.append(f"local_bytes_hash_mismatch:{kind}")
                if remote_check == "missing": unavailable.append(f"remote_bytes_missing:{kind}")
                elif remote_check == "mismatch": errors.append(f"remote_bytes_hash_mismatch:{kind}")
            if kind == "SKIPPED_PARTIAL_HOUR_RECEIPT":
                if row.get("s3_get_outcome") not in (None, "not_required"):
                    errors.append("skipped_partial_receipt_must_be_local_only")
                local_check = self._receipt_path_check(row.get("local_path"), row.get("local_sha256"))
                if local_check == "missing": unavailable.append("skipped_partial_local_receipt_missing")
                elif local_check == "mismatch": errors.append("skipped_partial_local_receipt_hash_mismatch")
            if kind in ("TIMESTAMPED_TERMINAL_RECEIPT", "OTHER") and isinstance(row.get("s3_key"), str):
                declared_remote_keys.add(row["s3_key"])
        extras = inventory.get("unexpected_s3_objects", [])
        if extras: errors.append("unexpected_s3_receipts_present")
        for kind, count in required_type_counts.items():
            if count == 0: unavailable.append(f"required_receipt_type_missing:{kind}")
        if len(remote_key_occurrences) != len(set(remote_key_occurrences)):
            errors.append("multiple_receipts_bound_to_same_s3_key")
        runtime = self.read_json("sealed/runtime.json")
        identity = self.read_json("sealed/identity.json")
        listing = inventory.get("s3_prefix_listing")
        if not isinstance(listing, dict):
            unavailable.append("complete_s3_prefix_listing_missing")
        elif listing.get("outcome") in ("AccessDenied", "403", "timeout", "network_error", "expired_credentials"):
            self.add("receipt_scope", NOT_VERIFIABLE, "Read-only S3 prefix inventory was denied or incomplete.", outcome=listing.get("outcome")); return
        elif (listing.get("outcome") != "success" or listing.get("complete") is not True or listing.get("next_token") not in (None, "") or
              not listing.get("request_id") or not listing.get("caller_arn") or not listing.get("captured_at_utc")):
            unavailable.append("complete_s3_prefix_listing_provenance_missing")
        else:
            expected_bucket = identity.get("s3_bucket") if identity else None
            expected_prefix = str(identity.get("s3_prefix", "")).rstrip("/") if identity else ""
            observed_prefix = listing.get("prefix")
            if listing.get("bucket") != expected_bucket or not isinstance(observed_prefix, str) or observed_prefix.rstrip("/") != expected_prefix:
                errors.append("s3_prefix_listing_target_mismatch")
            objects = listing.get("objects")
            if not isinstance(objects, list):
                unavailable.append("s3_prefix_listing_objects_missing")
            else:
                listed_keys = [obj.get("key") for obj in objects if isinstance(obj, dict)]
                if len(listed_keys) != len(objects) or any(not isinstance(key, str) for key in listed_keys):
                    errors.append("malformed_or_duplicate_s3_listing_keys")
                else:
                    if len(set(listed_keys)) != len(listed_keys): errors.append("malformed_or_duplicate_s3_listing_keys")
                    missing_remote = required_remote_keys - set(listed_keys)
                    unexpected_remote = set(listed_keys) - declared_remote_keys
                    if missing_remote: errors.extend(f"confirmed_required_s3_key_missing:{key}" for key in sorted(missing_remote))
                    optional_terminal_pattern = f"{expected_prefix}/terminal/terminal-receipt-"
                    unexpected_remote = {key for key in unexpected_remote if not (key.startswith(optional_terminal_pattern) and key.endswith(".json"))}
                    if unexpected_remote: errors.extend(f"unexpected_s3_object:{key}" for key in sorted(unexpected_remote))
        interval = self._load_cohort_interval()
        unavailable.extend(interval["missing"])
        errors.extend(interval["errors"])
        if runtime:
            start, end = interval["start"], interval["end"]
            expected_feeds = expected_feed_ids(runtime)
            if start and end and expected_feeds is not None:
                expected_hours, _, _ = qualifying_hours(start, end)
                expected_slot_ids = {(hour, feed) for hour in expected_hours for feed in expected_feeds}
                cohort_rows = [r for r in inventory["receipts"] if isinstance(r, dict) and r.get("type") == "COHORT_RECEIPT"]
                terminal_rows = [r for r in inventory["receipts"] if isinstance(r, dict) and r.get("type") == "TERMINAL_RECEIPT"]
                if len(cohort_rows) < len(expected_hours): unavailable.append("qualifying_cohort_receipt_count_short")
                if len(cohort_rows) > len(expected_hours): errors.append("unexpected_cohort_receipt_count")
                if len(terminal_rows) > 1: errors.append("duplicate_terminal_receipt_entries")
                if len(terminal_rows) != 1: unavailable.append("terminal_receipt_entry_count_not_one")
                missing_slots = expected_slot_ids - observed_slot_ids
                extra_slots = observed_slot_ids - expected_slot_ids
                if missing_slots: unavailable.append(f"slot_receipts_missing:{len(missing_slots)}")
                if extra_slots: errors.append(f"unexpected_slot_receipts:{len(extra_slots)}")
        if unknown: errors.extend(f"unknown_receipt_type:{x}" for x in unknown)
        status = FAIL if errors else NOT_VERIFIABLE if unavailable else PASS
        self.add("receipt_scope", status, "Receipt durability scopes match the frozen contract." if status == PASS else "Required receipt evidence is incomplete." if status == NOT_VERIFIABLE else "Receipt types, durability, or parity differ from the frozen contract.", failures=errors, missing=unavailable, receipt_count=len(inventory["receipts"]))

    def _receipt_path_matches(self, rel: Any, expected_sha: Any) -> bool:
        if not isinstance(rel, str) or not _safe_relpath(rel) or not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha):
            return False
        path = self.root / rel
        try:
            return path.is_file() and not path.is_symlink() and sha256_file(path) == expected_sha
        except OSError:
            return False

    def _receipt_path_check(self, rel: Any, expected_sha: Any) -> str:
        if not isinstance(rel, str) or not _safe_relpath(rel) or not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha):
            return "missing"
        path = self.root / rel
        try:
            if path.is_symlink() or not path.is_file(): return "missing"
            return "match" if sha256_file(path) == expected_sha else "mismatch"
        except OSError:
            return "missing"

    def _audit_receipt_immutability(self) -> None:
        inventory = self.read_json("terminal/receipt-inventory.json")
        if inventory is None or not isinstance(inventory.get("observations"), list):
            self.add("receipt_immutability", NOT_VERIFIABLE, "Timestamped receipt hash observations are missing."); return
        groups: dict[str, list[dict[str, Any]]] = {}
        for obs in inventory["observations"]:
            if isinstance(obs, dict) and isinstance(obs.get("receipt_id"), str):
                groups.setdefault(obs["receipt_id"], []).append(obs)
        required_ids = inventory.get("required_immutable_receipt_ids")
        if not isinstance(required_ids, list) or not required_ids:
            self.add("receipt_immutability", NOT_VERIFIABLE, "No explicit set of receipts has multiple immutability observations."); return
        failures, missing = [], []
        receipts = inventory.get("receipts")
        if not isinstance(receipts, list):
            self.add("receipt_immutability", NOT_VERIFIABLE, "Typed receipt inventory is missing."); return
        cohort_ids: set[str] = set()
        for receipt in receipts:
            if not isinstance(receipt, dict) or receipt.get("type") != "COHORT_RECEIPT":
                continue
            receipt_id = receipt.get("receipt_id")
            if not isinstance(receipt_id, str) or not receipt_id:
                missing.append("cohort_receipt_id_missing")
            else:
                cohort_ids.add(receipt_id)
        declared_ids = set(required_ids)
        if len(declared_ids) != len(required_ids):
            failures.append("duplicate_required_immutable_receipt_id")
        missing.extend(sorted(cohort_ids - declared_ids))
        undeclared_receipts = sorted(declared_ids - cohort_ids)
        if undeclared_receipts:
            missing.extend(undeclared_receipts)
        for receipt_id in required_ids:
            if not isinstance(receipt_id, str) or not receipt_id:
                failures.append("invalid_required_immutable_receipt_id")
                continue
            rows = groups.get(receipt_id, [])
            if len(rows) < 2 or len({r.get("captured_at_utc") for r in rows}) < 2:
                missing.append(receipt_id); continue
            hashes = {r.get("sha256") for r in rows}
            versions = {r.get("version_id") for r in rows}
            if len(hashes) != 1 or len(versions) != 1 or None in hashes or None in versions:
                failures.append(receipt_id)
        status = FAIL if failures else NOT_VERIFIABLE if missing else PASS
        self.add("receipt_immutability", status,
                 "Repeated read-only observations cover every qualifying cohort receipt and remained byte/version stable." if status == PASS else "Receipt stability failed or has insufficient observations.",
                 changed=failures, insufficient=missing, qualifying_cohort_receipts=sorted(cohort_ids))

    def _audit_finalization(self) -> None:
        trace = self.read_json("terminal/finalization-trace.json")
        if trace is None:
            self.add("finalization_trace", NOT_VERIFIABLE, "Finalization trace is missing."); return
        if trace.get("evidence_classification") != "NATIVE_INSTRUMENTATION_PRESENT":
            self.add("finalization_trace", NOT_VERIFIABLE, "Reconstructed observations cannot satisfy native finalization trace requirements.", evidence_classification=trace.get("evidence_classification")); return
        fields = ("scheduler_retries", "finalizer_retries", "recovery_invocations", "duplicate_finalization", "closed_at_utc_stable", "evidence_hash_stable", "restart_idempotency_path_exposed")
        missing = [field for field in fields if field not in trace]
        if missing:
            self.add("finalization_trace", NOT_VERIFIABLE, "Native trace omits required distinctions.", missing=missing); return
        ok = all(trace[field] == 0 for field in fields[:4]) and all(trace[field] is True for field in fields[4:])
        self.add("finalization_trace", PASS if ok else FAIL, "Native finalization trace verifies all frozen checks." if ok else "Native finalization trace records a contract violation.", values={key: trace[key] for key in fields})

    def report(self) -> dict[str, Any]:
        return self.audit()


def _safe_relpath(value: str) -> bool:
    path = PurePosixPath(value)
    return ("\\" not in value and "\x00" not in value and not path.is_absolute()
            and path.as_posix() == value
            and all(part not in ("", ".", "..") for part in path.parts))


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _first_value(mapping: Mapping[str, Any], *keys: str) -> Any:
    return next((mapping[k] for k in keys if k in mapping), None)


def _nested_sum(value: Any, field: str) -> int | None:
    if not isinstance(value, dict): return None
    vals = [row.get(field) for row in value.values() if isinstance(row, dict)]
    if not vals or any(not isinstance(v, int) for v in vals): return None
    return sum(vals)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle", required=True, type=Path)
    p.add_argument("--run-id", required=True)
    p.add_argument("--epoch", required=True)
    p.add_argument("--runtime-commit", required=True)
    p.add_argument("--runtime-tree", required=True)
    p.add_argument("--sealed-manifest-sha256", required=True)
    p.add_argument("--capture-manifest-sha256", required=True,
                   help="SHA256 captured independently before export, not copied from the bundle")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    auditor = AuditorV2(args.bundle, run_id=args.run_id, epoch=args.epoch,
        runtime_commit=args.runtime_commit, runtime_tree=args.runtime_tree,
        sealed_manifest_sha256=args.sealed_manifest_sha256,
        capture_manifest_sha256=args.capture_manifest_sha256)
    report = auditor.audit()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["overall_status"] == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
