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

    def _collector(self, paths: dict[str, Path], run_id: str, seconds: float = 0.15) -> tuple[str, ...]:
        return (
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
        )

    def _publisher(self, paths: dict[str, Path], run_id: str, exit_code: int = 0) -> tuple[str, ...]:
        return (
            sys.executable,
            str(FIXTURE),
            "publisher",
            "--run-id",
            run_id,
            "--events",
            str(paths["events"]),
            "--sleep",
            "0.02",
            "--exit-code",
            str(exit_code),
        )

    def test_natural_exit_persists_flush_and_publisher_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            run_id = "aws-short-smoke-run-test-natural"
            config = SupervisorConfig(
                run_id=run_id,
                duration_seconds=0.4,
                collector_command=self._collector(paths, run_id),
                publisher_command=self._publisher(paths, run_id),
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

    def test_publisher_failure_is_visible_and_fails_overall_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._paths(Path(tmp))
            run_id = "aws-short-smoke-run-test-publisher-fail"
            config = SupervisorConfig(
                run_id=run_id,
                duration_seconds=0.4,
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
                duration_seconds=0.5,
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
                duration_seconds=0.15,
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
                    "--duration-seconds",
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
                "--duration-seconds",
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


if __name__ == "__main__":
    unittest.main()
