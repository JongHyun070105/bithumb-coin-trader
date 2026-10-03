from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from scripts.create_fresh_30h_capture_manifest import create_capture_manifest


def test_manifest_binds_every_terminal_source_and_returns_external_hash(tmp_path: Path) -> None:
    terminal = tmp_path / "terminal"
    (terminal / "s3-readback").mkdir(parents=True)
    (terminal / "result.json").write_bytes(b"result evidence")
    (terminal / "s3-readback/receipt.bin").write_bytes(b"remote bytes")

    path, external_hash = create_capture_manifest(
        terminal_dir=terminal, run_id="run-1", epoch="epoch-1",
        runtime_commit="a" * 40, runtime_tree="b" * 40,
        now=lambda: datetime(2026, 10, 3, tzinfo=timezone.utc),
    )
    payload = json.loads(path.read_text())
    sources = {row["path"]: row for row in payload["sources"]}
    assert set(sources) == {"terminal/result.json", "terminal/s3-readback/receipt.bin"}
    assert sources["terminal/result.json"]["sha256"] == hashlib.sha256(b"result evidence").hexdigest()
    assert sources["terminal/s3-readback/receipt.bin"]["size"] == len(b"remote bytes")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == external_hash


def test_manifest_is_create_only_and_rejects_symlinks(tmp_path: Path) -> None:
    terminal = tmp_path / "terminal"
    terminal.mkdir()
    source = tmp_path / "source.json"
    source.write_text("{}")
    (terminal / "alias.json").symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        create_capture_manifest(
            terminal_dir=terminal, run_id="run-1", epoch="epoch-1",
            runtime_commit="a" * 40, runtime_tree="b" * 40,
        )
    (terminal / "alias.json").unlink()
    create_capture_manifest(
        terminal_dir=terminal, run_id="run-1", epoch="epoch-1",
        runtime_commit="a" * 40, runtime_tree="b" * 40,
    )
    with pytest.raises(FileExistsError, match="already exists"):
        create_capture_manifest(
            terminal_dir=terminal, run_id="run-1", epoch="epoch-1",
            runtime_commit="a" * 40, runtime_tree="b" * 40,
        )
