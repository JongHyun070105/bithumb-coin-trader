from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_cross_market_collector.py"
SPEC = importlib.util.spec_from_file_location("run_cross_market_collector", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ShortSmokeRuntimeConfigTests(unittest.TestCase):
    def test_canonical_fingerprint_ignores_json_formatting(self) -> None:
        payload = {"schema_version": 1, "nested": {"b": 2, "a": 1}}
        expected = MODULE.canonical_config_fingerprint(payload)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(payload, indent=4), encoding="utf-8")
            self.assertEqual(MODULE._load_runtime_config(path, expected), payload)

    def test_fingerprint_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
                MODULE._load_runtime_config(path, "0" * 64)

    def test_epoch_template_rejects_additional_placeholders(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported"):
            MODULE._render_epoch_template(
                "/var/lib/{collector_epoch}/{run_id}",
                "epoch-a",
                "raw_root_template",
            )

    def test_lifecycle_status_is_atomic_private_and_run_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run" / "collector-lifecycle.json"
            MODULE._write_lifecycle_status(
                path,
                run_id="aws-short-smoke-run-test",
                phase="COMPLETE",
                final_manifest_flush_observed=True,
                manifest_count=4,
                error_type=None,
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["collector_run_id"], "aws-short-smoke-run-test")
            self.assertEqual(payload["phase"], "COMPLETE")
            self.assertEqual(payload["schema_version"], 2)
            self.assertTrue(payload["final_manifest_flush_observed"])
            self.assertEqual(payload["manifest_count"], 4)
            self.assertEqual(payload["historical_raw_files_opened"], 0)
            self.assertEqual(payload["historical_raw_bytes_read"], 0)
            self.assertEqual(payload["current_raw_files_opened"], 0)
            self.assertEqual(payload["current_raw_bytes_read"], 0)
            self.assertEqual(payload["reused_manifest_count"], 0)
            self.assertEqual(payload["generated_manifest_count"], 0)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_lifecycle_status_persists_finalization_counters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run" / "collector-lifecycle.json"
            MODULE._write_lifecycle_status(
                path,
                run_id="aws-short-smoke-run-test",
                phase="COMPLETE",
                final_manifest_flush_observed=True,
                manifest_count=10,
                error_type=None,
                historical_raw_files_opened=0,
                historical_raw_bytes_read=0,
                current_raw_files_opened=3,
                current_raw_bytes_read=15000,
                reused_manifest_count=7,
                generated_manifest_count=3,
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["manifest_count"], 10)
            self.assertEqual(payload["historical_raw_files_opened"], 0)
            self.assertEqual(payload["historical_raw_bytes_read"], 0)
            self.assertEqual(payload["current_raw_files_opened"], 3)
            self.assertEqual(payload["current_raw_bytes_read"], 15000)
            self.assertEqual(payload["reused_manifest_count"], 7)
            self.assertEqual(payload["generated_manifest_count"], 3)

    def test_lifecycle_status_rejects_unknown_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "lifecycle phase"):
            MODULE._write_lifecycle_status(
                Path(tmp) / "lifecycle.json",
                run_id="test-run",
                phase="DONE",
                final_manifest_flush_observed=False,
                manifest_count=0,
                error_type=None,
            )

    def test_sealed_20260904_runtime_config_loads_with_canonical_fingerprint_and_transient_mode(self) -> None:
        seal_path = Path(__file__).resolve().parents[1] / "infra" / "aws" / "seals" / "aws-short-smoke-20260904.runtime.json"
        payload = json.loads(seal_path.read_text(encoding="utf-8"))
        fingerprint = MODULE.canonical_config_fingerprint(payload)
        self.assertEqual(fingerprint, "48e5996f86567dfa41ed515de0e96fdb3230001fbc0ac2e0eb5453dad81422a0")
        loaded = MODULE._load_runtime_config(seal_path, fingerprint)
        self.assertEqual(loaded["execution"]["launch_mode"], "bounded-transient-systemd")
        self.assertFalse(loaded["execution"]["systemd_enable"])
        self.assertFalse(loaded["execution"]["collector_autostart"])

    def test_run_cross_market_collector_v3_validation(self) -> None:
        from bithumb_coin_trader.qualification_schedule import build_qualification_schedule, parse_utc, save_schedule
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sched_path = tmp_path / "schedule.json"
            schedule = build_qualification_schedule(parse_utc("2026-09-14T12:00:00Z"), 100.0, 30, 111600)
            save_schedule(schedule, sched_path)

            base_args = [
                "--config-file", str(tmp_path / "config.json"),
                "--storage-base-dir", str(tmp_path / "raw"),
                "--environment-id", "test-env",
                "--collector-epoch", "epoch-1",
                "--run-id", "run-1",
                "--config-fingerprint", "fp",
                "--runtime-commit", "abc",
                "--qualification-schedule-path", str(sched_path),
            ]

            # Wrong hours
            with self.assertRaisesRegex(ValueError, "required_qualifying_full_hours must be 30 and match schedule"):
                MODULE.main([*base_args, "--required-qualifying-full-hours", "29"])

            # Wrong window
            with self.assertRaisesRegex(ValueError, "maximum_collection_window_seconds must be 111600 and match schedule"):
                MODULE.main([*base_args, "--maximum-collection-window-seconds", "108000"])

            # Duration conflict
            with self.assertRaisesRegex(ValueError, "cannot specify both duration and V3 schedule"):
                MODULE.main([*base_args, "--duration", "100"])

            # V3 args without schedule path
            with self.assertRaisesRegex(ValueError, "qualification_schedule_path must be provided when V3 schedule arguments are used"):
                MODULE.main([
                    "--config-file", str(tmp_path / "config.json"),
                    "--storage-base-dir", str(tmp_path / "raw"),
                    "--environment-id", "test-env",
                    "--collector-epoch", "epoch-1",
                    "--run-id", "run-1",
                    "--config-fingerprint", "fp",
                    "--runtime-commit", "abc",
                    "--required-qualifying-full-hours", "30",
                ])


if __name__ == "__main__":
    unittest.main()
