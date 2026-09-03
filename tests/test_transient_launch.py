from __future__ import annotations

from pathlib import Path
import unittest

from bithumb_coin_trader.bounded_supervisor import TransientLaunchConfig, render_systemd_run


class TransientLaunchTests(unittest.TestCase):
    def test_renderer_is_detached_finite_non_restarting_and_run_scoped(self) -> None:
        command = render_systemd_run(
            TransientLaunchConfig(
                run_id="aws-short-smoke-run-20260903-ab12cd34",
                workdir=Path("/opt/bitcoin-trader"),
                supervisor_command=("/opt/bitcoin-trader/.venv/bin/python", "scripts/run_bounded_short_smoke.py"),
                supervisor_duration_seconds=2700,
                hard_ceiling_seconds=2760,
            )
        )
        rendered = " ".join(command)
        self.assertEqual(command[0], "systemd-run")
        self.assertIn("--no-block", command)
        self.assertIn("--collect", command)
        self.assertIn("--uid=bitcoin-trader", command)
        self.assertIn("--property=Restart=no", command)
        self.assertIn("--property=KillMode=mixed", command)
        self.assertIn("--property=RuntimeMaxSec=2760s", command)
        self.assertIn("--property=TimeoutStopSec=55s", command)
        self.assertIn("--working-directory=/opt/bitcoin-trader", command)
        self.assertIn("aws-short-smoke-run-20260903-ab12cd34", rendered)
        self.assertNotIn("enable", rendered)
        self.assertNotIn("timer", rendered)
        self.assertNotIn("cron", rendered)

    def test_renderer_rejects_non_2700_production_duration_and_unsafe_run_id(self) -> None:
        for run_id, duration in (("unsafe/run", 2700), ("safe-run", 2699)):
            with self.subTest(run_id=run_id, duration=duration), self.assertRaises(ValueError):
                render_systemd_run(
                    TransientLaunchConfig(
                        run_id=run_id,
                        workdir=Path("/opt/bitcoin-trader"),
                        supervisor_command=("python", "runner.py"),
                        supervisor_duration_seconds=duration,
                        hard_ceiling_seconds=2760,
                    )
                )


if __name__ == "__main__":
    unittest.main()
