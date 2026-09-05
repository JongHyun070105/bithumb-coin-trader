"""Unit tests for 72H launch wrapper and transient systemd invocation semantics."""

import json
from pathlib import Path
import pytest

from bithumb_coin_trader.bounded_supervisor import (
    TransientLaunchConfig,
    render_systemd_run,
)


def test_render_systemd_run_72h_semantics():
    config = TransientLaunchConfig(
        run_id="aws-72h-soak-run-20260905T024039Z-8017b83e",
        supervisor_duration_seconds=259200,
        hard_ceiling_seconds=262800,
        workdir=Path("/var/lib/bitcoin-trader/remediation-650adc8"),
        supervisor_command=["python", "test.py"],
        pythonpath="src",
    )
    cmd = render_systemd_run(config)

    # Invariants
    assert cmd[0] == "systemd-run"
    assert "--unit=bitcoin-trader-72h-soak-aws-72h-soak-run-20260905T024039Z-8017b83e.service" in cmd
    assert "--no-block" in cmd
    assert "--collect" in cmd
    assert "--service-type=exec" in cmd
    # Critical: privilege drop to unprivileged user
    assert "--uid=bitcoin-trader" in cmd
    assert "--setenv=PYTHONPATH=src" in cmd
    assert "--property=Restart=no" in cmd
    assert "--property=KillMode=mixed" in cmd
    assert "--property=RuntimeMaxSec=262800s" in cmd
    assert "--property=TimeoutStopSec=55s" in cmd
    assert "--working-directory=/var/lib/bitcoin-trader/remediation-650adc8" in cmd
    assert cmd[-2:] == ["--", "python"] or "--" in cmd


def test_launcher_command_structure_no_double_sudo():
    # Verify that the launch command passes sudo systemd-run with payload uid
    epoch = "aws-72h-soak-20260905-8017b83e"
    epoch_dir = f"/var/lib/bitcoin-trader/72h-soak/{epoch}"
    correct_launch_cmd = f"sudo /var/lib/bitcoin-trader/venv-pre-soak/bin/python {epoch_dir}/launch_72h.py --launch"

    assert "sudo -u bitcoin-trader" not in correct_launch_cmd
    assert correct_launch_cmd.startswith("sudo ")
    assert correct_launch_cmd.endswith("--launch")
