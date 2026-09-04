"""Unit tests for archive runtime ownership verification and safe lock semantics."""

import os
from pathlib import Path
import pwd
import tempfile
import pytest

from bithumb_coin_trader.pre_soak_archive import (
    OwnershipViolationError,
    _partition_lock,
    verify_runtime_ownership,
)


def current_user_name() -> str:
    return pwd.getpwuid(os.getuid()).pw_name


def test_verify_runtime_ownership_current_user_passes(tmp_path: Path):
    test_dir = tmp_path / "runtime"
    test_dir.mkdir()
    child_file = test_dir / "data.jsonl"
    child_file.write_text('{"test": 1}\n', encoding="utf-8")

    # Current user check should pass without error
    verify_runtime_ownership((test_dir,), expected_owner=current_user_name())
    verify_runtime_ownership((test_dir,), expected_owner=None)


def test_verify_runtime_ownership_nonexistent_path_ignored(tmp_path: Path):
    missing_dir = tmp_path / "does_not_exist"
    verify_runtime_ownership((missing_dir,), expected_owner=current_user_name())


def test_verify_runtime_ownership_unknown_user_raises(tmp_path: Path):
    test_dir = tmp_path / "runtime"
    test_dir.mkdir()
    with pytest.raises(OwnershipViolationError, match="expected owner 'non_existent_user_xyz' does not exist"):
        verify_runtime_ownership((test_dir,), expected_owner="non_existent_user_xyz")


def test_verify_runtime_ownership_symlink_rejected(tmp_path: Path):
    real_file = tmp_path / "real.txt"
    real_file.write_text("ok", encoding="utf-8")
    symlink_file = tmp_path / "link.txt"
    symlink_file.symlink_to(real_file)

    with pytest.raises(ValueError, match="symlink runtime path is not allowed"):
        verify_runtime_ownership((symlink_file,))


def test_verify_runtime_ownership_symlink_child_rejected(tmp_path: Path):
    real_dir = tmp_path / "dir"
    real_dir.mkdir()
    target = tmp_path / "target.txt"
    target.write_text("ok", encoding="utf-8")
    symlink = real_dir / "link_child.txt"
    symlink.symlink_to(target)

    with pytest.raises(ValueError, match="symlink runtime path is not allowed"):
        verify_runtime_ownership((real_dir,))


def test_verify_runtime_ownership_mismatched_uid(tmp_path: Path, monkeypatch):
    test_file = tmp_path / "test.file"
    test_file.write_text("data", encoding="utf-8")

    # Mock os.stat to return a different UID (e.g. 0 for root)
    orig_stat = Path.stat

    def mock_stat(self, *args, **kwargs):
        st = orig_stat(self, *args, **kwargs)
        # return a mock with st_uid = 0 (root)
        return os.stat_result((
            st.st_mode,
            st.st_ino,
            st.st_dev,
            st.st_nlink,
            0,  # root UID
            st.st_gid,
            st.st_size,
            st.st_atime,
            st.st_mtime,
            st.st_ctime,
        ))

    monkeypatch.setattr(Path, "stat", mock_stat)

    with pytest.raises(OwnershipViolationError, match="Fail-closed ownership violation: .* owned by UID 0"):
        verify_runtime_ownership((test_file,), expected_owner=current_user_name())


def test_partition_lock_creation_and_release(tmp_path: Path):
    lock_file = tmp_path / "partition.lock"
    assert not lock_file.exists()

    with _partition_lock(lock_file, expected_owner=current_user_name()):
        assert lock_file.exists()
        # Verify permissions: 0o600 (-rw-------)
        mode = oct(lock_file.stat().st_mode & 0o777)
        assert mode == "0o600"

    # Lock is released, file remains (kernel closes fd, releasing flock)
    assert lock_file.exists()


def test_partition_lock_stale_lock_reuse(tmp_path: Path):
    lock_file = tmp_path / "stale.lock"
    # Pre-create empty lock file with matching user
    lock_file.touch(mode=0o600)

    # Should safely acquire and reuse the stale lock
    with _partition_lock(lock_file, expected_owner=current_user_name()):
        assert lock_file.exists()


def test_partition_lock_active_rejection(tmp_path: Path):
    lock_file = tmp_path / "active.lock"

    with _partition_lock(lock_file, expected_owner=current_user_name()):
        # Attempt concurrent acquire raises RuntimeError
        with pytest.raises(RuntimeError, match="partition is already claimed by another worker"):
            with _partition_lock(lock_file, expected_owner=current_user_name()):
                pass


def test_partition_lock_foreign_owner_fail_closed(tmp_path: Path, monkeypatch):
    lock_file = tmp_path / "foreign.lock"
    lock_file.touch(mode=0o600)

    orig_stat = Path.stat

    def mock_stat(self, *args, **kwargs):
        st = orig_stat(self, *args, **kwargs)
        if self == lock_file:
            return os.stat_result((
                st.st_mode,
                st.st_ino,
                st.st_dev,
                st.st_nlink,
                0,  # root UID
                st.st_gid,
                st.st_size,
                st.st_atime,
                st.st_mtime,
                st.st_ctime,
            ))
        return st

    monkeypatch.setattr(Path, "stat", mock_stat)

    with pytest.raises(OwnershipViolationError, match="Fail-closed ownership violation"):
        with _partition_lock(lock_file, expected_owner=current_user_name()):
            pass
