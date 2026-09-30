#!/usr/bin/env python3
"""Deterministically export explicitly named files into a v2 audit bundle.

This is an offline copier/hasher. It does not query AWS, discover sources,
adjudicate an incomplete run, or overwrite an existing output directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
from typing import Sequence

try:
    from .audit_fresh_30h_terminal_v2 import MAX_JSON_BYTES, REQUIRED_PAYLOADS, SHA256_RE, sha256_file, strict_json_loads
except ImportError:  # Executed directly as `python scripts/export_...py`.
    from audit_fresh_30h_terminal_v2 import MAX_JSON_BYTES, REQUIRED_PAYLOADS, SHA256_RE, sha256_file, strict_json_loads


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
    sealed = _read_source_json(seal_source)
    identity = _read_source_json(identity_source)
    runtime = _read_source_json(runtime_source)
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
    capture = _read_source_json(capture_source)
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
    if output_dir.exists():
        raise FileExistsError("output directory must not exist")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.incomplete-", dir=output_dir.parent))
    expected_hashes: dict[str, str] = {
        "sealed/sealed-manifest.json": sealed_manifest_sha256,
        "sealed/identity.json": sealed["identity_sha256"],
        "sealed/runtime.json": next(value for name, value in artifact_hashes.items() if name.endswith(".runtime.json")),
        "terminal/capture-manifest.json": capture_manifest_sha256,
    }
    expected_hashes.update({f"sealed/artifacts/{name}": digest for name, digest in artifact_hashes.items()})
    expected_hashes.update({path: digest for path, (_size, digest) in captured.items()})
    try:
        for destination, source in sources:
            dest = stage / destination
            dest.parent.mkdir(parents=True, exist_ok=True)
            _copy_verified(source, dest, expected_hashes[destination])
        files = []
        for destination, _source in sorted(sources):
            path = stage / destination
            files.append({"path": destination, "size": path.stat().st_size, "sha256": sha256_file(path)})
        manifest = {
            "schema": "Fresh30HTerminalBundle", "version": 2,
            "run_id": run_id, "epoch": epoch,
            "runtime_commit": runtime_commit, "runtime_tree": runtime_tree,
            "external_sealed_manifest_sha256": sealed_manifest_sha256,
            "external_capture_manifest_sha256": capture_manifest_sha256,
            "payloads": files,
        }
        _write_json(stage / "bundle-manifest.json", manifest)
        index_files = []
        for path in sorted(p for p in stage.rglob("*") if p.is_file()):
            rel = path.relative_to(stage).as_posix()
            if rel == "terminal/evidence-hash-index.json":
                continue
            index_files.append({"path": rel, "size": path.stat().st_size, "sha256": sha256_file(path)})
        _write_json(stage / "terminal/evidence-hash-index.json", {"schema": 1, "files": index_files})
        _fsync_directories(stage)
        if output_dir.exists():
            raise FileExistsError("output directory appeared during export")
        os.rename(stage, output_dir)
        _fsync_directory(output_dir.parent)
        return output_dir
    except BaseException as exc:
        # A crash leaves a clearly named incomplete stage, never the final bundle path.
        try:
            (stage / "INCOMPLETE.txt").write_text(
                f"Bundle export did not commit. {type(exc).__name__}: {exc}\n", encoding="utf-8")
            _fsync_directory(stage)
        except OSError:
            pass
        raise


def _copy_verified(source: Path, destination: Path, expected_sha256: str) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(source, flags)
    digest = hashlib.sha256()
    total = 0
    with os.fdopen(descriptor, "rb") as src:
        before = os.fstat(src.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError(f"source is not a regular singly-linked file: {source}")
        path_before = source.lstat()
        if path_before.st_dev != before.st_dev or path_before.st_ino != before.st_ino or stat.S_ISLNK(path_before.st_mode):
            raise ValueError(f"source path changed before copy: {source}")
        with destination.open("xb") as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(chunk)
                digest.update(chunk)
                total += len(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        after = os.fstat(src.fileno())
    path_after = source.lstat()
    stable_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_nlink)
    stable_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink)
    if stable_before != stable_after or path_after.st_dev != before.st_dev or path_after.st_ino != before.st_ino:
        raise ValueError(f"source changed during copy: {source}")
    if total != after.st_size or digest.hexdigest() != expected_sha256:
        raise ValueError(f"copied source differs from its frozen hash: {source}")


def _read_source_json(path: Path) -> object:
    with path.open("rb") as stream:
        data = stream.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise ValueError(f"source JSON exceeds {MAX_JSON_BYTES}-byte limit: {path}")
    return strict_json_loads(data.decode("utf-8"))


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directories(root: Path) -> None:
    for path in sorted((item for item in root.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
        _fsync_directory(path)
    _fsync_directory(root)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


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
