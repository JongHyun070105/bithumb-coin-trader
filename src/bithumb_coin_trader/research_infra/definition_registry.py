"""Append-only, versioned feature and strategy definition registry.

Definitions are immutable once a (kind, id, version) key is recorded. An
implementation or schema change therefore requires a new version, and
experiment manifests bind both the definition and concrete config hashes.
"""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Literal, Mapping


DefinitionKind = Literal["feature", "strategy"]
_IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


class DefinitionRegistryError(ValueError):
    """Raised for invalid, conflicting, or corrupted definition records."""


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    definition_id: str
    version: str
    implementation_sha256: str
    config_schema: Mapping[str, Any]
    description: str = ""
    kind: DefinitionKind = "feature"


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    definition_id: str
    version: str
    implementation_sha256: str
    config_schema: Mapping[str, Any]
    description: str = ""
    kind: DefinitionKind = "strategy"


VersionedDefinition = FeatureDefinition | StrategyDefinition


class VersionedDefinitionRegistry:
    """Durable JSONL registry whose key and hashes cannot be overwritten."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def register(self, definition: VersionedDefinition) -> dict[str, Any]:
        record = _definition_record(definition)
        key = (str(record["kind"]), str(record["definition_id"]), str(record["version"]))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise DefinitionRegistryError("definition registry must not be a symlink")
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.lseek(fd, 0, os.SEEK_SET)
            chunks: list[bytes] = []
            while chunk := os.read(fd, 1 << 20):
                chunks.append(chunk)
            existing_records = _parse_registry(b"".join(chunks), self.path)
            for existing in existing_records:
                existing_key = (
                    str(existing["kind"]),
                    str(existing["definition_id"]),
                    str(existing["version"]),
                )
                if existing_key == key:
                    if existing["definition_sha256"] != record["definition_sha256"]:
                        raise DefinitionRegistryError(
                            f"{key[0]} definition {key[1]} version {key[2]} is immutable; bump the version"
                        )
                    return existing
            encoded = (_canonical(record) + "\n").encode("utf-8")
            os.lseek(fd, 0, os.SEEK_END)
            offset = 0
            while offset < len(encoded):
                offset += os.write(fd, encoded[offset:])
            os.fsync(fd)
            return record
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def resolve(self, kind: DefinitionKind, definition_id: str, version: str) -> dict[str, Any]:
        if self.path.is_symlink() or not self.path.is_file():
            raise DefinitionRegistryError("definition registry is missing or is a symlink")
        records = _parse_registry(self.path.read_bytes(), self.path)
        matches = [
            item for item in records
            if item["kind"] == kind and item["definition_id"] == definition_id and item["version"] == version
        ]
        if len(matches) != 1:
            raise DefinitionRegistryError(
                f"expected one registered definition for {kind}:{definition_id}@{version}; found {len(matches)}"
            )
        return matches[0]

    def bind(
        self,
        kind: DefinitionKind,
        definition_id: str,
        version: str,
        config: Mapping[str, Any],
    ) -> dict[str, Any]:
        record = self.resolve(kind, definition_id, version)
        config_payload = _canonical(dict(config))
        return {
            "kind": kind,
            "definition_id": definition_id,
            "version": version,
            "definition_sha256": record["definition_sha256"],
            "implementation_sha256": record["implementation_sha256"],
            "config_sha256": hashlib.sha256(config_payload.encode("utf-8")).hexdigest(),
        }


def _definition_record(definition: VersionedDefinition) -> dict[str, Any]:
    kind = definition.kind
    if kind not in {"feature", "strategy"}:
        raise DefinitionRegistryError("definition kind must be feature or strategy")
    if not _IDENTIFIER.fullmatch(definition.definition_id):
        raise DefinitionRegistryError("definition_id must be a stable non-empty identifier")
    if not definition.version.strip() or len(definition.version) > 64:
        raise DefinitionRegistryError("definition version must be non-empty and at most 64 characters")
    if not _is_sha256(definition.implementation_sha256):
        raise DefinitionRegistryError("implementation_sha256 must be lowercase SHA-256 hex")
    if not isinstance(definition.config_schema, Mapping):
        raise DefinitionRegistryError("config_schema must be a JSON object")
    unsigned = {
        "schema_version": 1,
        "kind": kind,
        "definition_id": definition.definition_id,
        "version": definition.version,
        "implementation_sha256": definition.implementation_sha256,
        "config_schema": json.loads(_canonical(dict(definition.config_schema))),
        "description": definition.description,
    }
    unsigned["definition_sha256"] = hashlib.sha256(_canonical(unsigned).encode("utf-8")).hexdigest()
    return unsigned


def _parse_registry(raw: bytes, path: Path) -> list[dict[str, Any]]:
    if raw and not raw.endswith(b"\n"):
        raise DefinitionRegistryError(f"definition registry must end with a complete JSONL record: {path}")
    records: list[dict[str, Any]] = []
    seen: dict[tuple[str, str, str], str] = {}
    for line_number, line in enumerate(raw.splitlines(), start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DefinitionRegistryError(f"invalid definition JSON at {path}:{line_number}") from exc
        if not isinstance(record, dict) or set(record) != {
            "schema_version", "kind", "definition_id", "version", "implementation_sha256",
            "config_schema", "description", "definition_sha256",
        }:
            raise DefinitionRegistryError(f"invalid definition record fields at {path}:{line_number}")
        unsigned = {key: value for key, value in record.items() if key != "definition_sha256"}
        if (
            record.get("schema_version") != 1
            or record.get("kind") not in {"feature", "strategy"}
            or not isinstance(record.get("definition_id"), str)
            or not isinstance(record.get("version"), str)
            or not _is_sha256(record.get("implementation_sha256"))
            or not isinstance(record.get("config_schema"), dict)
            or not isinstance(record.get("description"), str)
            or record.get("definition_sha256") != hashlib.sha256(_canonical(unsigned).encode("utf-8")).hexdigest()
        ):
            raise DefinitionRegistryError(f"definition hash or schema validation failed at {path}:{line_number}")
        key = (record["kind"], record["definition_id"], record["version"])
        prior = seen.get(key)
        if prior is not None:
            raise DefinitionRegistryError(f"duplicate immutable definition key at {path}:{line_number}")
        seen[key] = record["definition_sha256"]
        records.append(record)
    return records


def validate_definition_record(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one embedded immutable definition record and its content hash."""
    record = dict(value)
    expected = {
        "schema_version", "kind", "definition_id", "version", "implementation_sha256",
        "config_schema", "description", "definition_sha256",
    }
    unsigned = {key: item for key, item in record.items() if key != "definition_sha256"}
    if (
        set(record) != expected
        or type(record.get("schema_version")) is not int
        or record.get("schema_version") != 1
        or record.get("kind") not in {"feature", "strategy"}
        or not isinstance(record.get("definition_id"), str)
        or not _IDENTIFIER.fullmatch(str(record.get("definition_id")))
        or not isinstance(record.get("version"), str)
        or not record["version"].strip()
        or not _is_sha256(record.get("implementation_sha256"))
        or not isinstance(record.get("config_schema"), dict)
        or not isinstance(record.get("description"), str)
        or record.get("definition_sha256") != hashlib.sha256(_canonical(unsigned).encode("utf-8")).hexdigest()
    ):
        raise DefinitionRegistryError("embedded definition record has an invalid schema or content hash")
    return record


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DefinitionRegistryError("definition/config contains non-JSON or non-finite values") from exc


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
