"""RED Regression tests for launch artifact generation, validation, and duration consistency.

Proves rejection of:
1. Duration inconsistency (runtime.json duration vs collector --duration vs supervisor collection_duration_seconds)
2. Invalid template placeholders (missing {collector_epoch}, multiple, or pre-expanded)
3. Config to invocation path binding mismatches (RAW root, manifests, compressed, receipts, metrics, archive base-dir)
4. Unsupported soak durations (ensuring 10800s and 21600s are properly supported)
5. Historical V1/V2 malformed artifacts
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
for d in (ROOT, SRC_DIR):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

from bithumb_coin_trader.bounded_supervisor import TransientLaunchConfig, render_systemd_run
from bithumb_coin_trader.launch_artifacts import (
    ValidationRunSpec,
    generate_canonical_runtime_config,
    generate_launch_artifacts,
    resolve_epoch_paths,
    validate_launch_artifacts,
    validate_template_placeholders,
)


class TestLaunchArtifactRegressions(unittest.TestCase):
    def setUp(self) -> None:
        self.commit = "32b667e39f94684246915bd0d1d17dd611688a3a"
        self.epoch_90m = "aws-validation-observability-90m-20260917-20260917T120000Z-v3"
        self.run_id_90m = "aws-validation-observability-90m-run-20260917T120000Z-v3"

    # ----------------------------------------------------------------------
    # RED REGRESSION 1: DURATION CONSISTENCY
    # ----------------------------------------------------------------------
    def test_duration_consistency_rejects_runtime_zero_with_positive_target(self) -> None:
        """Reject if runtime config duration is 0 but expected soak is 5400 (V1 bug pattern)."""
        spec = ValidationRunSpec(
            epoch=self.epoch_90m,
            run_id=self.run_id_90m,
            duration_seconds=5400,
            runtime_commit=self.commit,
        )
        runtime_config = generate_canonical_runtime_config(spec)
        # Mutate runtime config to duration 0 (like V1)
        runtime_config["duration_seconds"] = 0

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            with self.assertRaisesRegex(ValueError, "duration"):
                validate_launch_artifacts(
                    spec=spec,
                    runtime_config=runtime_config,
                    target_dir=tmp_dir,
                )

    def test_duration_consistency_rejects_collector_supervisor_mismatch(self) -> None:
        """Reject if collector --duration does not match supervisor collection_duration_seconds."""
        spec = ValidationRunSpec(
            epoch=self.epoch_90m,
            run_id=self.run_id_90m,
            duration_seconds=5400,
            runtime_commit=self.commit,
        )
        runtime_config = generate_canonical_runtime_config(spec)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            artifacts = generate_launch_artifacts(spec, target_dir=tmp_dir)
            # Corrupt supervisor command to 2700s
            sup_cmd = list(artifacts.launch_command["supervisor_command"])
            idx = sup_cmd.index("--collection-duration-seconds")
            sup_cmd[idx + 1] = "2700"
            artifacts.launch_command["supervisor_command"] = sup_cmd

            with self.assertRaisesRegex(ValueError, "duration"):
                validate_launch_artifacts(
                    spec=spec,
                    runtime_config=runtime_config,
                    launch_command=artifacts.launch_command,
                    target_dir=tmp_dir,
                )

    # ----------------------------------------------------------------------
    # RED REGRESSION 2: TEMPLATE PLACEHOLDERS
    # ----------------------------------------------------------------------
    def test_template_placeholders_rejects_hardcoded_paths(self) -> None:
        """Reject if template paths do not contain {collector_epoch} (V2 bug pattern)."""
        # V2 bad paths pattern:
        bad_paths = {
            "raw_root_template": "/var/lib/bitcoin-trader/90m-validation/aws-observability-90m-20260917/raw",
            "manifest_root_template": "/var/lib/bitcoin-trader/90m-validation/aws-observability-90m-20260917/manifests",
            "compressed_root_template": "/var/lib/bitcoin-trader/90m-validation/aws-observability-90m-20260917/compressed",
            "receipt_root_template": "/var/lib/bitcoin-trader/90m-validation/aws-observability-90m-20260917/archive-receipts",
            "metrics_path_template": "/var/lib/bitcoin-trader/90m-validation/aws-observability-90m-20260917/collector_metrics.json",
            "publisher_state_path_template": "/var/lib/bitcoin-trader/90m-validation/aws-observability-90m-20260917/metric-publisher-state.json",
            "log_root_template": "/var/lib/bitcoin-trader/90m-validation/aws-observability-90m-20260917/logs",
        }
        with self.assertRaisesRegex(ValueError, "must contain exactly one {collector_epoch}"):
            validate_template_placeholders(bad_paths)

    def test_template_placeholders_rejects_multiple_placeholders(self) -> None:
        """Reject if template path contains more than one {collector_epoch}."""
        bad_paths = {
            "raw_root_template": "/var/lib/{collector_epoch}/90m/{collector_epoch}/raw",
        }
        with self.assertRaisesRegex(ValueError, "must contain exactly one {collector_epoch}"):
            validate_template_placeholders(bad_paths)

    def test_template_placeholders_accepts_valid_canonical_templates(self) -> None:
        """Accept canonical template paths that have exactly one {collector_epoch}."""
        spec = ValidationRunSpec(
            epoch=self.epoch_90m,
            run_id=self.run_id_90m,
            duration_seconds=5400,
            runtime_commit=self.commit,
        )
        config = generate_canonical_runtime_config(spec)
        paths = config["paths"]
        assert isinstance(paths, dict)
        validate_template_placeholders(paths)

    # ----------------------------------------------------------------------
    # RED REGRESSION 3: CONFIG / INVOCATION PATH BINDING
    # ----------------------------------------------------------------------
    def test_path_binding_rejects_mismatched_collector_storage_base_dir(self) -> None:
        """Reject if collector --storage-base-dir does not match resolved raw_root_template."""
        spec = ValidationRunSpec(
            epoch=self.epoch_90m,
            run_id=self.run_id_90m,
            duration_seconds=5400,
            runtime_commit=self.commit,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            artifacts = generate_launch_artifacts(spec, target_dir=tmp_dir)

            # Mismatched collector command: pointing to parent dir instead of epoch raw
            sup_cmd = list(artifacts.launch_command["supervisor_command"])
            idx = sup_cmd.index("--collector-command-json")
            coll_cmd = json.loads(sup_cmd[idx + 1])
            s_idx = coll_cmd.index("--storage-base-dir")
            coll_cmd[s_idx + 1] = "/var/lib/bitcoin-trader/90m-validation/wrong-epoch/raw"
            sup_cmd[idx + 1] = json.dumps(coll_cmd)
            artifacts.launch_command["supervisor_command"] = sup_cmd

            with self.assertRaisesRegex(ValueError, "path binding"):
                validate_launch_artifacts(
                    spec=spec,
                    runtime_config=artifacts.runtime_config,
                    launch_command=artifacts.launch_command,
                    target_dir=tmp_dir,
                )

    def test_archive_scheduler_base_dir_must_be_epoch_root(self) -> None:
        """Reject if archive scheduler --base-dir is parent validation dir rather than epoch root."""
        spec = ValidationRunSpec(
            epoch=self.epoch_90m,
            run_id=self.run_id_90m,
            duration_seconds=5400,
            runtime_commit=self.commit,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            artifacts = generate_launch_artifacts(spec, target_dir=tmp_dir)

            # Corrupt archive scheduler command to use parent dir (V1/V2 bug pattern)
            sup_cmd = list(artifacts.launch_command["supervisor_command"])
            idx = sup_cmd.index("--archive-scheduler-command-json")
            sched_cmd = json.loads(sup_cmd[idx + 1])
            b_idx = sched_cmd.index("--base-dir")
            sched_cmd[b_idx + 1] = "/var/lib/bitcoin-trader/90m-validation"
            sup_cmd[idx + 1] = json.dumps(sched_cmd)
            artifacts.launch_command["supervisor_command"] = sup_cmd

            with self.assertRaisesRegex(ValueError, "archive scheduler"):
                validate_launch_artifacts(
                    spec=spec,
                    runtime_config=artifacts.runtime_config,
                    launch_command=artifacts.launch_command,
                    target_dir=tmp_dir,
                )

    # ----------------------------------------------------------------------
    # PRODUCTION DURATION POLICY SUPPORT: 3H (10800s) AND 6H (21600s)
    # ----------------------------------------------------------------------
    def test_bounded_supervisor_supports_10800_and_21600(self) -> None:
        """Verify render_systemd_run accepts 10800s (3h) and 21600s (6h)."""
        cfg_3h = TransientLaunchConfig(
            run_id="test-run-3h",
            workdir=Path("/var/lib/bitcoin-trader/work"),
            supervisor_command=("python3", "test"),
            collection_duration_seconds=10800,
            finalization_timeout_seconds=180,
            supervisor_hard_ceiling_seconds=10980,
            systemd_runtime_max_seconds=11040,
        )
        cmd_3h = render_systemd_run(cfg_3h)
        self.assertIn("--unit=bitcoin-trader-3h-test-run-3h.service", cmd_3h)
        self.assertIn("--service-type=notify", cmd_3h)
        self.assertIn("--property=NotifyAccess=main", cmd_3h)
        self.assertIn("--property=WatchdogSec=60s", cmd_3h)

        cfg_6h = TransientLaunchConfig(
            run_id="test-run-6h",
            workdir=Path("/var/lib/bitcoin-trader/work"),
            supervisor_command=("python3", "test"),
            collection_duration_seconds=21600,
            finalization_timeout_seconds=180,
            supervisor_hard_ceiling_seconds=21780,
            systemd_runtime_max_seconds=21840,
        )
        cmd_6h = render_systemd_run(cfg_6h)
        self.assertIn("--unit=bitcoin-trader-6h-test-run-6h.service", cmd_6h)
        self.assertIn("--service-type=notify", cmd_6h)

    # ----------------------------------------------------------------------
    # HISTORICAL V1 / V2 ARTIFACTS REJECTION
    # ----------------------------------------------------------------------
    def test_validator_rejects_historical_v1_artifacts(self) -> None:
        """Existing V1 launch artifacts must be rejected by validator."""
        v1_runtime_path = ROOT / "reliability-artifacts" / "aws-90m" / "aws-observability-90m-20260917-20260917T043600Z-v1.runtime.json"
        v1_launch_cmd_path = ROOT / "reliability-artifacts" / "aws-90m" / "launch-command.json"
        if v1_runtime_path.exists() and v1_launch_cmd_path.exists():
            v1_runtime = json.loads(v1_runtime_path.read_text(encoding="utf-8"))
            v1_launch_cmd = json.loads(v1_launch_cmd_path.read_text(encoding="utf-8"))
            spec = ValidationRunSpec(
                epoch="aws-observability-90m-20260917-20260917T043600Z-v1",
                run_id="aws-observability-90m-run-20260917T043600Z-v1",
                duration_seconds=5400,
                runtime_commit="32b667e39f94684246915bd0d1d17dd611688a3a",
                s3_bucket="local-test",
            )
            with self.assertRaises(ValueError):
                validate_launch_artifacts(
                    spec=spec,
                    runtime_config=v1_runtime,
                    launch_command=v1_launch_cmd,
                )

    def test_validator_rejects_historical_v2_artifacts(self) -> None:
        """Existing V2 launch artifacts must be rejected by validator."""
        v2_runtime_path = ROOT / "reliability-artifacts" / "aws-90m" / "aws-observability-90m-20260917-20260917T050128Z-v2.runtime.json"
        v2_launch_cmd_path = ROOT / "reliability-artifacts" / "aws-90m" / "launch-command-v2.json"
        if v2_runtime_path.exists() and v2_launch_cmd_path.exists():
            v2_runtime = json.loads(v2_runtime_path.read_text(encoding="utf-8"))
            v2_launch_cmd = json.loads(v2_launch_cmd_path.read_text(encoding="utf-8"))
            spec = ValidationRunSpec(
                epoch="aws-observability-90m-20260917-20260917T050128Z-v2",
                run_id="aws-observability-90m-run-20260917T050128Z-v2",
                duration_seconds=5400,
                runtime_commit="32b667e39f94684246915bd0d1d17dd611688a3a",
                s3_bucket="local-test",
            )
            with self.assertRaises(ValueError):
                validate_launch_artifacts(
                    spec=spec,
                    runtime_config=v2_runtime,
                    launch_command=v2_launch_cmd,
                )

    def test_s3_iam_prefix_enforced_for_research_bucket(self) -> None:
        """Research bucket requires epoch to start with aws-validation- for IAM PutObject policy."""
        with self.assertRaisesRegex(ValueError, "aws-validation-"):
            ValidationRunSpec(
                epoch="aws-observability-90m-20260917-v3",
                run_id="run-v3",
                duration_seconds=5400,
                runtime_commit=self.commit,
            )

    def test_observer_command_and_t0_sequencing_in_launch_artifacts(self) -> None:
        """Verify observer_command is generated and launch_ec2_sh pre-starts observer before collector."""
        spec = ValidationRunSpec(
            epoch=self.epoch_90m,
            run_id=self.run_id_90m,
            duration_seconds=5400,
            runtime_commit=self.commit,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            artifacts = generate_launch_artifacts(spec, target_dir=tmp_dir)

            # 1. observer_command present in launch_command
            obs_cmd = artifacts.launch_command.get("observer_command")
            self.assertIsNotNone(obs_cmd)
            assert obs_cmd is not None
            self.assertIn("bithumb_coin_trader.runtime_observer", obs_cmd)
            self.assertIn("--data-dir", obs_cmd)
            self.assertIn("--allow-s3-write", obs_cmd)

            # 2. launch_ec2_sh guarantees OBSERVER_START <= COLLECTOR_START
            ec2_sh = artifacts.launch_ec2_sh
            self.assertIn("OBSERVER_START <= COLLECTOR_START", ec2_sh)
            self.assertIn("must be executed with root/sudo privileges", ec2_sh)
            self.assertIn("bitcoin-trader-obs-", ec2_sh)
            self.assertIn("systemd-run", ec2_sh)
            self.assertIn("--uid=bitcoin-trader", ec2_sh)
            self.assertIn("systemctl is-active", ec2_sh)
            self.assertIn("Step A: Enforcing planned start window arrival", ec2_sh)
            self.assertIn("Step D: Final pre-collector freshness re-check", ec2_sh)

            # 3. Validator passes cleanly
            res = validate_launch_artifacts(
                spec=spec,
                runtime_config=artifacts.runtime_config,
                launch_command=artifacts.launch_command,
                target_dir=tmp_dir,
            )
            self.assertEqual(res["status"], "PASS")

    def test_validator_rejects_mismatched_observer_data_dir(self) -> None:
        """Validator rejects launch_command if observer --data-dir does not match epoch root."""
        spec = ValidationRunSpec(
            epoch=self.epoch_90m,
            run_id=self.run_id_90m,
            duration_seconds=5400,
            runtime_commit=self.commit,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            artifacts = generate_launch_artifacts(spec, target_dir=tmp_dir)

            # Corrupt observer command data-dir
            obs_cmd = list(artifacts.launch_command["observer_command"])
            idx = obs_cmd.index("--data-dir")
            obs_cmd[idx + 1] = "/var/lib/bitcoin-trader/wrong-epoch"
            artifacts.launch_command["observer_command"] = obs_cmd

            with self.assertRaisesRegex(ValueError, "observer_command data-dir binding failure"):
                validate_launch_artifacts(
                    spec=spec,
                    runtime_config=artifacts.runtime_config,
                    launch_command=artifacts.launch_command,
                    target_dir=tmp_dir,
                )

    # ----------------------------------------------------------------------
    # RED REGRESSION 6: ARTIFACT SEAL & IMMUTABILITY INTEGRITY
    # ----------------------------------------------------------------------
    def test_sealed_artifact_integrity_passes_intact_artifacts(self) -> None:
        """Validator verifies sealed_at_utc and artifact hashes correctly."""
        spec = ValidationRunSpec(
            epoch=self.epoch_90m,
            run_id=self.run_id_90m,
            duration_seconds=5400,
            runtime_commit=self.commit,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            artifacts = generate_launch_artifacts(spec, target_dir=tmp_dir)

            res = validate_launch_artifacts(
                spec=spec,
                runtime_config=artifacts.runtime_config,
                launch_command=artifacts.launch_command,
                target_dir=tmp_dir,
            )
            self.assertEqual(res["status"], "PASS")
            self.assertTrue(res.get("sealed_artifact_hashes_verified"))
            self.assertIsNotNone(res.get("sealed_at_utc"))

    def test_sealed_artifact_integrity_rejects_missing_sealed_at_utc(self) -> None:
        """Validator rejects identity.json if sealed_at_utc is missing or empty."""
        spec = ValidationRunSpec(
            epoch=self.epoch_90m,
            run_id=self.run_id_90m,
            duration_seconds=5400,
            runtime_commit=self.commit,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            artifacts = generate_launch_artifacts(spec, target_dir=tmp_dir)

            id_file = tmp_dir / "identity.json"
            id_data = json.loads(id_file.read_text(encoding="utf-8"))
            id_data["sealed_at_utc"] = ""
            id_file.write_text(json.dumps(id_data), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing or empty sealed_at_utc"):
                validate_launch_artifacts(
                    spec=spec,
                    runtime_config=artifacts.runtime_config,
                    launch_command=artifacts.launch_command,
                    target_dir=tmp_dir,
                )

    def test_sealed_artifact_integrity_rejects_tampered_artifact(self) -> None:
        """Validator rejects artifacts if a sealed file hash was tampered with."""
        spec = ValidationRunSpec(
            epoch=self.epoch_90m,
            run_id=self.run_id_90m,
            duration_seconds=5400,
            runtime_commit=self.commit,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            artifacts = generate_launch_artifacts(spec, target_dir=tmp_dir)

            # Tamper with launch-ec2.sh
            ec2_path = tmp_dir / "launch-ec2.sh"
            ec2_path.write_text(ec2_path.read_text() + "\n# tampered line", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                validate_launch_artifacts(
                    spec=spec,
                    runtime_config=artifacts.runtime_config,
                    launch_command=artifacts.launch_command,
                    target_dir=tmp_dir,
                )

    def test_sealed_artifact_integrity_rejects_tampered_identity_vs_manifest(self) -> None:
        """Validator rejects when identity.json is tampered relative to sealed-manifest.json."""
        spec = ValidationRunSpec(
            epoch=self.epoch_90m,
            run_id=self.run_id_90m,
            duration_seconds=5400,
            runtime_commit=self.commit,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            artifacts = generate_launch_artifacts(spec, target_dir=tmp_dir)

            id_file = tmp_dir / "identity.json"
            id_data = json.loads(id_file.read_text(encoding="utf-8"))
            id_data["note"] = "unauthorized edit"
            id_file.write_text(json.dumps(id_data), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "sealed-manifest.json identity_sha256 mismatch"):
                validate_launch_artifacts(
                    spec=spec,
                    runtime_config=artifacts.runtime_config,
                    launch_command=artifacts.launch_command,
                    target_dir=tmp_dir,
                )


if __name__ == "__main__":
    unittest.main()
