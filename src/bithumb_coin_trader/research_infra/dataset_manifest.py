"""Immutable provenance contract for research dataset builds."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CODE_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_DQ_RESULTS = {"PASS", "FAIL", "PARTIAL", "UNKNOWN"}


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_utc(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class DatasetBuildManifest:
    """Binds a built dataset to source runs, bytes, code, scope, and DQ."""

    dataset_id: str
    source_run_ids: tuple[str, ...]
    source_hashes: dict[str, str]
    build_code_sha: str
    schema_version: str
    start_utc: str
    end_utc: str
    venues: tuple[str, ...]
    symbols: tuple[str, ...]
    record_counts: dict[str, int]
    dq_result: str
    created_at_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    manifest_schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if not _SAFE_ID_RE.fullmatch(self.dataset_id) or ".." in self.dataset_id:
            raise ValueError("dataset_id must be a safe, non-empty identifier")
        if not self.source_run_ids or len(set(self.source_run_ids)) != len(self.source_run_ids):
            raise ValueError("source_run_ids must be non-empty and unique")
        if any(not _SAFE_ID_RE.fullmatch(run_id) or ".." in run_id for run_id in self.source_run_ids):
            raise ValueError("source_run_ids must contain safe identifiers")
        if not self.source_hashes or any(not key or not _SHA256_RE.fullmatch(value) for key, value in self.source_hashes.items()):
            raise ValueError("source_hashes must map source identifiers to SHA-256 digests")
        if not _CODE_SHA_RE.fullmatch(self.build_code_sha):
            raise ValueError("build_code_sha must be a 40- or 64-character lowercase hex SHA")
        if not self.schema_version or not self.manifest_schema_version:
            raise ValueError("schema versions must be non-empty")
        if _parse_utc(self.start_utc, "start_utc") > _parse_utc(self.end_utc, "end_utc"):
            raise ValueError("start_utc must be at or before end_utc")
        if not self.venues or len(set(self.venues)) != len(self.venues):
            raise ValueError("venues must be non-empty and unique")
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("symbols must be non-empty and unique")
        if not self.record_counts or any(
            not name or not isinstance(count, int) or isinstance(count, bool) or count < 0
            for name, count in self.record_counts.items()
        ):
            raise ValueError("record_counts must contain non-negative integer counts")
        if self.dq_result not in _DQ_RESULTS:
            raise ValueError(f"dq_result must be one of {sorted(_DQ_RESULTS)}")
        _parse_utc(self.created_at_utc, "created_at_utc")

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "source_run_ids": list(self.source_run_ids),
            "source_hashes": dict(self.source_hashes),
            "build_code_sha": self.build_code_sha,
            "schema_version": self.schema_version,
            "start_utc": self.start_utc,
            "end_utc": self.end_utc,
            "venues": list(self.venues),
            "symbols": list(self.symbols),
            "record_counts": dict(self.record_counts),
            "dq_result": self.dq_result,
            "created_at_utc": self.created_at_utc,
            "manifest_schema_version": self.manifest_schema_version,
        }

    def compute_dataset_fingerprint(self) -> str:
        """Stable content ID for identical build inputs and DQ evidence."""
        identity = self.to_dict()
        identity.pop("created_at_utc")
        return _canonical_sha256(identity)

    def compute_manifest_fingerprint(self) -> str:
        """Fingerprint the complete serialized manifest, including build time."""
        return _canonical_sha256(self.to_dict())

    def save(self, path: Path, *, overwrite: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict()
        payload["dataset_fingerprint"] = self.compute_dataset_fingerprint()
        payload["manifest_fingerprint"] = self.compute_manifest_fingerprint()
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            if overwrite:
                os.replace(tmp_path, path)
            else:
                os.link(tmp_path, path)
                tmp_path.unlink()
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except Exception:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DatasetBuildManifest:
        return cls(
            dataset_id=data["dataset_id"],
            source_run_ids=tuple(data["source_run_ids"]),
            source_hashes=dict(data["source_hashes"]),
            build_code_sha=data["build_code_sha"],
            schema_version=data["schema_version"],
            start_utc=data["start_utc"],
            end_utc=data["end_utc"],
            venues=tuple(data["venues"]),
            symbols=tuple(data["symbols"]),
            record_counts=dict(data["record_counts"]),
            dq_result=data["dq_result"],
            created_at_utc=data["created_at_utc"],
            manifest_schema_version=data.get("manifest_schema_version", "1.0.0"),
        )

    @classmethod
    def load(cls, path: Path) -> DatasetBuildManifest:
        data = json.loads(path.read_text(encoding="utf-8"))
        expected_dataset_fingerprint = data.pop("dataset_fingerprint", None)
        expected_manifest_fingerprint = data.pop("manifest_fingerprint", None)
        manifest = cls.from_dict(data)
        if expected_dataset_fingerprint != manifest.compute_dataset_fingerprint():
            raise ValueError(f"Dataset manifest fingerprint mismatch: {path}")
        if expected_manifest_fingerprint != manifest.compute_manifest_fingerprint():
            raise ValueError(f"Dataset manifest manifest fingerprint mismatch: {path}")
        return manifest
