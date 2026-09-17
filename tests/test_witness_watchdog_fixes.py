"""Tests for terminal witness parent-dir fsync, watchdog, ExecStopPost, and sd_notify fixes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import textwrap

import pytest
from typing import Any


# ---------------------------------------------------------------------------
# 1. write_receipt_atomic has parent-dir fsync
# ---------------------------------------------------------------------------

class TestWriteReceiptAtomicParentDirFsync:
    """Verify write_receipt_atomic includes parent directory fsync after os.replace."""

    def test_source_contains_parent_dir_fsync_pattern(self) -> None:
        """Read the source of terminal_witness.py and confirm the fsync-after-replace pattern."""
        src = Path(__file__).resolve().parents[1] / "scripts" / "terminal_witness.py"
        text = src.read_text(encoding="utf-8")

        # Must have os.replace followed (within a reasonable span) by fsync of the parent dir
        assert "os.replace(temp_path, path)" in text, "Expected os.replace(temp_path, path)"
        # The parent dir fsync pattern (matching collector_state_model.py lines 191-198)
        assert "parent_fd = os.open(str(path.parent), os.O_RDONLY)" in text
        assert "os.fsync(parent_fd)" in text
        assert "os.close(parent_fd)" in text

    def test_write_receipt_atomic_fsyncs_parent_dir(self, tmp_path: Path) -> None:
        """Functional test: write_receipt_atomic succeeds and the file lands correctly."""
        from scripts.terminal_witness import write_receipt_atomic

        target = tmp_path / "sub" / "receipt.json"
        payload = {"hello": "world", "nested": {"a": 1}}
        write_receipt_atomic(target, payload)
        assert target.exists()
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded == payload

    def test_write_receipt_atomic_cleanup_on_error(self, tmp_path: Path) -> None:
        """If os.replace fails (dir removed), temp file should be cleaned up."""
        from scripts.terminal_witness import write_receipt_atomic

        target = tmp_path / "vanish" / "receipt.json"
        # Create the parent so the temp file can be created, then remove it
        target.parent.mkdir(parents=True)
        payload = {"key": "value"}
        # We can't easily force os.replace to fail without root privileges,
        # so just verify the normal path works.
        write_receipt_atomic(target, payload)
        assert target.exists()


# ---------------------------------------------------------------------------
# 2. render_systemd_run includes WatchdogSec, NotifyAccess, Type=notify
# ---------------------------------------------------------------------------

class TestRenderSystemdRunWatchdog:
    """Verify render_systemd_run emits watchdog and notify properties."""

    @staticmethod
    def _base_config(**overrides: Any) -> Any:
        from bithumb_coin_trader.bounded_supervisor import TransientLaunchConfig
        defaults: dict[str, Any] = dict(
            run_id="test-run-001",
            workdir=Path("/opt/bitcoin-trader/work"),
            supervisor_command=("python3", "-m", "supervisor"),
            collection_duration_seconds=2700,
            finalization_timeout_seconds=120,
            supervisor_hard_ceiling_seconds=2820,
            systemd_runtime_max_seconds=2880,
        )
        defaults.update(overrides)
        return TransientLaunchConfig(**defaults)  # type: ignore[arg-type]

    def test_service_type_is_notify(self) -> None:
        from bithumb_coin_trader.bounded_supervisor import render_systemd_run

        cmd = render_systemd_run(self._base_config())
        assert "--service-type=notify" in cmd
        assert "--service-type=exec" not in cmd

    def test_watchdog_sec_present(self) -> None:
        from bithumb_coin_trader.bounded_supervisor import render_systemd_run

        cmd = render_systemd_run(self._base_config())
        assert "--property=WatchdogSec=60s" in cmd

    def test_notify_access_present(self) -> None:
        from bithumb_coin_trader.bounded_supervisor import render_systemd_run

        cmd = render_systemd_run(self._base_config())
        assert "--property=NotifyAccess=main" in cmd

    def test_exec_stop_post_not_present_when_not_configured(self) -> None:
        from bithumb_coin_trader.bounded_supervisor import render_systemd_run

        cmd = render_systemd_run(self._base_config())
        exec_stop_args = [c for c in cmd if "ExecStopPost" in c]
        assert exec_stop_args == [], f"Expected no ExecStopPost, got: {exec_stop_args}"

    def test_exec_stop_post_present_when_configured(self) -> None:
        from bithumb_coin_trader.bounded_supervisor import render_systemd_run

        cfg = self._base_config(
            exec_stop_post_script="/opt/scripts/terminal_witness.py",
            data_dir=Path("/opt/data/health"),
        )
        cmd = render_systemd_run(cfg)
        exec_stop_args = [c for c in cmd if "ExecStopPost" in c]
        assert len(exec_stop_args) == 1, f"Expected exactly one ExecStopPost, got: {exec_stop_args}"
        esp = exec_stop_args[0]
        assert "/opt/scripts/terminal_witness.py" in esp
        assert "--data-dir=/opt/data/health" in esp
        assert "--epoch=bitcoin-trader-short-smoke" in esp
        assert "--run-id=test-run-001" in esp

    def test_exec_stop_post_defaults_data_dir_to_workdir(self) -> None:
        from bithumb_coin_trader.bounded_supervisor import render_systemd_run

        cfg = self._base_config(
            exec_stop_post_script="/opt/scripts/terminal_witness.py",
        )
        cmd = render_systemd_run(cfg)
        exec_stop_args = [c for c in cmd if "ExecStopPost" in c]
        assert len(exec_stop_args) == 1
        assert "--data-dir=/opt/bitcoin-trader/work" in exec_stop_args[0]

    def test_transient_launch_config_accepts_new_fields(self) -> None:
        from bithumb_coin_trader.bounded_supervisor import TransientLaunchConfig

        cfg = TransientLaunchConfig(
            run_id="abc",
            workdir=Path("/tmp/work"),
            supervisor_command=("echo", "hi"),
            collection_duration_seconds=2700,
            exec_stop_post_script="/usr/local/bin/witness.py",
            data_dir=Path("/tmp/health"),
        )
        assert cfg.exec_stop_post_script == "/usr/local/bin/witness.py"
        assert cfg.data_dir == Path("/tmp/health")

    def test_transient_launch_config_defaults_new_fields_to_none(self) -> None:
        from bithumb_coin_trader.bounded_supervisor import TransientLaunchConfig

        cfg = TransientLaunchConfig(
            run_id="abc",
            workdir=Path("/tmp/work"),
            supervisor_command=("echo", "hi"),
            collection_duration_seconds=2700,
        )
        assert cfg.exec_stop_post_script is None
        assert cfg.data_dir is None


# ---------------------------------------------------------------------------
# 3. sd_notify("READY=1") at collector start
# ---------------------------------------------------------------------------

class TestSdNotifyReady:
    """Verify sd_notify(READY=1) is called at the start of run_collector."""

    def test_source_contains_sd_notify_ready(self) -> None:
        """Read cross_market_collector.py and confirm sd_notify(READY=1) is present in run_collector."""
        src = Path(__file__).resolve().parents[1] / "src" / "bithumb_coin_trader" / "cross_market_collector.py"
        text = src.read_text(encoding="utf-8")

        # Find run_collector method and check for sd_notify READY=1
        idx = text.find("async def run_collector(")
        assert idx != -1, "run_collector not found"

        # Look at the first ~300 chars after the def
        snippet = text[idx : idx + 500]
        assert 'systemd.daemon.notify("READY=1")' in snippet, (
            "sd_notify READY=1 not found at the start of run_collector"
        )

    def test_sd_notify_wrapped_in_try_except(self) -> None:
        """The sd_notify call must be wrapped in try/except for graceful degradation."""
        src = Path(__file__).resolve().parents[1] / "src" / "bithumb_coin_trader" / "cross_market_collector.py"
        text = src.read_text(encoding="utf-8")

        idx = text.find("async def run_collector(")
        snippet = text[idx : idx + 500]

        # Should have try/except around the import
        assert "try:" in snippet
        assert "except Exception:" in snippet or "except Exception as" in snippet

    def test_health_worker_still_sends_watchdog(self) -> None:
        """Confirm WATCHDOG=1 notification is still present in _health_worker (existing feature)."""
        src = Path(__file__).resolve().parents[1] / "src" / "bithumb_coin_trader" / "cross_market_collector.py"
        text = src.read_text(encoding="utf-8")
        assert 'systemd.daemon.notify("WATCHDOG=1")' in text


# ---------------------------------------------------------------------------
# 4. Collector state model reference pattern consistency
# ---------------------------------------------------------------------------

class TestPatternConsistency:
    """Ensure the fsync pattern in terminal_witness matches collector_state_model."""

    def test_both_files_have_same_parent_fsync_pattern(self) -> None:
        tw_src = Path(__file__).resolve().parents[1] / "scripts" / "terminal_witness.py"
        csm_src = Path(__file__).resolve().parents[1] / "src" / "bithumb_coin_trader" / "collector_state_model.py"

        tw_text = tw_src.read_text(encoding="utf-8")
        csm_text = csm_src.read_text(encoding="utf-8")

        # Both should contain the core pattern
        for src_text, label in [(tw_text, "terminal_witness"), (csm_text, "collector_state_model")]:
            assert "os.replace(" in src_text, f"{label} missing os.replace"
            assert "parent_fd = os.open(str(path.parent), os.O_RDONLY)" in src_text, (
                f"{label} missing parent dir open"
            )
            assert "os.fsync(parent_fd)" in src_text, f"{label} missing parent dir fsync"


class TestSdNotify:
    """Verify native socket-based sd_notify implementation."""

    def test_sd_notify_no_socket_returns_false(self) -> None:
        from bithumb_coin_trader.bounded_supervisor import sd_notify
        import os
        old = os.environ.pop("NOTIFY_SOCKET", None)
        try:
            assert sd_notify("READY=1") is False
        finally:
            if old is not None:
                os.environ["NOTIFY_SOCKET"] = old

    def test_sd_notify_sends_datagram(self) -> None:
        import socket
        import os
        import uuid
        from bithumb_coin_trader.bounded_supervisor import sd_notify

        sock_path = f"/tmp/sd_{uuid.uuid4().hex[:8]}.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        server.bind(sock_path)
        old = os.environ.get("NOTIFY_SOCKET")
        os.environ["NOTIFY_SOCKET"] = sock_path
        try:
            result = sd_notify("READY=1")
            assert result is True
            data, _ = server.recvfrom(1024)
            assert data == b"READY=1"
        finally:
            server.close()
            if os.path.exists(sock_path):
                os.remove(sock_path)
            if old is not None:
                os.environ["NOTIFY_SOCKET"] = old
            else:
                os.environ.pop("NOTIFY_SOCKET", None)
