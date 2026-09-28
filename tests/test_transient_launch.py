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
        expected_duration_error = "production supervisor duration must be 2700, 5400, 7200, 10800, 21600, 108000, 259200, or the sealed 120-second witness smoke"
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
        with self.assertRaisesRegex(ValueError, "production supervisor duration must be 2700, 5400, 7200, 10800, 21600, 108000, 259200, or the sealed 120-second witness smoke"):
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

    def test_exec_stop_post_binds_terminal_witness_identity_and_s3_upload(self) -> None:
        cfg = TransientLaunchConfig(
            run_id="aws-validation-observability-30h-run-20260926T135000Z-v3",
            workdir=Path("/opt/bitcoin-trader"),
            supervisor_command=("python", "run.py"),
            maximum_collection_window_seconds=111600,
            finalization_timeout_seconds=120,
            supervisor_hard_ceiling_seconds=111720,
            systemd_runtime_max_seconds=111800,
            exec_stop_post_script="/opt/bitcoin-trader/scripts/terminal_witness.py",
            data_dir=Path("/var/lib/bitcoin-trader/30h-validation/exact-epoch"),
            exec_stop_post_epoch="aws-validation-observability-30h-20260926-20260926T135000Z-v3",
            exec_stop_post_s3_bucket="receipt-bucket",
            exec_stop_post_s3_prefix="market-data/temporary/aws-validation-observability-30h/exact-epoch",
            exec_stop_post_s3_region="ap-northeast-2",
            exec_stop_post_allow_s3_write=True,
        )

        command = render_systemd_run(cfg)
        exec_stop_post = next(part for part in command if "ExecStopPost" in part)

        self.assertIn("--epoch=aws-validation-observability-30h-20260926-20260926T135000Z-v3", exec_stop_post)
        self.assertNotIn("--epoch=bitcoin-trader-30h", exec_stop_post)
        self.assertIn("--run-id=aws-validation-observability-30h-run-20260926T135000Z-v3", exec_stop_post)
        self.assertIn("--s3-bucket=receipt-bucket", exec_stop_post)
        self.assertIn("--s3-prefix=market-data/temporary/aws-validation-observability-30h/exact-epoch", exec_stop_post)
        self.assertIn("--s3-region=ap-northeast-2", exec_stop_post)
        self.assertIn("--allow-s3-write", exec_stop_post)

    def test_exec_stop_post_requires_an_exact_epoch(self) -> None:
        cfg = TransientLaunchConfig(
            run_id="test-run",
            workdir=Path("/opt/bitcoin-trader"),
            supervisor_command=("python", "run.py"),
            collection_duration_seconds=2700,
            finalization_timeout_seconds=120,
            supervisor_hard_ceiling_seconds=2820,
            systemd_runtime_max_seconds=2880,
            exec_stop_post_script="/opt/bitcoin-trader/scripts/terminal_witness.py",
        )

        with self.assertRaisesRegex(ValueError, "exact epoch"):
            render_systemd_run(cfg)

    def test_launch_cli_threads_terminal_witness_binding_into_systemd_unit(self) -> None:
        args = [
            "--run-id", "aws-validation-observability-30h-run-20260926T135000Z-v3",
            "--workdir", "/opt/bitcoin-trader",
            "--supervisor-command-json", json.dumps(["python", "run.py", "--required-qualifying-full-hours", "30"]),
            "--required-qualifying-full-hours", "30",
            "--maximum-collection-window-seconds", "111600",
            "--qualification-schedule-path", "/var/lib/bitcoin-trader/schedule.json",
            "--supervisor-hard-ceiling-seconds", "111720",
            "--systemd-runtime-max-seconds", "111800",
            "--exec-stop-post-script", "/opt/bitcoin-trader/scripts/terminal_witness.py",
            "--data-dir", "/var/lib/bitcoin-trader/30h-validation/exact-epoch",
            "--exec-stop-post-epoch", "aws-validation-observability-30h-20260926-20260926T135000Z-v3",
            "--exec-stop-post-s3-bucket", "receipt-bucket",
            "--exec-stop-post-s3-prefix", "market-data/temporary/aws-validation-observability-30h/exact-epoch",
            "--exec-stop-post-s3-region", "ap-northeast-2",
            "--exec-stop-post-allow-s3-write",
        ]

        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            result = launch_transient_main(args)

        self.assertEqual(result, 0)
        rendered = " ".join(json.loads(stdout.getvalue()))
        self.assertIn("--epoch=aws-validation-observability-30h-20260926-20260926T135000Z-v3", rendered)
        self.assertIn("--s3-bucket=receipt-bucket", rendered)
        self.assertIn("--s3-prefix=market-data/temporary/aws-validation-observability-30h/exact-epoch", rendered)
        self.assertIn("--s3-region=ap-northeast-2", rendered)
        self.assertIn("--allow-s3-write", rendered)

    def test_exec_stop_post_rejects_missing_upload_contract_fields(self) -> None:
        base = dict(
            run_id="smoke-run",
            workdir=Path("/opt/bitcoin-trader"),
            supervisor_command=("python", "run.py"),
            collection_duration_seconds=2700,
            finalization_timeout_seconds=120,
            supervisor_hard_ceiling_seconds=2820,
            systemd_runtime_max_seconds=2880,
            exec_stop_post_script="/opt/bitcoin-trader/scripts/terminal_witness.py",
            exec_stop_post_epoch="aws-validation-smoke-epoch",
            exec_stop_post_s3_bucket="receipt-bucket",
            exec_stop_post_s3_prefix="market-data/temporary/aws-validation-smoke-epoch",
            exec_stop_post_s3_region="ap-northeast-2",
            exec_stop_post_allow_s3_write=True,
        )
        cases = [
            ({"exec_stop_post_allow_s3_write": False}, "explicitly enabled"),
            ({"exec_stop_post_s3_bucket": None}, "valid bucket"),
            ({"exec_stop_post_s3_prefix": None}, "valid prefix"),
            ({"exec_stop_post_s3_region": None}, "explicit AWS region"),
            ({"exec_stop_post_epoch": None}, "exact epoch"),
            ({"exec_stop_post_s3_prefix": "market-data/temporary/../wrong"}, "dot path segments"),
        ]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, expected):
                    render_systemd_run(TransientLaunchConfig(**{**base, **changes}))

    def test_exec_stop_post_accepts_slashes_and_long_identity_but_rejects_shell_syntax(self) -> None:
        long_epoch = "aws-validation-witness-e2e-smoke-20260928T043700Z-v1-" + "x" * 60
        base = dict(
            run_id="aws-validation-witness-e2e-smoke-run-20260928T043700Z-v1",
            workdir=Path("/opt/bitcoin-trader"),
            supervisor_command=("python", "run.py"),
            collection_duration_seconds=120,
            finalization_timeout_seconds=120,
            supervisor_hard_ceiling_seconds=240,
            systemd_runtime_max_seconds=300,
            exec_stop_post_script="/opt/bitcoin-trader/scripts/terminal_witness.py",
            data_dir=Path("/var/lib/bitcoin-trader/witness-e2e-smoke"),
            exec_stop_post_epoch=long_epoch,
            exec_stop_post_s3_bucket="receipt-bucket",
            exec_stop_post_s3_prefix=f"market-data/temporary/{long_epoch}",
            exec_stop_post_s3_region="ap-northeast-2",
            exec_stop_post_allow_s3_write=True,
        )
        command = render_systemd_run(TransientLaunchConfig(**base))
        property_value = next(token for token in command if token.startswith("--property=ExecStopPost="))
        self.assertIn(f"--epoch={long_epoch}", property_value)
        self.assertIn(f"--s3-prefix=market-data/temporary/{long_epoch}", property_value)
        self.assertIn("--s3-region=ap-northeast-2", property_value)
        self.assertIn("--unit=bitcoin-trader-witness-e2e-smoke-aws-validation-witness-e2e-smoke-run-20260928T043700Z-v1.service", command)

        for field, value in (
            ("exec_stop_post_epoch", "unsafe;$(touch /tmp/no)"),
            ("exec_stop_post_s3_prefix", 'market-data/temporary/";touch /tmp/no'),
            ("exec_stop_post_script", "/opt/bad path/witness.py"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    render_systemd_run(TransientLaunchConfig(**{**base, field: value}))

    def test_120_second_duration_is_reserved_for_bound_witness_smoke_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "sealed 120-second witness smoke"):
            render_systemd_run(
                TransientLaunchConfig(
                    run_id="ordinary-run",
                    workdir=Path("/opt/bitcoin-trader"),
                    supervisor_command=("python", "run.py"),
                    collection_duration_seconds=120,
                    finalization_timeout_seconds=120,
                    supervisor_hard_ceiling_seconds=240,
                    systemd_runtime_max_seconds=300,
                )
            )


if __name__ == "__main__":
    unittest.main()
