# Targeted terminal-witness E2E (120s smoke) — 2026-10-04

Runtime commit `dc1248ea05e3b4f7ebbdef434a5dcbdbaa1d4b96`, tree `e3a66819d40f066bec90b1895dc96f36cdccf190`.
Epoch `aws-validation-witness-e2e-smoke-20261004T140014Z-dc1248ea`; run `aws-validation-witness-e2e-smoke-run-20261004T140014Z-dc1248ea`.
Unit `bitcoin-trader-witness-e2e-smoke-<run>.service`, InvocationID `8f8006608a7f48858d97a3db204741cc`, Result=success, ExecMainStatus=0, NRestarts=0.

* TERMINAL_WITNESS_LOCAL=PASS (`terminal-witness.json`: runtime_identity_bound=true, commit/tree bound, s3_uploaded=true after PutObject)
* TERMINAL_WITNESS_S3=PASS (terminal-only capture as collector role; receipt sha256 `65105a63...834c`, 6082 bytes)
* VERSION_PROVENANCE=PASS (`s3-readback.json` version_ids == [PUT VersionId `rbVnh3gLDxIaYRKKTzXjV.QEDqzjqOE4`]; requested_version_id null; latest_delete_marker false)
* LATEST_VERSION_PROVEN=PASS (collector ListObjectVersions: 1 version, IsLatest=true, equals witness PUT VersionId, 0 delete markers)
* SYSTEMD_MECHANICS_QUALIFIED=PASS (diagnostic only; SHORT_RUN_SYSTEMD_ACCEPTANCE=NOT_APPLICABLE; not acceptance evidence)

Supersedes the earlier smoke at commit 8f37c068 whose witness had runtime_identity_bound=false (identity.json was not in the data root);
that run is not preserved as a pass and the defect was fixed in dc1248e. This is NOT Frozen V2 acceptance and says nothing about alpha.
