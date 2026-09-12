from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bithumb_coin_trader.bounded_supervisor import TransientLaunchConfig, render_systemd_run
from scripts.launch_short_smoke_transient import (
    _extract_supervisor_duration,
    _validate_cross_layer_duration,
    main as launch_transient_main,
)


class TransientLaunchTests(unittest.TestCase):
    def test_renderer_is_detached_finite_non_restarting_and_run_scoped(self) -> None:
        command = render_systemd_run(
            TransientLaunchConfig(
                run_id="aws-short-smoke-run-20260903-ab12cd34",
                workdir=Path("/opt/bitcoin-trader"),
                supervisor_command=("/opt/bitcoin-trader/.venv/bin/python", "scripts/run_bounded_short_smoke.py"),
                collection_duration_seconds=2700,
                finalization_timeout_seconds=120,
                supervisor_hard_ceiling_seconds=2820,
                systemd_runtime_max_seconds=2880,
            )
        )
        rendered = " ".join(command)
        self.assertEqual(command[0], "systemd-run")
        self.assertIn("--no-block", command)
        self.assertIn("--collect", command)
        self.assertIn("--uid=bitcoin-trader", command)
        self.assertIn("--setenv=PYTHONPATH=src", command)
        self.assertIn("--property=Restart=no", command)
        self.assertIn("--property=KillMode=mixed", command)
        self.assertIn("--property=RuntimeMaxSec=2880s", command)
        self.assertIn("--property=TimeoutStopSec=55s", command)
        self.assertIn("--working-directory=/opt/bitcoin-trader", command)
        self.assertIn("aws-short-smoke-run-20260903-ab12cd34", rendered)
        self.assertNotIn("enable", rendered)
        self.assertNotIn("timer", rendered)
        self.assertNotIn("cron", rendered)

    def test_renderer_accepts_all_closed_production_durations_with_correct_prefixes(self) -> None:
        cases = [
            (2700, "bitcoin-trader-short-smoke-test-run.service"),
            (7200, "bitcoin-trader-120m-test-run.service"),
            (108000, "bitcoin-trader-30h-test-run.service"),
            (259200, "bitcoin-trader-72h-soak-test-run.service"),
        ]
        for duration, expected_unit in cases:
            with self.subTest(duration=duration):
                hard_ceiling = duration + 120
                runtime_max = hard_ceiling + 60
                command = render_systemd_run(
                    TransientLaunchConfig(
                        run_id="test-run",
                        workdir=Path("/opt/bitcoin-trader"),
                        supervisor_command=("python", "runner.py"),
                        collection_duration_seconds=duration,
                        finalization_timeout_seconds=120,
                        supervisor_hard_ceiling_seconds=hard_ceiling,
                        systemd_runtime_max_seconds=runtime_max,
                    )
                )
                self.assertIn(f"--unit={expected_unit}", command)
                self.assertIn(f"--property=RuntimeMaxSec={runtime_max}s", command)

    def test_renderer_rejects_unapproved_production_duration_and_unsafe_run_id(self) -> None:
        expected_duration_error = "production supervisor duration must be exactly 2700, 7200, 108000, or 259200 seconds"
        for duration in (2699, 5000, 108001, 3600):
            with self.subTest(duration=duration):
                with self.assertRaisesRegex(ValueError, expected_duration_error):
                    render_systemd_run(
                        TransientLaunchConfig(
                            run_id="safe-run",
                            workdir=Path("/opt/bitcoin-trader"),
                            supervisor_command=("python", "runner.py"),
                            collection_duration_seconds=duration,
                            finalization_timeout_seconds=120,
                            supervisor_hard_ceiling_seconds=duration + 120,
                            systemd_runtime_max_seconds=duration + 180,
                        )
                    )

        with self.assertRaisesRegex(ValueError, "run_id must be a safe identifier"):
            render_systemd_run(
                TransientLaunchConfig(
                    run_id="unsafe/run/id",
                    workdir=Path("/opt/bitcoin-trader"),
                    supervisor_command=("python", "runner.py"),
                    collection_duration_seconds=2700,
                    finalization_timeout_seconds=120,
                    supervisor_hard_ceiling_seconds=2820,
                    systemd_runtime_max_seconds=2880,
                )
            )

    def test_renderer_requires_systemd_deadline_beyond_supervisor_ceiling(self) -> None:
        with self.assertRaisesRegex(ValueError, "systemd runtime max"):
            render_systemd_run(
                TransientLaunchConfig(
                    run_id="aws-45m-test",
                    workdir=Path("/opt/bitcoin-trader"),
                    supervisor_command=("python", "runner.py"),
                    collection_duration_seconds=2700,
                    finalization_timeout_seconds=120,
                    supervisor_hard_ceiling_seconds=2820,
                    systemd_runtime_max_seconds=2820,
                )
            )

    def test_extract_supervisor_duration(self) -> None:
        self.assertEqual(
            _extract_supervisor_duration(["python", "run.py", "--collection-duration-seconds", "108000"]),
            108000,
        )
        self.assertEqual(
            _extract_supervisor_duration(["python", "run.py", "--collection-duration-seconds=108000"]),
            108000,
        )
        self.assertEqual(
            _extract_supervisor_duration(["python", "run.py", "--duration", "2700.0"]),
            2700,
        )
        self.assertEqual(
            _extract_supervisor_duration(["python", "run.py", "--duration=7200"]),
            7200,
        )
        self.assertIsNone(
            _extract_supervisor_duration(["python", "run.py", "--metrics-path", "/tmp/m.json"])
        )

        with self.assertRaisesRegex(ValueError, "missing value for supervisor command flag"):
            _extract_supervisor_duration(["python", "run.py", "--duration"])

        with self.assertRaisesRegex(ValueError, "must be integer"):
            _extract_supervisor_duration(["python", "run.py", "--duration", "108000.5"])

        with self.assertRaisesRegex(ValueError, "invalid supervisor command duration"):
            _extract_supervisor_duration(["python", "run.py", "--duration", "not-a-number"])

    def test_validate_cross_layer_duration(self) -> None:
        # Matching duration passes
        _validate_cross_layer_duration(
            108000,
            ["python", "run.py", "--collection-duration-seconds", "108000"],
        )
        # Missing duration in supervisor command is allowed (no mismatch)
        _validate_cross_layer_duration(
            2700,
            ["python", "run.py"],
        )
        # Mismatch raises ValueError
        with self.assertRaisesRegex(ValueError, "collection duration mismatch: launcher declared 2700s but supervisor command specifies 108000s"):
            _validate_cross_layer_duration(
                2700,
                ["python", "run.py", "--collection-duration-seconds", "108000"],
            )

    def test_launch_cli_propagates_108000_and_enforces_cross_layer_guard(self) -> None:
        # Case 1: 108000 rendered correctly without launch
        supervisor_cmd = json.dumps(["python", "run.py", "--collection-duration-seconds", "108000"])
        stdout_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf):
            ret = launch_transient_main([
                "--run-id", "aws-30h-run-20260912",
                "--workdir", "/opt/bitcoin-trader",
                "--supervisor-command-json", supervisor_cmd,
                "--collection-duration-seconds", "108000",
                "--supervisor-hard-ceiling-seconds", "108120",
                "--systemd-runtime-max-seconds", "108180",
            ])
        self.assertEqual(ret, 0)
        output = json.loads(stdout_buf.getvalue())
        self.assertIn("--unit=bitcoin-trader-30h-aws-30h-run-20260912.service", output)
        self.assertIn("--property=RuntimeMaxSec=108180s", output)

        # Case 2: Mismatch between default launcher (2700) and supervisor (108000)
        with self.assertRaisesRegex(ValueError, "collection duration mismatch"):
            launch_transient_main([
                "--run-id", "aws-30h-run-20260912",
                "--workdir", "/opt/bitcoin-trader",
                "--supervisor-command-json", supervisor_cmd,
                # omitting --collection-duration-seconds defaults to 2700
            ])


if __name__ == "__main__":
    unittest.main()
