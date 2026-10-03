#!/usr/bin/env python3
"""Freeze terminal evidence sources into an externally anchored capture manifest."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Callable, Sequence


def _hash_stable(path: Path) -> tuple[int, str]:
    if path.is_symlink():
        raise ValueError(f"captured terminal source must not be a symlink: {path}")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    size = 0
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError(f"captured terminal source must be a singly-linked regular file: {path}")
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
        after = os.fstat(stream.fileno())
    before_state = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_nlink)
    after_state = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink)
    if before_state != after_state or size != after.st_size:
        raise ValueError(f"captured terminal source changed while hashing: {path}")
    return size, digest.hexdigest()


def create_capture_manifest(
    *,
    terminal_dir: Path,
    run_id: str,
    epoch: str,
    runtime_commit: str,
    runtime_tree: str,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> tuple[Path, str]:
    """Create one exclusive manifest that binds every captured terminal file."""
    if terminal_dir.is_symlink() or not terminal_dir.is_dir():
        raise ValueError("terminal evidence directory must be an existing real directory")
    if not all((run_id, epoch, runtime_commit, runtime_tree)):
        raise ValueError("exact run/epoch/runtime identity is required")
    manifest_path = terminal_dir / "capture-manifest.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        raise FileExistsError("capture manifest already exists")
    files: list[dict[str, Any]] = []
    for path in sorted(terminal_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"captured terminal tree contains a symlink: {path}")
        if not path.is_file():
            continue
        rel = path.relative_to(terminal_dir).as_posix()
        if rel in {"capture-manifest.json", "evidence-hash-index.json"}:
            continue
        size, digest = _hash_stable(path)
        files.append({
            "path": f"terminal/{rel}", "size": size, "sha256": digest,
            "source_kind": "postrun_capture",
        })
    captured_at = now()
    if captured_at.tzinfo is None:
        raise ValueError("capture manifest clock must be timezone-aware")
    payload = {
        "schema": "Fresh30HTerminalCapture", "version": 1,
        "run_id": run_id, "epoch": epoch,
        "runtime_commit": runtime_commit, "runtime_tree": runtime_tree,
        "captured_at_utc": captured_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sources": files,
    }
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    fd = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        if stream.write(data) != len(data):
            raise OSError("short write while creating capture manifest")
        stream.flush()
        os.fsync(stream.fileno())
    dir_fd = os.open(terminal_dir, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return manifest_path, hashlib.sha256(data).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terminal-dir", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--epoch", required=True)
    parser.add_argument("--runtime-commit", required=True)
    parser.add_argument("--runtime-tree", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        path, digest = create_capture_manifest(
            terminal_dir=args.terminal_dir, run_id=args.run_id, epoch=args.epoch,
            runtime_commit=args.runtime_commit, runtime_tree=args.runtime_tree,
        )
    except Exception as exc:
        print(f"CAPTURE_MANIFEST_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"CAPTURE_MANIFEST: {path}\nSHA256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
