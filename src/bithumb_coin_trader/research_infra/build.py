"""Offline, content-bound canonical dataset builder.

Builds only from an explicitly selected local raw-data copy. It never fetches
remote data and marks the resulting manifest as not yet DQ-qualified.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from .adapters import iter_raw_jsonl_streaming
from .registry import DatasetRegistration


class CanonicalBuildError(ValueError):
    """Raised when the local source or output cannot be safely built."""


def build_canonical_dataset(
    registration: DatasetRegistration,
    *,
    data_root: Path,
    output_dir: Path,
    git_commit: str,
) -> dict[str, Any]:
    """Persist a canonical JSONL dataset and source-bound manifest atomically.

    Existing output directories are never overwritten. Holdout-role datasets
    are rejected before reading any source bytes. DQ remains explicitly pending.
    """
    dataset_id = registration.dataset_id
    if not re.fullmatch(r"[A-Za-z0-9._-]+", dataset_id):
        raise CanonicalBuildError("dataset_id must be a safe path component")
    if registration.allowed_for_final_holdout or registration.dataset_role.value == "FROZEN_HOLDOUT":
        raise CanonicalBuildError("canonical build refuses prospective/frozen holdout datasets")

    root = Path(data_root).resolve()
    destination = Path(output_dir).resolve()
    if not root.is_dir():
        raise CanonicalBuildError(f"local raw data root does not exist: {root}")
    if destination == root or root in destination.parents:
        raise CanonicalBuildError("canonical output must be outside the raw data root")
    if destination.exists():
        raise CanonicalBuildError(f"output already exists; refusing to overwrite: {destination}")

    source_files = _source_files(root, registration.exchange_universe, registration.feed_universe)
    if not source_files:
        raise CanonicalBuildError("no supported local JSONL source files found")
    source_hashes = {path: _hash_file(path) for path in source_files}

    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix=f".{dataset_id}-build-", dir=destination.parent) as scratch:
            staging = Path(scratch)
            events_path = staging / "events.jsonl"
            event_hasher = hashlib.sha256()
            event_count = 0
            with events_path.open("w", encoding="utf-8", newline="\n") as stream:
                for event in iter_raw_jsonl_streaming(
                    root,
                    dataset_id,
                    exchanges=registration.exchange_universe,
                    feeds=registration.feed_universe,
                    source_run_id=registration.source_run_id,
                    collector_epoch=registration.collector_epoch,
                ):
                    payload = event.to_dict()
                    if payload.get("source_file"):
                        payload["source_file"] = Path(payload["source_file"]).resolve().relative_to(root).as_posix()
                    line = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
                    stream.write(line + "\n")
                    event_hasher.update((line + "\n").encode("utf-8"))
                    event_count += 1
                stream.flush()
                os.fsync(stream.fileno())

            if event_count == 0:
                raise CanonicalBuildError("no raw records adapted to canonical events")
            for path, expected_hash in source_hashes.items():
                if _hash_file(path) != expected_hash:
                    raise CanonicalBuildError(f"source changed while building: {path.relative_to(root)}")

            source_entries = [
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": source_hashes[path],
                    "size_bytes": path.stat().st_size,
                }
                for path in source_files
            ]
            source_set_hash = _hash_text(_canonical_json(source_entries))
            manifest = {
                "schema_version": "canonical-build-v1",
                "dataset_id": dataset_id,
                "dataset_role": registration.dataset_role.value,
                "source_run_id": registration.source_run_id,
                "collector_epoch": registration.collector_epoch,
                "source_root_label": root.name,
                "source_files": source_entries,
                "source_set_sha256": source_set_hash,
                "git_commit": git_commit,
                "event_schema_version": "1.0.0",
                "event_count": event_count,
                "events_file": "events.jsonl",
                "events_sha256": event_hasher.hexdigest(),
                "built_at_utc": datetime.now(timezone.utc).isoformat(),
                "build_status": "BUILT_DQ_NOT_RUN",
                "dq_status": "NOT_RUN",
                "candidate_selection_allowed": registration.allowed_for_candidate_selection,
                "holdout_evaluated": False,
            }
            manifest_path = staging / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            with manifest_path.open("rb") as stream:
                os.fsync(stream.fileno())

            if destination.exists():
                raise CanonicalBuildError(f"output appeared during build; refusing to overwrite: {destination}")
            Path(scratch).rename(destination)
    except FileExistsError as exc:
        raise CanonicalBuildError(f"output already exists; refusing to overwrite: {destination}") from exc

    return manifest


def _source_files(root: Path, exchanges: tuple[str, ...], feeds: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for date_dir in sorted(root.iterdir()):
        if not date_dir.is_dir():
            continue
        for exchange_dir in sorted(date_dir.iterdir()):
            if not exchange_dir.is_dir() or (exchanges and exchange_dir.name not in exchanges):
                continue
            for feed_dir in sorted(exchange_dir.iterdir()):
                if not feed_dir.is_dir() or (feeds and feed_dir.name not in feeds):
                    continue
                files.extend(
                    path for path in sorted(feed_dir.iterdir())
                    if path.is_file() and (path.name.endswith(".jsonl") or path.name.endswith(".jsonl.zst"))
                )
    return files


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
