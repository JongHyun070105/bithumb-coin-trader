from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from bithumb_coin_trader.bounded_supervisor import BoundedSupervisor, SupervisorConfig


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "lifecycle_child.py"
CLI = ROOT / "scripts" / "run_bounded_short_smoke.py"


class BoundedSupervisorTests(unittest.TestCase):
    def _paths(self, root: Path) -> dict[str, Path]:
        return {
            "metrics": root / "metrics.json",
            "lifecycle": root / "lifecycle.json",
            "events": root / "events.log",
            "result": root / "result.json",
            "log": root / "supervisor.log",
        }

    def _collector(
        self,
        paths: dict[str, Path],
        run_id: str,
        seconds: float = 0.15,
        *,
        finalize_seconds: float = 0.0,
        hang_finalization: bool = False,
        exit_code: int = 0,
    ) -> tuple[str, ...]:
        command = (
            sys.executable,
            str(FIXTURE),
            "collector",
            "--run-id",
            run_id,
            "--metrics",
            str(paths["metrics"]),
            "--lifecycle",
            str(paths["lifecycle"]),
            "--events",
            str(paths["events"]),
            "--sleep",
            str(seconds),
            "--finalize-sleep",
            str(finalize_seconds),
            "--exit-code",
            str(exit_code),
        )
        return command + (("--hang-finalization",) if hang_finalization else ())

    def _publisher(
        self,
        paths: dict[str, Path],
        run_id: str,
        exit_code: int = 0,
        *,
        sleep: float = 0.02,
    ) -> tuple[str, ...]:
        return (
            sys.executable,
            str(FIXTURE),
            "publisher",
            "--run-id",
            run_id,
            "--events",
            str(paths["events"]),
            "--sleep",
            str(sleep),
            "--exit-code",
            str(exit_code),
        )

    def _archive_scheduler(self, paths: dict[str, Path], run_id: str, exit_code: int = 0, sleep: float = 0.5) -> tuple[str, ...]:
        return (
            sys.executable,
            str(FIXTURE),
            "scheduler",
            "--run-id",
            run_id,
            "--events",
            str(paths["events"]),
            "--sleep",
            str(sleep),
            "--exit-code",
            str(exit_code),
        )

    def test_natural_exit_persists_flush_and_publisher_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            run_id = "aws-short-smoke-run-test-natural"
            config = SupervisorConfig(
                run_id=run_id,
                collection_duration_seconds=0.4,
                collector_command=self._collector(paths, run_id),
                # This test exercises supervisor-owned shutdown. Keep the
                # publisher alive longer than the 0.15s collector lifetime so
                # scheduler timing cannot select the natural-exit branch.
                publisher_command=self._publisher(paths, run_id, sleep=1.0),
                metrics_path=paths["metrics"],
                collector_lifecycle_path=paths["lifecycle"],
                result_path=paths["result"],
                log_path=paths["log"],
                poll_interval_seconds=0.01,
                publisher_interval_seconds=0.04,
                shutdown_grace_seconds=0.2,
            )
            self.assertEqual(BoundedSupervisor(config).run(), 0)
            result = json.loads(paths["result"].read_text(encoding="utf-8"))
            events = paths["events"].read_text(encoding="utf-8")
            self.assertEqual(result["overall_status"], "PASS")
            self.assertTrue(result["publisher_started"])
            self.assertIn(result["publisher_exit_code"], (0, -signal.SIGTERM))
            self.assertTrue(result["publisher_stopped_after_collector"])
            self.assertTrue(result["final_metrics_valid"])
            self.assertTrue(result["final_manifest_flush_observed"])
            self.assertIn("writer-drain", events)
            self.assertIn("final-metrics", events)
            self.assertIn("final-manifest", events)
            self.assertIn("publisher-start", events)

    def test_natural_publisher_exit_before_collector_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            run_id = "aws-short-smoke-run-test-publisher-natural-exit"
            config = SupervisorConfig(
                run_id=run_id,
                collection_duration_seconds=0.5,
                collector_command=self._collector(paths, run_id, seconds=0.5),
                publisher_command=self._publisher(paths, run_id, sleep=0.0),
                metrics_path=paths["metrics"],
                collector_lifecycle_path=paths["lifecycle"],
                result_path=paths["result"],
                log_path=paths["log"],
                poll_interval_seconds=0.005,
                publisher_interval_seconds=10.0,
                shutdown_grace_seconds=0.2,
            )

            self.assertEqual(BoundedSupervisor(config).run(), 0)
            result = json.loads(paths["result"].read_text(encoding="utf-8"))
            events = paths["events"].read_text(encoding="utf-8")
            self.assertEqual(result["overall_status"], "PASS")
            self.assertEqual(result["collector_exit_code"], 0)
            self.assertTrue(result["publisher_started"])
            self.assertEqual(result["publisher_exit_code"], 0)
            self.assertFalse(result["publisher_stopped_after_collector"])
            self.assertTrue(result["final_metrics_valid"])
            self.assertTrue(result["final_manifest_flush_observed"])
            self.assertIn("publisher-stop", events)

    def test_publisher_failure_is_visible_and_fails_overall_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            run_id = "aws-short-smoke-run-test-publisher-fail"
            config = SupervisorConfig(
                run_id=run_id,
                collection_duration_seconds=0.4,
                collector_command=self._collector(paths, run_id),
                publisher_command=self._publisher(paths, run_id, exit_code=7),
                metrics_path=paths["metrics"],
                collector_lifecycle_path=paths["lifecycle"],
                result_path=paths["result"],
                log_path=paths["log"],
                poll_interval_seconds=0.01,
                publisher_interval_seconds=0.04,
                shutdown_grace_seconds=0.2,
            )
            self.assertNotEqual(BoundedSupervisor(config).run(), 0)
            result = json.loads(paths["result"].read_text(encoding="utf-8"))
            self.assertEqual(result["publisher_exit_code"], 7)
            self.assertEqual(result["overall_status"], "FAIL")

    def test_required_duration_rejects_early_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            run_id = "aws-short-smoke-run-test-early"
            config = SupervisorConfig(
                run_id=run_id,
                collection_duration_seconds=0.5,
                collector_command=self._collector(paths, run_id, seconds=0.05),
                metrics_path=paths["metrics"],
                collector_lifecycle_path=paths["lifecycle"],
                result_path=paths["result"],
                log_path=paths["log"],
                poll_interval_seconds=0.01,
                shutdown_grace_seconds=0.2,
                require_full_duration=True,
            )
            self.assertNotEqual(BoundedSupervisor(config).run(), 0)
            result = json.loads(paths["result"].read_text(encoding="utf-8"))
            self.assertFalse(result["full_duration_satisfied"])
            self.assertEqual(result["overall_status"], "FAIL")

    def test_duration_expiry_allows_child_natural_flush_without_forced_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            run_id = "aws-short-smoke-run-test-deadline"
            config = SupervisorConfig(
                run_id=run_id,
                collection_duration_seconds=0.15,
                collector_command=self._collector(paths, run_id, seconds=0.15),
                metrics_path=paths["metrics"],
                collector_lifecycle_path=paths["lifecycle"],
                result_path=paths["result"],
                log_path=paths["log"],
                poll_interval_seconds=0.005,
                shutdown_grace_seconds=0.2,
                require_full_duration=True,
            )
            self.assertEqual(BoundedSupervisor(config).run(), 0)
            result = json.loads(paths["result"].read_text(encoding="utf-8"))
            self.assertTrue(result["full_duration_satisfied"])
            self.assertFalse(result["forced_timeout"])
            self.assertIsNone(result["received_signal"])

    def test_collection_deadline_allows_controlled_finalization_without_sigterm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            run_id = "remediation-normal-finalization"
            config = SupervisorConfig(
                run_id=run_id,
                collection_duration_seconds=0.10,
                finalization_timeout_seconds=0.30,
                hard_ceiling_seconds=0.40,
                collector_command=self._collector(
                    paths, run_id, seconds=0.10, finalize_seconds=0.08
                ),
                metrics_path=paths["metrics"],
                collector_lifecycle_path=paths["lifecycle"],
                result_path=paths["result"],
                log_path=paths["log"],
                poll_interval_seconds=0.005,
                shutdown_grace_seconds=0.05,
                require_full_duration=True,
            )

            self.assertEqual(BoundedSupervisor(config).run(), 0)
            result = json.loads(paths["result"].read_text(encoding="utf-8"))
            events = paths["events"].read_text(encoding="utf-8")
            self.assertEqual(result["overall_status"], "PASS")
            self.assertEqual(result["collector_exit_code"], 0)
            self.assertIsNone(result["received_signal"])
            self.assertFalse(result["forced_timeout"])
            self.assertTrue(result["full_duration_satisfied"])
            self.assertTrue(result["final_manifest_flush_observed"])
            self.assertIn("COLLECTING", events)
            self.assertIn("FINALIZING", events)
            self.assertIn("COMPLETE", events)

    def test_hung_finalization_hits_outer_hard_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            run_id = "remediation-hung-finalization"
            config = SupervisorConfig(
                run_id=run_id,
                collection_duration_seconds=0.05,
                finalization_timeout_seconds=0.08,
                hard_ceiling_seconds=0.13,
                collector_command=self._collector(
                    paths, run_id, seconds=0.05, hang_finalization=True
                ),
                metrics_path=paths["metrics"],
                collector_lifecycle_path=paths["lifecycle"],
                result_path=paths["result"],
                log_path=paths["log"],
                poll_interval_seconds=0.005,
                shutdown_grace_seconds=0.05,
                require_full_duration=True,
            )

            self.assertEqual(BoundedSupervisor(config).run(), 1)
            result = json.loads(paths["result"].read_text(encoding="utf-8"))
            self.assertEqual(result["overall_status"], "FAIL")
            self.assertTrue(result["forced_timeout"])
            self.assertIsNone(result["received_signal"])
            self.assertNotEqual(result["collector_exit_code"], 0)

    def test_early_nonzero_collector_failure_returns_before_outer_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            run_id = "remediation-early-failure"
            config = SupervisorConfig(
                run_id=run_id,
                collection_duration_seconds=0.50,
                finalization_timeout_seconds=0.20,
                hard_ceiling_seconds=0.70,
                collector_command=self._collector(
                    paths, run_id, seconds=0.03, exit_code=7
                ),
                metrics_path=paths["metrics"],
                collector_lifecycle_path=paths["lifecycle"],
                result_path=paths["result"],
                log_path=paths["log"],
                poll_interval_seconds=0.005,
                shutdown_grace_seconds=0.05,
                require_full_duration=True,
            )

            started = time.monotonic()
            self.assertEqual(BoundedSupervisor(config).run(), 1)
            elapsed = time.monotonic() - started
            result = json.loads(paths["result"].read_text(encoding="utf-8"))
            self.assertLess(elapsed, 0.30)
            self.assertEqual(result["collector_exit_code"], 7)
            self.assertEqual(result["overall_status"], "FAIL")
            self.assertFalse(result["forced_timeout"])

    def test_sigint_and_sigterm_reach_collector_and_are_durably_recorded(self) -> None:
        for sent_signal in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=sent_signal), tempfile.TemporaryDirectory() as tmp:
                paths = self._paths(Path(tmp))
                run_id = f"aws-short-smoke-run-test-{sent_signal.name.lower()}"
                command = [
                    sys.executable,
                    str(CLI),
                    "--run-id",
                    run_id,
                    "--collection-duration-seconds",
                    "5",
                    "--collector-command-json",
                    json.dumps(self._collector(paths, run_id, seconds=5)),
                    "--metrics-path",
                    str(paths["metrics"]),
                    "--collector-lifecycle-path",
                    str(paths["lifecycle"]),
                    "--result-path",
                    str(paths["result"]),
                    "--log-path",
                    str(paths["log"]),
                    "--poll-interval-seconds",
                    "0.01",
                    "--shutdown-grace-seconds",
                    "0.5",
                ]
                process = subprocess.Popen(command, cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
                deadline = time.monotonic() + 2
                while not paths["metrics"].exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(paths["metrics"].exists())
                os.kill(process.pid, sent_signal)
                process.wait(timeout=3)
                result = json.loads(paths["result"].read_text(encoding="utf-8"))
                events = paths["events"].read_text(encoding="utf-8")
                self.assertEqual(result["received_signal"], sent_signal.name)
                self.assertIn(sent_signal.name, events)
                self.assertTrue(result["final_manifest_flush_observed"])

    def test_detached_parent_stdio_does_not_own_supervisor_lifetime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            run_id = "aws-short-smoke-run-test-detached"
            command = [
                sys.executable,
                str(CLI),
                "--run-id",
                run_id,
                "--collection-duration-seconds",
                "0.5",
                "--collector-command-json",
                json.dumps(self._collector(paths, run_id, seconds=0.2)),
                "--metrics-path",
                str(paths["metrics"]),
                "--collector-lifecycle-path",
                str(paths["lifecycle"]),
                "--result-path",
                str(paths["result"]),
                "--log-path",
                str(paths["log"]),
            ]
            with open(os.devnull, "rb") as stdin, open(os.devnull, "ab") as output:
                process = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                    stdin=stdin,
                    stdout=output,
                    stderr=output,
                    start_new_session=True,
                )
            process.wait(timeout=3)
            result = json.loads(paths["result"].read_text(encoding="utf-8"))
            self.assertEqual(result["overall_status"], "PASS")
            self.assertEqual(result["received_signal"], None)

    def test_supervisor_with_archive_scheduler_natural_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            run_id = "aws-72h-soak-test-archive-sched"
            config = SupervisorConfig(
                run_id=run_id,
                collection_duration_seconds=0.4,
                collector_command=self._collector(paths, run_id),
                publisher_command=self._publisher(paths, run_id),
                archive_scheduler_command=self._archive_scheduler(paths, run_id, sleep=0.5),
                metrics_path=paths["metrics"],
                collector_lifecycle_path=paths["lifecycle"],
                result_path=paths["result"],
                log_path=paths["log"],
                poll_interval_seconds=0.01,
                publisher_interval_seconds=0.04,
                shutdown_grace_seconds=0.2,
            )
            self.assertEqual(BoundedSupervisor(config).run(), 0)
            result = json.loads(paths["result"].read_text(encoding="utf-8"))
            events = paths["events"].read_text(encoding="utf-8")
            self.assertEqual(result["overall_status"], "PASS")
            self.assertTrue(result["archive_scheduler_started"])
            self.assertEqual(result["archive_scheduler_exit_code"], 0)
            self.assertTrue(result["archive_scheduler_stopped_after_collector"])
            self.assertIn("scheduler-start", events)
            self.assertIn("scheduler-SIGTERM", events)

    def test_supervisor_archive_scheduler_failure_fails_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            run_id = "aws-72h-soak-test-sched-fail"
            config = SupervisorConfig(
                run_id=run_id,
                collection_duration_seconds=0.4,
                collector_command=self._collector(paths, run_id),
                archive_scheduler_command=self._archive_scheduler(paths, run_id, exit_code=7, sleep=0.05),
                metrics_path=paths["metrics"],
                collector_lifecycle_path=paths["lifecycle"],
                result_path=paths["result"],
                log_path=paths["log"],
                poll_interval_seconds=0.01,
                shutdown_grace_seconds=0.2,
            )
            self.assertEqual(BoundedSupervisor(config).run(), 1)
            result = json.loads(paths["result"].read_text(encoding="utf-8"))
            self.assertEqual(result["overall_status"], "FAIL")
            self.assertEqual(result["archive_scheduler_exit_code"], 7)

    def test_v3_schedule_config_validates(self) -> None:
        from bithumb_coin_trader.bounded_supervisor import V3ScheduleConfig
        with self.assertRaises(ValueError):
            V3ScheduleConfig(required_qualifying_full_hours=29, maximum_collection_window_seconds=111600, schedule_path=Path("/tmp/s.json"))
        with self.assertRaises(ValueError):
            V3ScheduleConfig(required_qualifying_full_hours=30, maximum_collection_window_seconds=108000, schedule_path=Path("/tmp/s.json"))
        # valid
        cfg = V3ScheduleConfig(required_qualifying_full_hours=30, maximum_collection_window_seconds=111600, schedule_path=Path("/tmp/s.json"))
        self.assertEqual(cfg.required_qualifying_full_hours, 30)

    def test_supervisor_config_rejects_both_modes(self) -> None:
        from bithumb_coin_trader.bounded_supervisor import V3ScheduleConfig
        with self.assertRaises(ValueError):
            SupervisorConfig(
                run_id="test-run",
                collection_duration_seconds=108000.0,
                v3_schedule=V3ScheduleConfig(30, 111600, Path("/tmp/s.json")),
                collector_command=("python", "-c", "pass"),
                metrics_path=Path("/tmp/metrics.json"),
                collector_lifecycle_path=Path("/tmp/lifecycle.json"),
                result_path=Path("/tmp/result.json"),
                log_path=Path("/tmp/log.txt"),
            )

    def test_supervisor_v3_schedule_execution(self) -> None:
        from bithumb_coin_trader.bounded_supervisor import V3ScheduleConfig
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = self._paths(tmp_path)
            schedule_path = tmp_path / "qualification_schedule.json"
            run_id = "aws-30h-run-v3-exec-test"
            config = SupervisorConfig(
                run_id=run_id,
                collector_command=self._collector(paths, run_id, seconds=0.05),
                metrics_path=paths["metrics"],
                collector_lifecycle_path=paths["lifecycle"],
                result_path=paths["result"],
                log_path=paths["log"],
                poll_interval_seconds=0.01,
                shutdown_grace_seconds=0.2,
                v3_schedule=V3ScheduleConfig(
                    required_qualifying_full_hours=30,
                    maximum_collection_window_seconds=111600,
                    schedule_path=schedule_path,
                ),
            )
            exit_code = BoundedSupervisor(config).run()
            self.assertEqual(exit_code, 0)
            self.assertTrue(schedule_path.exists())
            schedule_data = json.loads(schedule_path.read_text(encoding="utf-8"))
            self.assertEqual(schedule_data.get("schema_version"), 1)
            self.assertEqual(schedule_data.get("required_qualifying_full_hours"), 30)
            self.assertEqual(schedule_data.get("maximum_collection_window_seconds"), 111600)
            self.assertTrue(paths["result"].exists())
            result_data = json.loads(paths["result"].read_text(encoding="utf-8"))
            self.assertIn("deadline_recomputed", result_data)
            self.assertIs(result_data["deadline_recomputed"], False)
            self.assertEqual(result_data["overall_status"], "PASS")

    def test_run_bounded_short_smoke_partial_v3_args_rejected(self) -> None:
        from scripts.run_bounded_short_smoke import main as smoke_main
        cases = [
            ["--required-qualifying-full-hours", "30"],
            ["--maximum-collection-window-seconds", "111600"],
            ["--qualification-schedule-path", "/tmp/s.json"],
            ["--required-qualifying-full-hours", "30", "--maximum-collection-window-seconds", "111600"],
        ]
        for v3_part in cases:
            with self.subTest(v3_part=v3_part):
                with self.assertRaisesRegex(ValueError, "all V3 schedule arguments must be provided together"):
                    smoke_main([
                        "--run-id", "test-partial-v3",
                        "--collector-command-json", json.dumps(["python", "-c", "pass"]),
                        "--metrics-path", "/tmp/metrics.json",
                        "--collector-lifecycle-path", "/tmp/lifecycle.json",
                        "--result-path", "/tmp/result.json",
                        "--log-path", "/tmp/log.txt",
                        *v3_part,
                    ])
if __name__ == "__main__":
    unittest.main()
