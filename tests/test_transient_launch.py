from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

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
            (5400, "bitcoin-trader-90m-test-run.service"),
            (7200, "bitcoin-trader-120m-test-run.service"),
            (10800, "bitcoin-trader-3h-test-run.service"),
            (21600, "bitcoin-trader-6h-test-run.service"),
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
        expected_duration_error = "production supervisor duration must be exactly 2700, 5400, 7200, 10800, 21600, 108000, or 259200 seconds"
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

    def test_extract_supervisor_duration_valid(self) -> None:
        for dur in (2700, 7200, 108000, 259200):
            with self.subTest(duration=dur, form="space"):
                self.assertEqual(
                    _extract_supervisor_duration(["python", "run.py", "--collection-duration-seconds", str(dur)]),
                    dur,
                )
            with self.subTest(duration=dur, form="equals"):
                self.assertEqual(
                    _extract_supervisor_duration(["python", "run.py", f"--collection-duration-seconds={dur}"]),
                    dur,
                )
        # Integral float representation
        self.assertEqual(
            _extract_supervisor_duration(["python", "run.py", "--collection-duration-seconds", "108000.0"]),
            108000,
        )

    def test_extract_supervisor_duration_fails_closed(self) -> None:
        # Missing duration flag (0 occurrences)
        with self.assertRaisesRegex(ValueError, "supervisor command must declare exactly one collection duration"):
            _extract_supervisor_duration(["python", "run_bounded_short_smoke.py"])

        # Generic --duration is NOT accepted as supervisor duration
        with self.assertRaisesRegex(ValueError, "supervisor command must declare exactly one collection duration"):
            _extract_supervisor_duration(["python", "run_bounded_short_smoke.py", "--duration", "108000"])

        # Flag without value
        with self.assertRaisesRegex(ValueError, "missing value for supervisor command flag"):
            _extract_supervisor_duration(["python", "run.py", "--collection-duration-seconds"])

        # Flag with empty value in equals form
        with self.assertRaisesRegex(ValueError, "missing value for supervisor command flag"):
            _extract_supervisor_duration(["python", "run.py", "--collection-duration-seconds="])

        # Duplicate identical durations (108000 + 108000)
        with self.assertRaisesRegex(ValueError, "duplicate supervisor collection duration"):
            _extract_supervisor_duration([
                "python", "run.py",
                "--collection-duration-seconds", "108000",
                "--collection-duration-seconds", "108000",
            ])

        # Duplicate conflicting durations (108000 + 2700)
        with self.assertRaisesRegex(ValueError, "duplicate supervisor collection duration"):
            _extract_supervisor_duration([
                "python", "run.py",
                "--collection-duration-seconds", "108000",
                "--collection-duration-seconds", "2700",
            ])

        # Duplicate mixed forms (= and space)
        with self.assertRaisesRegex(ValueError, "duplicate supervisor collection duration"):
            _extract_supervisor_duration([
                "python", "run.py",
                "--collection-duration-seconds=108000",
                "--collection-duration-seconds", "108000",
            ])

        # Malformed duration
        with self.assertRaisesRegex(ValueError, "invalid supervisor command duration"):
            _extract_supervisor_duration(["python", "run.py", "--collection-duration-seconds", "not-a-number"])

        # Fractional duration
        with self.assertRaisesRegex(ValueError, "supervisor command duration must be integer"):
            _extract_supervisor_duration(["python", "run.py", "--collection-duration-seconds", "108000.5"])

    def test_validate_cross_layer_duration(self) -> None:
        # Exact bindings pass
        for dur in (2700, 7200, 108000, 259200):
            _validate_cross_layer_duration(
                dur,
                ["python", "run.py", "--collection-duration-seconds", str(dur)],
            )
            _validate_cross_layer_duration(
                dur,
                ["python", "run.py", f"--collection-duration-seconds={dur}"],
            )

        # Missing duration in supervisor command fails closed
        with self.assertRaisesRegex(ValueError, "supervisor command must declare exactly one collection duration"):
            _validate_cross_layer_duration(2700, ["python", "run.py"])

        # Mismatch: launcher 2700 vs supervisor 108000
        with self.assertRaisesRegex(ValueError, "collection duration mismatch: launcher declared 2700s but supervisor command specifies 108000s"):
            _validate_cross_layer_duration(
                2700,
                ["python", "run.py", "--collection-duration-seconds", "108000"],
            )

        # Mismatch: launcher 108000 vs supervisor 2700
        with self.assertRaisesRegex(ValueError, "collection duration mismatch: launcher declared 108000s but supervisor command specifies 2700s"):
            _validate_cross_layer_duration(
                108000,
                ["python", "run.py", "--collection-duration-seconds", "2700"],
            )

    def test_launch_cli_dangerous_duplicate_regression(self) -> None:
        # Explicit dangerous regression case: 108000 followed by 2700
        dangerous_cmd = json.dumps([
            "python",
            "run_bounded_short_smoke.py",
            "--collection-duration-seconds",
            "108000",
            "--collection-duration-seconds",
            "2700",
        ])
        with self.assertRaisesRegex(ValueError, "duplicate supervisor collection duration"):
            launch_transient_main([
                "--run-id", "aws-30h-run-20260912",
                "--workdir", "/opt/bitcoin-trader",
                "--supervisor-command-json", dangerous_cmd,
                "--collection-duration-seconds", "108000",
            ])

    def test_launch_cli_propagates_108000_and_enforces_cross_layer_guard(self) -> None:
        # Valid 108000 rendered correctly without launch
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

        # Mismatch between default launcher (2700) and supervisor (108000)
        with self.assertRaisesRegex(ValueError, "collection duration mismatch"):
            launch_transient_main([
                "--run-id", "aws-30h-run-20260912",
                "--workdir", "/opt/bitcoin-trader",
                "--supervisor-command-json", supervisor_cmd,
                # omitting --collection-duration-seconds defaults to 2700
            ])

        # Missing duration in supervisor command fails closed
        no_dur_cmd = json.dumps(["python", "run.py"])
        with self.assertRaisesRegex(ValueError, "supervisor command must declare exactly one collection duration"):
            launch_transient_main([
                "--run-id", "aws-30h-run-20260912",
                "--workdir", "/opt/bitcoin-trader",
                "--supervisor-command-json", no_dur_cmd,
            ])

        # Arbitrary unapproved launcher duration fails closed
        unapproved_cmd = json.dumps(["python", "run.py", "--collection-duration-seconds", "5000"])
        with self.assertRaisesRegex(ValueError, "production supervisor duration must be exactly 2700, 5400, 7200, 10800, 21600, 108000, or 259200 seconds"):
            launch_transient_main([
                "--run-id", "aws-30h-run-20260912",
                "--workdir", "/opt/bitcoin-trader",
                "--supervisor-command-json", unapproved_cmd,
                "--collection-duration-seconds", "5000",
                "--supervisor-hard-ceiling-seconds", "5120",
                "--systemd-runtime-max-seconds", "5180",
            ])

    def test_render_only_mode_performs_no_launch(self) -> None:
        supervisor_cmd = json.dumps(["python", "run.py", "--collection-duration-seconds", "2700"])
        with patch("subprocess.run") as mock_run, patch("sys.stdout", io.StringIO()):
            # Without --launch: subprocess.run must NOT be called
            ret = launch_transient_main([
                "--run-id", "aws-smoke-test",
                "--workdir", "/opt/bitcoin-trader",
                "--supervisor-command-json", supervisor_cmd,
                "--collection-duration-seconds", "2700",
            ])
            self.assertEqual(ret, 0)
            mock_run.assert_not_called()

            # With --launch: subprocess.run MUST be called
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_run.return_value = mock_proc
            ret_launch = launch_transient_main([
                "--run-id", "aws-smoke-test",
                "--workdir", "/opt/bitcoin-trader",
                "--supervisor-command-json", supervisor_cmd,
                "--collection-duration-seconds", "2700",
                "--launch",
            ])
            self.assertEqual(ret_launch, 0)
            mock_run.assert_called_once()

    def test_launch_cli_rejects_v3_with_collection_duration(self) -> None:
        supervisor_cmd = json.dumps(["python", "run.py", "--required-qualifying-full-hours", "30"])
        with self.assertRaisesRegex(ValueError, "cannot specify both collection_duration_seconds and V3 schedule"):
            launch_transient_main([
                "--run-id", "aws-smoke-test",
                "--workdir", "/opt/bitcoin-trader",
                "--supervisor-command-json", supervisor_cmd,
                "--collection-duration-seconds", "108000",
                "--required-qualifying-full-hours", "30",
                "--maximum-collection-window-seconds", "111600",
                "--qualification-schedule-path", "/tmp/s.json",
            ])

    def test_launch_cli_accepts_v3_without_collection_duration(self) -> None:
        supervisor_cmd = json.dumps(["python", "run.py", "--required-qualifying-full-hours", "30"])
        with patch("sys.stdout", io.StringIO()):
            ret = launch_transient_main([
                "--run-id", "aws-smoke-test",
                "--workdir", "/opt/bitcoin-trader",
                "--supervisor-command-json", supervisor_cmd,
                "--required-qualifying-full-hours", "30",
                "--maximum-collection-window-seconds", "111600",
                "--qualification-schedule-path", "/tmp/s.json",
                "--supervisor-hard-ceiling-seconds", "112000",
                "--systemd-runtime-max-seconds", "113000",
            ])
        self.assertEqual(ret, 0)

    def test_launch_cli_rejects_v3_without_v3_supervisor_args(self) -> None:
        supervisor_cmd = json.dumps(["python", "run.py"])
        with self.assertRaisesRegex(ValueError, "V3 supervisor command must contain --qualification-schedule-path or V3 arguments"):
            launch_transient_main([
                "--run-id", "aws-smoke-test",
                "--workdir", "/opt/bitcoin-trader",
                "--supervisor-command-json", supervisor_cmd,
                "--required-qualifying-full-hours", "30",
                "--maximum-collection-window-seconds", "111600",
                "--qualification-schedule-path", "/tmp/s.json",
                "--supervisor-hard-ceiling-seconds", "112000",
                "--systemd-runtime-max-seconds", "113000",
            ])

    def test_launch_cli_rejects_v3_with_supervisor_command_collection_duration(self) -> None:
        supervisor_cmd = json.dumps(["python", "run.py", "--collection-duration-seconds", "108000", "--required-qualifying-full-hours", "30"])
        with self.assertRaisesRegex(ValueError, "V3 supervisor command must not include --collection-duration-seconds"):
            launch_transient_main([
                "--run-id", "aws-smoke-test",
                "--workdir", "/opt/bitcoin-trader",
                "--supervisor-command-json", supervisor_cmd,
                "--required-qualifying-full-hours", "30",
                "--maximum-collection-window-seconds", "111600",
                "--qualification-schedule-path", "/tmp/s.json",
                "--supervisor-hard-ceiling-seconds", "112000",
                "--systemd-runtime-max-seconds", "113000",
            ])

    def test_renderer_v3_maximum_collection_window(self) -> None:
        cfg = TransientLaunchConfig(
            run_id="aws-v3-test",
            workdir=Path("/opt/bitcoin-trader"),
            supervisor_command=("python", "run.py"),
            maximum_collection_window_seconds=111600,
            finalization_timeout_seconds=120,
            supervisor_hard_ceiling_seconds=111720,
            systemd_runtime_max_seconds=111800,
        )
        command = render_systemd_run(cfg)
        self.assertIn("--unit=bitcoin-trader-30h-aws-v3-test.service", command)

        # Rejects window != 111600
        with self.assertRaisesRegex(ValueError, "V3 maximum_collection_window_seconds must be 111600"):
            render_systemd_run(
                TransientLaunchConfig(
                    run_id="aws-v3-test",
                    workdir=Path("/opt/bitcoin-trader"),
                    supervisor_command=("python", "run.py"),
                    maximum_collection_window_seconds=108000,
                    finalization_timeout_seconds=120,
                    supervisor_hard_ceiling_seconds=111720,
                    systemd_runtime_max_seconds=111800,
                )
            )

        # Rejects hard ceiling < max_window + finalization
        with self.assertRaisesRegex(ValueError, "supervisor hard ceiling must cover maximum collection window plus finalization"):
            render_systemd_run(
                TransientLaunchConfig(
                    run_id="aws-v3-test",
                    workdir=Path("/opt/bitcoin-trader"),
                    supervisor_command=("python", "run.py"),
                    maximum_collection_window_seconds=111600,
                    finalization_timeout_seconds=120,
                    supervisor_hard_ceiling_seconds=111600,  # less than 111600 + 120
                    systemd_runtime_max_seconds=111800,
                )
            )


if __name__ == "__main__":
    unittest.main()
