#!/usr/bin/env python3
"""Deterministically export explicitly named files into a v2 audit bundle.

This is an offline copier/hasher. It does not query AWS, discover sources,
adjudicate an incomplete run, or overwrite an existing output directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
from typing import Sequence

try:
    from .audit_fresh_30h_terminal_v2 import REQUIRED_PAYLOADS, SHA256_RE, sha256_file
except ImportError:  # Executed directly as `python scripts/export_...py`.
    from audit_fresh_30h_terminal_v2 import REQUIRED_PAYLOADS, SHA256_RE, sha256_file


def _safe_destination(value: str) -> bool:
    path = PurePosixPath(value)
    return not path.is_absolute() and path.as_posix() == value and all(p not in ("", ".", "..") for p in path.parts)


def _mapping(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("source must be DESTINATION=SOURCE_PATH")
    destination, source = value.split("=", 1)
    if not _safe_destination(destination) or not source:
        raise argparse.ArgumentTypeError("source destination must be a safe relative path")
    return destination, Path(source)


def export_bundle(*, output_dir: Path, sources: Sequence[tuple[str, Path]], run_id: str,
                  epoch: str, runtime_commit: str, runtime_tree: str,
                  sealed_manifest_sha256: str, capture_manifest_sha256: str) -> Path:
    if not SHA256_RE.fullmatch(sealed_manifest_sha256):
        raise ValueError("a valid external sealed-manifest SHA256 is required")
    if not SHA256_RE.fullmatch(capture_manifest_sha256):
        raise ValueError("a valid external post-run capture-manifest SHA256 is required")
    destinations = [dest for dest, _ in sources]
    if any(not _safe_destination(dest) for dest in destinations):
        raise ValueError("bundle destinations must be safe relative paths")
    if len(set(destinations)) != len(destinations):
        raise ValueError("duplicate bundle destinations")
    required = set(REQUIRED_PAYLOADS) | {"sealed/runtime.json"}
    if not required.issubset(destinations):
        raise ValueError(f"missing required sealed sources: {sorted(required - set(destinations))}")
    if output_dir.is_symlink():
        raise ValueError("output directory must not be a symlink")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError("output directory must be absent or empty")
    source_map: dict[str, Path] = {}
    for destination, source in sources:
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"source is not a regular file: {source}")
        source_map[destination] = source
    seal_source = source_map["sealed/sealed-manifest.json"]
    identity_source = source_map["sealed/identity.json"]
    runtime_source = source_map["sealed/runtime.json"]
    capture_source = source_map["terminal/capture-manifest.json"]
    if sha256_file(seal_source) != sealed_manifest_sha256:
        raise ValueError("sealed-manifest source differs from the external hash anchor")
    if sha256_file(capture_source) != capture_manifest_sha256:
        raise ValueError("capture-manifest source differs from the external hash anchor")
    sealed = json.loads(seal_source.read_text(encoding="utf-8"))
    identity = json.loads(identity_source.read_text(encoding="utf-8"))
    runtime = json.loads(runtime_source.read_text(encoding="utf-8"))
    if not isinstance(sealed, dict) or not isinstance(identity, dict) or not isinstance(runtime, dict):
        raise ValueError("sealed JSON sources must be objects")
    if sha256_file(identity_source) != sealed.get("identity_sha256"):
        raise ValueError("identity bytes do not match sealed-manifest identity_sha256")
    if identity.get("run_id") != run_id or identity.get("epoch") != epoch:
        raise ValueError("sealed identity differs from requested run/epoch")
    if identity.get("software_commit_sha") != runtime_commit or identity.get("software_tree_sha") != runtime_tree:
        raise ValueError("sealed identity differs from requested runtime commit/tree")
    if runtime.get("runtime_software_commit") != runtime_commit:
        raise ValueError("runtime config commit differs from requested commit")
    artifact_hashes = sealed.get("artifact_hashes")
    identity_hashes = identity.get("sealed_artifact_hashes")
    if not isinstance(artifact_hashes, dict) or artifact_hashes != identity_hashes:
        raise ValueError("sealed artifact hash maps disagree")
    for name, expected_hash in artifact_hashes.items():
        path = source_map.get(f"sealed/artifacts/{name}")
        if path is None or not isinstance(expected_hash, str) or sha256_file(path) != expected_hash:
            raise ValueError(f"missing or mismatched sealed artifact copy: {name}")
    allowed_sealed = {"sealed/sealed-manifest.json", "sealed/identity.json", "sealed/runtime.json"}
    allowed_sealed.update(f"sealed/artifacts/{name}" for name in artifact_hashes)
    if sha256_file(runtime_source) != next((value for name, value in artifact_hashes.items() if name.endswith(".runtime.json")), None):
        raise ValueError("runtime config copy is not the sealed runtime artifact")
    capture = json.loads(capture_source.read_text(encoding="utf-8"))
    if not isinstance(capture, dict) or capture.get("schema") != "Fresh30HTerminalCapture" or capture.get("version") != 1:
        raise ValueError("post-run capture manifest schema/version is invalid")
    for key, expected in (("run_id", run_id), ("epoch", epoch),
                          ("runtime_commit", runtime_commit), ("runtime_tree", runtime_tree)):
        if capture.get(key) != expected:
            raise ValueError(f"post-run capture manifest {key} differs from requested identity")
    capture_records = capture.get("sources")
    if not isinstance(capture_records, list):
        raise ValueError("post-run capture manifest source inventory is missing")
    captured: dict[str, tuple[int, str]] = {}
    for record in capture_records:
        if not isinstance(record, dict):
            raise ValueError("post-run capture manifest contains a malformed source record")
        path, size, digest = record.get("path"), record.get("size"), record.get("sha256")
        if not isinstance(path, str) or not _safe_destination(path) or path in captured:
            raise ValueError("post-run capture manifest contains an unsafe or duplicate path")
        if type(size) is not int or size < 0 or not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ValueError(f"post-run capture source metadata is invalid: {path}")
        captured[path] = (size, digest)
    source_terminal = {dest: (src.stat().st_size, sha256_file(src)) for dest, src in sources
                       if dest.startswith("terminal/") and dest not in {
                           "terminal/capture-manifest.json", "terminal/evidence-hash-index.json"}}
    if captured != source_terminal:
        raise ValueError("post-run capture manifest does not bind every terminal source byte")
    allowed_terminal = set(captured) | {"terminal/capture-manifest.json"}
    unexpected_sources = sorted(dest for dest in destinations if dest.startswith(("sealed/", "terminal/"))
                                and dest not in allowed_sealed | allowed_terminal)
    outside_scope = sorted(dest for dest in destinations if not dest.startswith(("sealed/", "terminal/")))
    if unexpected_sources or outside_scope:
        raise ValueError(f"unallowlisted bundle sources: {unexpected_sources + outside_scope}")
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        for destination, source in sources:
            dest = output_dir / destination
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
        files = []
        for destination, _source in sorted(sources):
            path = output_dir / destination
            files.append({"path": destination, "size": path.stat().st_size, "sha256": sha256_file(path)})
        manifest = {
            "schema": "Fresh30HTerminalBundle", "version": 2,
            "run_id": run_id, "epoch": epoch,
            "runtime_commit": runtime_commit, "runtime_tree": runtime_tree,
            "external_sealed_manifest_sha256": sealed_manifest_sha256,
            "external_capture_manifest_sha256": capture_manifest_sha256,
            "payloads": files,
        }
        _write_json(output_dir / "bundle-manifest.json", manifest)
        index_files = []
        for path in sorted(p for p in output_dir.rglob("*") if p.is_file()):
            rel = path.relative_to(output_dir).as_posix()
            if rel == "terminal/evidence-hash-index.json":
                continue
            index_files.append({"path": rel, "size": path.stat().st_size, "sha256": sha256_file(path)})
        _write_json(output_dir / "terminal/evidence-hash-index.json", {"schema": 1, "files": index_files})
        return output_dir
    except Exception:
        # Preserve the partial output for inspection; never delete user evidence.
        raise


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", required=True, type=Path)
    p.add_argument("--run-id", required=True)
    p.add_argument("--epoch", required=True)
    p.add_argument("--runtime-commit", required=True)
    p.add_argument("--runtime-tree", required=True)
    p.add_argument("--sealed-manifest-sha256", required=True)
    p.add_argument("--capture-manifest-sha256", required=True)
    p.add_argument("--source", action="append", type=_mapping, required=True, metavar="DEST=PATH",
                   help="explicit file source; repeat for each bundle path")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    export_bundle(output_dir=args.output_dir, sources=args.source, run_id=args.run_id,
        epoch=args.epoch, runtime_commit=args.runtime_commit, runtime_tree=args.runtime_tree,
        sealed_manifest_sha256=args.sealed_manifest_sha256,
        capture_manifest_sha256=args.capture_manifest_sha256)
    print(f"EXPORTED: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
