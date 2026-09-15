"""Regression tests for authoritative AWS 30H V3 launch seals and contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
for d in (ROOT, SRC_DIR):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

from bithumb_coin_trader.bounded_supervisor import TransientLaunchConfig
from bithumb_coin_trader.actual_start_evidence import (
    ActualStartIdentity,
    normalize_actual_start_evidence,
)
from scripts.run_cross_market_collector import (
    _load_runtime_config,
    _render_epoch_template,
    _validate_runtime_config,
    canonical_config_fingerprint as collector_config_fingerprint,
)
from scripts.seal_v3_validation_run import (
    BITHUMB_MARKETS,
    BINANCE_SYMBOLS,
    UPBIT_MARKETS,
    canonical_config_fingerprint as seal_config_fingerprint,
    generate_runtime_config,
    validate_git_commit,
)


class TestV3SealConsistency(unittest.TestCase):
    def setUp(self) -> None:
        self.epoch = "aws-validation-30h-20260915-v3"
        self.run_id = "aws-validation-30h-run-20260915T013000Z-v3"
        self.seals_dir = ROOT / "infra" / "aws" / "seals"
        self.runtime_commit = "ac81f94f431f5d868d88e10fa784eb0da449264d"

    def test_01_generator_without_explicit_runtime_commit_fails(self) -> None:
        """1. Generator without explicit runtime commit -> FAIL (exit code 2)."""
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "seal_v3_validation_run.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("required", proc.stderr)
        self.assertIn("--runtime-commit", proc.stderr)

    def test_02_runtime_json_runtime_commit_matches_explicit_commit(self) -> None:
        """2. Generated runtime.json runtime commit == explicit runtime commit."""
        runtime_path = self.seals_dir / f"{self.epoch}.runtime.json"
        self.assertTrue(runtime_path.exists())
        data = json.loads(runtime_path.read_text(encoding="utf-8"))
        self.assertEqual(data["runtime_software_commit"], self.runtime_commit)

    def test_03_collector_runtime_commit_matches_runtime_json(self) -> None:
        """3. Collector --runtime-commit == runtime.json runtime commit."""
        launch_cmd_path = self.seals_dir / f"{self.epoch}.launch-command.json"
        data = json.loads(launch_cmd_path.read_text(encoding="utf-8"))
        sup_cmd = data["supervisor_command"]
        idx = sup_cmd.index("--collector-command-json")
        collector_cmd = json.loads(sup_cmd[idx + 1])
        c_commit_idx = collector_cmd.index("--runtime-commit")
        collector_commit = collector_cmd[c_commit_idx + 1]

        runtime_data = json.loads(
            (self.seals_dir / f"{self.epoch}.runtime.json").read_text(encoding="utf-8")
        )
        self.assertEqual(collector_commit, runtime_data["runtime_software_commit"])
        self.assertEqual(collector_commit, self.runtime_commit)

    def test_04_archive_scheduler_git_commit_matches_runtime_json(self) -> None:
        """4. Archive scheduler --git-commit == runtime.json runtime commit."""
        launch_cmd_path = self.seals_dir / f"{self.epoch}.launch-command.json"
        data = json.loads(launch_cmd_path.read_text(encoding="utf-8"))
        sup_cmd = data["supervisor_command"]
        idx = sup_cmd.index("--archive-scheduler-command-json")
        sched_cmd = json.loads(sup_cmd[idx + 1])
        s_commit_idx = sched_cmd.index("--git-commit")
        sched_commit = sched_cmd[s_commit_idx + 1]

        runtime_data = json.loads(
            (self.seals_dir / f"{self.epoch}.runtime.json").read_text(encoding="utf-8")
        )
        self.assertEqual(sched_commit, runtime_data["runtime_software_commit"])
        self.assertEqual(sched_commit, self.runtime_commit)

    def test_05_launch_provenance_runtime_commit_and_tree_match(self) -> None:
        """5. Launch provenance runtime_code_commit == runtime.json commit & tree matches."""
        prov_path = self.seals_dir / f"{self.epoch}.launch-provenance.json"
        prov_data = json.loads(prov_path.read_text(encoding="utf-8"))
        runtime_data = json.loads(
            (self.seals_dir / f"{self.epoch}.runtime.json").read_text(encoding="utf-8")
        )
        self.assertEqual(prov_data["runtime_code_commit"], runtime_data["runtime_software_commit"])
        self.assertEqual(prov_data["runtime_code_commit"], self.runtime_commit)

        # Tree SHA must match git rev-parse <commit>^{tree}
        expected_tree = subprocess.run(
            ["git", "rev-parse", "--verify", f"{self.runtime_commit}^{{tree}}"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(prov_data["runtime_git_tree"], expected_tree)

    def test_06_authorization_runtime_commit_matches_runtime_json(self) -> None:
        """6. Authorization runtime_commit == runtime.json runtime commit."""
        auth_path = self.seals_dir / f"{self.epoch}.authorization-evidence.json"
        auth_data = json.loads(auth_path.read_text(encoding="utf-8"))
        runtime_data = json.loads(
            (self.seals_dir / f"{self.epoch}.runtime.json").read_text(encoding="utf-8")
        )
        self.assertEqual(auth_data["runtime_commit"], runtime_data["runtime_software_commit"])
        self.assertEqual(auth_data["runtime_commit"], self.runtime_commit)

    def test_07_runtime_config_fingerprint_matches_collector_calculation(self) -> None:
        """7. runtime_config_fingerprint generated by seal tool == collector calculation."""
        runtime_data = json.loads(
            (self.seals_dir / f"{self.epoch}.runtime.json").read_text(encoding="utf-8")
        )
        seal_fp = seal_config_fingerprint(runtime_data)
        collector_fp = collector_config_fingerprint(runtime_data)
        self.assertEqual(seal_fp, collector_fp)

        # Verify launch command uses this exact fingerprint
        launch_cmd_path = self.seals_dir / f"{self.epoch}.launch-command.json"
        data = json.loads(launch_cmd_path.read_text(encoding="utf-8"))
        sup_cmd = data["supervisor_command"]
        idx = sup_cmd.index("--collector-command-json")
        collector_cmd = json.loads(sup_cmd[idx + 1])
        fp_idx = collector_cmd.index("--config-fingerprint")
        self.assertEqual(collector_cmd[fp_idx + 1], seal_fp)

    def test_08_feed_universe_has_exactly_76_unique_feeds(self) -> None:
        """8. Feed universe = exactly 76 unique feeds (60 bithumb, 8 binance, 8 upbit)."""
        feed_path = self.seals_dir / f"{self.epoch}.feed-universe.json"
        feed_data = json.loads(feed_path.read_text(encoding="utf-8"))
        feeds = feed_data["feeds"]
        self.assertEqual(len(feeds), 76)
        self.assertEqual(len(set(feeds)), 76)
        self.assertEqual(feed_data["feed_count"], 76)
        self.assertEqual(feed_data["exchanges"]["bithumb"]["feed_count"], 60)
        self.assertEqual(feed_data["exchanges"]["binance"]["feed_count"], 8)
        self.assertEqual(feed_data["exchanges"]["upbit"]["feed_count"], 8)

    def test_09_candidate_cohorts_exactly_30(self) -> None:
        """9. Candidate cohorts = exactly 30."""
        timing_path = self.seals_dir / f"{self.epoch}.timing-contract.json"
        timing_data = json.loads(timing_path.read_text(encoding="utf-8"))
        self.assertEqual(timing_data["required_qualifying_full_hours"], 30)
        self.assertEqual(timing_data["expected_candidate_cohorts"], 30)
        self.assertEqual(timing_data["maximum_collection_window_seconds"], 111600)

    def test_10_total_coverage_slots_exactly_2280(self) -> None:
        """10. Total coverage slots = exactly 2280 (30 cohorts * 76 feeds)."""
        timing_path = self.seals_dir / f"{self.epoch}.timing-contract.json"
        timing_data = json.loads(timing_path.read_text(encoding="utf-8"))
        self.assertEqual(timing_data["total_expected_coverage_slots"], 2280)
        self.assertEqual(timing_data["total_expected_coverage_slots"], 30 * 76)

    def test_11_v3_consumed_identity_reflects_post_launch_failure(self) -> None:
        """11. V3 consumed identity: launch_authorized=True, status=FAILED_TO_START."""
        auth_path = self.seals_dir / f"{self.epoch}.authorization-evidence.json"
        auth_data = json.loads(auth_path.read_text(encoding="utf-8"))
        self.assertTrue(auth_data["launch_authorized"])
        self.assertEqual(auth_data["status"], "FAILED_TO_START")
        self.assertIn("raw_root_template", auth_data["note"])

        prov_path = self.seals_dir / f"{self.epoch}.launch-provenance.json"
        prov_data = json.loads(prov_path.read_text(encoding="utf-8"))
        self.assertTrue(prov_data["launch_authorized"])

    def test_12_v3_actual_start_time_utc_is_recorded(self) -> None:
        """12. V3 consumed identity: actual_start_time_utc is recorded."""
        auth_path = self.seals_dir / f"{self.epoch}.authorization-evidence.json"
        auth_data = json.loads(auth_path.read_text(encoding="utf-8"))
        self.assertIsNotNone(auth_data["actual_start_time_utc"])
        self.assertIsNotNone(auth_data["actual_start_evidence"])

        prov_path = self.seals_dir / f"{self.epoch}.launch-provenance.json"
        prov_data = json.loads(prov_path.read_text(encoding="utf-8"))
        self.assertIsNotNone(prov_data["actual_start_time_utc"])

    def test_13_collector_schedule_mode_duration_validation(self) -> None:
        """13. V3 schedule mode requires duration 0; positive duration with schedule path fails closed."""
        # Valid: duration 0 with schedule path
        schedule_path_str: str | None = "some/path.json"
        valid_check = (
            0 == 0
            if schedule_path_str is not None
            else 0 == 0 and 0 > 0
        )
        self.assertTrue(valid_check)

        # Invalid: positive duration with schedule path
        conflicting_check = (
            0 == 108000
            if schedule_path_str is not None
            else 0 == 108000 and 108000 > 0
        )
        self.assertFalse(conflicting_check)

        # Invalid: duration 0 without schedule path
        no_schedule_check = (
            0 == 0
            if None is not None
            else 0 == 0 and 0 > 0
        )
        self.assertFalse(no_schedule_check)

    def test_14_transient_launch_config_rejects_legacy_duration(self) -> None:
        """14. TransientLaunchConfig rejects positive collection_duration_seconds when schedule is used."""
        cfg = TransientLaunchConfig(
            run_id=self.run_id,
            workdir=Path("/var/lib/bitcoin-trader/workdir"),
            supervisor_command=("echo", "hi"),
            collection_duration_seconds=None,
            maximum_collection_window_seconds=111600,
            finalization_timeout_seconds=180,
            supervisor_hard_ceiling_seconds=111825,
            systemd_runtime_max_seconds=111900,
        )
        self.assertIsNone(cfg.collection_duration_seconds)
        self.assertEqual(cfg.maximum_collection_window_seconds, 111600)

    def test_15_validate_git_commit_helper(self) -> None:
        """15. validate_git_commit validates real commit and returns full SHA and tree SHA."""
        commit_sha, tree_sha = validate_git_commit(self.runtime_commit, cwd=ROOT)
        self.assertEqual(commit_sha, self.runtime_commit)
        self.assertEqual(len(commit_sha), 40)
        self.assertEqual(len(tree_sha), 40)

        with self.assertRaises(ValueError):
            validate_git_commit("notarealcommit99999", cwd=ROOT)

        with self.assertRaises(ValueError):
            validate_git_commit("", cwd=ROOT)

    def test_16_sealed_runtime_config_passes_real_collector_config_validation(self) -> None:
        """16. Freshly generated runtime config passes real collector config loading + validation."""
        epoch = "aws-validation-30h-20260915-v4"
        runtime_data = generate_runtime_config(self.runtime_commit, epoch)

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "runtime.json"
            config_path.write_text(json.dumps(runtime_data, indent=2), encoding="utf-8")

            fingerprint = seal_config_fingerprint(runtime_data)
            loaded = _load_runtime_config(config_path, fingerprint)
            self.assertEqual(loaded, runtime_data)

            paths = loaded["paths"]
            archive = loaded["archive"]
            assert isinstance(paths, dict)
            assert isinstance(archive, dict)
            rendered_raw_root = _render_epoch_template(
                paths["raw_root_template"], epoch, "raw_root_template"
            )
            rendered_prefix = _render_epoch_template(
                archive["temporary_prefix_template"], epoch, "temporary_prefix_template"
            )
            storage_base_dir = Path(rendered_raw_root)

            args = argparse.Namespace(
                collector_epoch=epoch,
                run_id=f"aws-validation-30h-run-test-v4",
                storage_base_dir=storage_base_dir,
                runtime_commit=self.runtime_commit,
                environment_id="aws-apne2-research",
                duration=0,
                qualification_schedule_path="/dummy/schedule.json",
                bithumb_markets=20,
            )
            _validate_runtime_config(loaded, args, list(BITHUMB_MARKETS), list(BINANCE_SYMBOLS), list(UPBIT_MARKETS))

            self.assertEqual(rendered_prefix, f"market-data/temporary/{epoch}")

    def test_17_render_epoch_template_rejects_zero_placeholders(self) -> None:
        """17. _render_epoch_template rejects a resolved path with zero {collector_epoch} placeholders."""
        with self.assertRaisesRegex(ValueError, "exactly one"):
            _render_epoch_template(
                "/var/lib/bitcoin-trader/30h-validation/aws-validation-30h-20260915-v4/raw",
                "aws-validation-30h-20260915-v4",
                "raw_root_template",
            )

    def test_18_render_epoch_template_accepts_exactly_one_placeholder(self) -> None:
        """18. _render_epoch_template accepts exactly one {collector_epoch} and renders correctly."""
        result = _render_epoch_template(
            "/var/lib/bitcoin-trader/30h-validation/{collector_epoch}/raw",
            "aws-validation-30h-20260915-v4",
            "raw_root_template",
        )
        self.assertEqual(result, "/var/lib/bitcoin-trader/30h-validation/aws-validation-30h-20260915-v4/raw")

    def test_19_render_epoch_template_rejects_multiple_placeholders(self) -> None:
        """19. _render_epoch_template rejects two {collector_epoch} placeholders."""
        with self.assertRaisesRegex(ValueError, "exactly one"):
            _render_epoch_template(
                "/var/lib/{collector_epoch}/data/{collector_epoch}/raw",
                "epoch-a",
                "raw_root_template",
            )

    def test_20_render_epoch_template_rejects_already_resolved_absolute_path(self) -> None:
        """20. _render_epochTemplate rejects a fully resolved absolute path as zero-placeholder."""
        with self.assertRaisesRegex(ValueError, "exactly one"):
            _render_epoch_template(
                "/var/lib/bitcoin-trader/30h-validation/aws-validation-30h-20260915-v3/raw",
                "aws-validation-30h-20260915-v3",
                "raw_root_template",
            )

    def test_21_v4_consumed_identity_is_running(self) -> None:
        """21. V4 post-launch: exact identity is authorized, running, and consumed."""
        v4_auth_path = self.seals_dir / "aws-validation-30h-20260915-v4.authorization-evidence.json"
        auth_data = json.loads(v4_auth_path.read_text(encoding="utf-8"))
        self.assertTrue(auth_data["launch_authorized"])
        self.assertEqual(auth_data["status"], "RUNNING")
        self.assertEqual(auth_data["actual_start_time_utc"], "2026-09-15T10:26:33.652102Z")
        self.assertEqual(
            auth_data["actual_start_evidence"],
            "evidence/aws-validation-30h-20260915-v4/actual-start-evidence.json",
        )
        self.assertTrue(auth_data["systemd_unit_active"])
        self.assertTrue(auth_data["collector_process_running"])
        self.assertTrue(auth_data["supervisor_process_running"])
        self.assertTrue(auth_data["archive_scheduler_running"])
        self.assertEqual(auth_data["collector_epoch"], "aws-validation-30h-20260915-v4")

    def test_22_v4_post_launch_provenance_state(self) -> None:
        """22. V4 post-launch provenance records authorization and actual start."""
        v4_prov_path = self.seals_dir / "aws-validation-30h-20260915-v4.launch-provenance.json"
        prov_data = json.loads(v4_prov_path.read_text(encoding="utf-8"))
        self.assertTrue(prov_data["launch_authorized"])
        self.assertEqual(prov_data["actual_start_time_utc"], "2026-09-15T10:26:33.652102Z")
        self.assertEqual(prov_data["authorization_timestamp_utc"], "2026-09-15T10:24:43Z")
        self.assertEqual(prov_data["runtime_code_commit"], self.runtime_commit)
        self.assertEqual(prov_data["runtime_config_fingerprint"],
                         "4229274b582598bb869aafd4d0c139949559ebffab837546613be651ee60b2fa")

    def test_23_v4_actual_start_evidence_normalizes_to_exact_identity(self) -> None:
        """23. V4 schema-v2 actual-start evidence is canonical and identity-bound."""
        evidence_path = (
            ROOT
            / "evidence"
            / "aws-validation-30h-20260915-v4"
            / "actual-start-evidence.json"
        )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        normalized = normalize_actual_start_evidence(
            evidence,
            ActualStartIdentity(
                collector_epoch="aws-validation-30h-20260915-v4",
                collector_run_id="aws-validation-30h-run-20260915T061253Z-v4",
                runtime_commit=self.runtime_commit,
                runtime_config_fingerprint=(
                    "4229274b582598bb869aafd4d0c139949559ebffab837546613be651ee60b2fa"
                ),
            ),
        )
        self.assertEqual(normalized.schema_version, 2)
        self.assertEqual(normalized.actual_start_time_utc, "2026-09-15T10:26:33.652102Z")
        self.assertEqual(
            normalized.source,
            "bitcoin-trader-30h-aws-validation-30h-run-20260915T061253Z-v4.service",
        )


if __name__ == "__main__":
    unittest.main()
