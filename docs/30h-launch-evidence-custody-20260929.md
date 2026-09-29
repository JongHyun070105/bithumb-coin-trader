# Fresh 30H Launch Evidence Custody — 2026-09-29

## Scope and identity

This inventory covers the exact run `aws-validation-observability-30h-run-20260929T055022Z-52f3d272`, epoch `aws-validation-observability-30h-20260929-20260929T055022Z-52f3d272`, runtime commit `b4d482363e2f988dad9c6d29053f97e1e4160883`, tree `5c96ed79fee107c1604ee7018621835910221fdf`.

The authoritative evidence checkout is `/Users/macintosh/.codex/worktrees/final-30h-preflight/bitcoin-trader`, at prelaunch commit `cce61af5b2e2fa6c2404a3555ca55177ceef8dbc` (committed 2026-09-29 15:22:11 KST, before collector start 2026-09-29 09:34:57Z). That commit is present in `origin/codex/final-30h-preflight-20260928`. This report is an audit record; the run evidence directories were not edited.

## Preserved evidence sets

| Set | Result / purpose | Git custody | Hash coverage observed now |
|---|---|---|---|
| Run identity and sealed launch files (`identity.json`, runtime JSON, `launch-command.json`, `launch.sh`, `launch-ec2.sh`, authorization, `sealed-manifest.json`) | Exact run identity and executable launch configuration | Tracked in prelaunch commit | The manifest binds identity and runtime artifacts. The prelaunch Git commit binds the manifest bytes. SHA256 of `sealed-manifest.json` computed from the committed file during this audit: `dbb0afcba47f804b0fa555f3babbfc0491f5b763ba90546d332ff861aafb4de1`. This is an audit-time SHA256 derived from the prelaunch Git object, not a claim that a standalone SHA256 was recorded at launch. |
| `fresh-readiness/` | Earlier prelaunch gate record, including both the first wrong-control-root FAIL and the corrected exact runtime-worktree PASS | 45 files tracked by `cce61af`; `SHA256SUMS.txt` lists the other 44 files | Complete relative to that directory: 44 payloads listed, checksum file excluded from itself. The Git commit predates the run and anchors the checksum file. |
| `fresh-readiness-refresh-20260929T092630Z/` | Exact 09:26Z refresh; 35/36, FAIL | 45 files untracked in the evidence checkout | Its checksum file lists 38 files; six payloads are not listed: `collection-timing.json`, `gate-execution.json`, `prelaunch-gate-output.txt`, `raw-guest-snapshot.json`, `readiness-evidence.json`, `refresh-status.json`. The checksum file is itself untracked and has no external signature/anchor in this set. |
| `fresh-readiness-refresh-20260929T092926Z/` | Exact 09:29Z refresh; 36/36, PASS | 54 files untracked in the evidence checkout | Its checksum file lists 38 files; 15 payloads are not listed: the six refresh wrapper files above plus `health-baseline.json`, `health-t1-late.json`, `health-t5.json`, `launch-artifact-staging.json`, `launch-attempt.json`, `post-gate-launch-boundary.json`, `post-stage-launch-boundary.json`, `postlaunch-health/late-health-capture.json`, and `postlaunch-health/t15-journal-window.json`. The checksum file is itself untracked and unanchored externally. |
| `postlaunch-health-observer-notify-audit-20260929T114048+0000/` | Later health/observer notification audit | Three files untracked | Checksum file lists two payloads; the checksum file is not self-authenticating. |

The hash values computed during this audit for the 09:26 and 09:29 wrapper files are post-hoc custody aids only. They must not be described as launch-time hashes. The two refresh directories, their differing outputs, and the 09:26 failure have been preserved intact.

## Why the gate changed from FAIL to PASS

At 09:26:35Z, `s3_prefix_not_reused` recorded `prefix_exists=false`, `key_count=0`, but status `FAIL`; `checked_by` was the assumed `bitcoin-trader-aws-apne2-research-collector` role. That single status produced the 35/36 gate failure. At 09:29:27Z, the same exact prefix was listed empty (`KeyCount=0`, no first key, no error) using the `bitcoin-trader-terraform-provisioner/codex-preapply-validation` principal; status became `PASS` and the gate passed 36/36.

This establishes an authority/method change between observations, not a change in the run identity or prefix. The preserved artifacts do not explain why the first observation was assigned FAIL despite its empty-prefix fields, nor why the caller changed; classify the rationale as `UNKNOWN`. The gate consumes the producer's `status` string and checks the evidence file hash; it does not independently repeat the S3 listing. The 09:29 evidence contains the caller ARN and empty-list result, but the refresh directory's local checksum manifest is not externally anchored. Preserve both outcomes; do not erase the 09:26 FAIL or treat the later PASS as proof that all 36 raw facts were independently recomputed by the gate.

The later launch-boundary evidence is also preserved: the 09:31:37Z post-gate boundary check failed because the staged guest artifact hashes were still null; the 09:34:36Z post-stage check passed after all four launch files matched the expected manifest hashes. This is a separately recorded staging transition, not a rewrite of either prelaunch gate result.

## Hash trust and evidence producers

- The tracked prelaunch `fresh-readiness/SHA256SUMS.txt` is bound by commit `cce61af5b2e2fa6c2404a3555ca55177ceef8dbc`, which is available under the remote evidence branch. The committed `sealed-manifest.json` bytes match the Git blob at that commit. Its SHA256 above was computed during this audit and remains explicitly post-hoc as a SHA256 value.
- The two refresh manifests are not in Git and do not hash themselves, their gate output, `readiness-evidence.json`, collection timing, or refresh status. Their own internal hashes are not independent trust roots.
- Check producers in the 09:29 readiness bundle point to command logs under `/tmp/fresh30h-20260929-window-final-52f3d272` and the launch refresh used a task-scoped SSM payload. The `collect_local_checks.py`, guest snapshot and repair/collection helpers remained in temporary work areas rather than the tracked prelaunch evidence package. The captured JSON is useful evidence, but the exact producer scripts and full stdout are not uniformly retained beside every check.
- The prelaunch gate's strongest recomputation is local contract checking: sealed identity/runtime values, exact paths, freshness window, S3 key/prefix binding, disk projection, positive test count and captured result fields. For most runtime/AWS claims it validates `status == PASS` and SHA256 of the referenced local evidence file, then checks selected JSON fields. This verifies bytes against the bundle's claimed digest, not the truth of the external operation.
- External tool evidence includes pytest, pyright, compileall, shell parser, Git and AWS/SSM outputs. A reported external exit code is still a captured producer claim unless the referenced raw log and exact command can be re-run against immutable inputs.

## Per-check prelaunch-gate trust classification

`RECOMPUTED` means the gate compares captured values to an explicit invariant itself. `EXTERNAL_TOOL_RESULT` means the central fact originates in an external process/AWS/SSM tool result. `STATUS_STRING_ONLY` means the gate's check-specific acceptance is primarily the producer-supplied `status=PASS`. `HASH_VERIFIED` below is a supplemental property: **all 36** checks must name a local evidence file whose SHA256 matches the digest declared in that same readiness bundle. That does not independently authenticate the producer or the remote operation. When multiple properties apply, the table lists the primary fact source and the gate's own extra checks.

| # | Check | Primary classification | Gate's direct check / limit |
|---:|---|---|---|
| 1 | `runtime_role_put_witness` | `EXTERNAL_TOOL_RESULT` | Captured guest AWS SDK PUT/HEAD/GET and SHA comparison; gate checks only status and evidence-file hash. |
| 2 | `runtime_role_put_receipts` | `EXTERNAL_TOOL_RESULT` | Captured guest AWS SDK receipt PUT/HEAD/GET results; no repeat call by the gate. |
| 3 | `runtime_role_read_required_objects` | `EXTERNAL_TOOL_RESULT` | Captured guest-role reads and object snapshot; status/hash are trusted. |
| 4 | `auditor_get_exact_witness` | `EXTERNAL_TOOL_RESULT` | Captured exact `GetObject`; remote/local SHA comparison is inside the capture. |
| 5 | `auditor_get_exact_receipt` | `EXTERNAL_TOOL_RESULT` | Captured exact `GetObject`; remote/local SHA comparison is inside the capture. |
| 6 | `auditor_head_exact_objects` | `EXTERNAL_TOOL_RESULT` | Captured `HeadObject` results; no fresh HEAD in gate. |
| 7 | `disk_capacity` | `RECOMPUTED` | Gate recomputes 30-hour projected free space and 50 GiB + 5 GiB reserve from captured fields. |
| 8 | `inode_capacity` | `STATUS_STRING_ONLY` | Guest snapshot values are hash-linked locally; gate does not enforce a minimum inode reserve. |
| 9 | `memory_and_swap` | `STATUS_STRING_ONLY` | Guest snapshot values are hash-linked locally; gate does not apply an explicit memory/swap threshold. |
| 10 | `collector_identity_conflict_clear` | `RECOMPUTED` | Gate binds run/epoch and checks `conflicting_count=0`; the process census itself is guest-produced. |
| 11 | `observer_identity_conflict_clear` | `RECOMPUTED` | Same: exact run/epoch and zero conflicts are recomputed from captured fields. |
| 12 | `scheduler_identity_conflict_clear` | `RECOMPUTED` | Same: exact run/epoch and zero conflicts are recomputed from captured fields. |
| 13 | `finalizer_identity_conflict_clear` | `RECOMPUTED` | Same: exact run/epoch and zero conflicts are recomputed from captured fields. |
| 14 | `transient_unit_identity_conflict_clear` | `RECOMPUTED` | Same: exact run/epoch and zero conflicts are recomputed from captured fields. |
| 15 | `identity_mount_conflict_clear` | `STATUS_STRING_ONLY` | Mount claim is guest-snapshot-derived; gate does not inspect mount output itself. |
| 16 | `identity_data_root_conflict_clear` | `STATUS_STRING_ONLY` | Path/root claim is guest-snapshot-derived; gate does not independently stat the guest path. |
| 17 | `runtime_commit_on_guest` | `EXTERNAL_TOOL_RESULT` | Guest Git result is captured; local gate separately validates its own pinned commit/tree. |
| 18 | `runtime_full_suite` | `EXTERNAL_TOOL_RESULT` | Captured pytest exit/log; gate also recomputes commit/tree binding, positive passed count, nonnegative skips and zero failures. |
| 19 | `changed_scope_pyright` | `EXTERNAL_TOOL_RESULT` | Captured pyright exit/log; gate also checks commit/tree and zero errors/warnings/informations. |
| 20 | `compileall` | `EXTERNAL_TOOL_RESULT` | Captured command result; gate does not re-run it. |
| 21 | `shell_syntax` | `EXTERNAL_TOOL_RESULT` | Captured parser result; gate does not re-run it. |
| 22 | `git_diff_check` | `EXTERNAL_TOOL_RESULT` | Captured Git command result; gate does not re-run it. |
| 23 | `target_full_hours_30` | `RECOMPUTED` | Gate checks `target_full_hours=30` and separately checks exact 108000-second identity. |
| 24 | `artifact_determinism` | `EXTERNAL_TOOL_RESULT` | Captured generator comparison; gate trusts its status/evidence file. |
| 25 | `runtime_python_environment` | `EXTERNAL_TOOL_RESULT` | Guest interpreter/dependency snapshot; gate does not import/execute on guest. |
| 26 | `closed_at_utc_idempotency` | `EXTERNAL_TOOL_RESULT` | Captured targeted pytest result; gate does not re-run the tests. |
| 27 | `default_live_safe` | `EXTERNAL_TOOL_RESULT` | Captured safety test result; gate does not re-run the tests. |
| 28 | `run_id_not_reused` | `RECOMPUTED` | Gate requires `run_id_exists=false`; guest process/path census remains producer evidence. |
| 29 | `epoch_not_reused` | `RECOMPUTED` | Gate requires `epoch_exists=false`; guest process/path census remains producer evidence. |
| 30 | `s3_prefix_not_reused` | `RECOMPUTED` | Gate binds exact bucket/prefix, requires empty-prefix claim, and requires a principal string; actual list is external. |
| 31 | `local_evidence_dir_not_reused` | `RECOMPUTED` | Gate requires `path_exists=false`; path and check time are producer-supplied. |
| 32 | `private_api_disabled` | `STATUS_STRING_ONLY` | Gate checks status and the broader sealed/runtime private-API invariants; evidence producer provides this check's status. |
| 33 | `paper_disabled` | `STATUS_STRING_ONLY` | Gate checks status and sealed safety state; the individual producer status is not recomputed. |
| 34 | `live_disabled` | `STATUS_STRING_ONLY` | Gate checks status and sealed safety state; the individual producer status is not recomputed. |
| 35 | `terminal_auditor_dry_run` | `EXTERNAL_TOOL_RESULT` | Captured fixture-only auditor command; gate trusts status and the recorded fixture scope. |
| 36 | `terminal_auditor_live_e2e` | `EXTERNAL_TOOL_RESULT` | Captured smoke/auditor outputs and S3 evidence; gate checks their fields but does not repeat the external smoke. |

For every row, `HASH_VERIFIED` applies only to the copied evidence file bytes versus the digest written into `readiness-evidence.json`. Since the path, digest, status and file are in one locally assembled bundle, an editor can change the file and regenerate the digest. Future gate hardening must preserve raw producer inputs/logs and bind a complete evidence manifest to a trusted prelaunch commit or signature.

## T+15

The preserved `postlaunch-health/t15-journal-window.json` says `full_t15_health_snapshot_captured=NO`; it contains journal activity in the interval and NotifyAccess/main-PID warnings, but no point-in-time queue, heartbeat, resources, or unit-property snapshot. A single targeted search across the exact V3 auditor, prelaunch gate, their tests, and project runbooks/playbooks found no pre-existing full T+15 snapshot acceptance requirement. Record `T15 = MISSED_NON_BLOCKING_INFORMATIONAL`; do not synthesize a snapshot or spend further audit time on this item.

## Current-run boundary

The 09:29 prelaunch gate and 09:34 launch artifacts are historical inputs to this run, not a current health snapshot. No prelaunch weakness alone invalidates the run. Future gate hardening is tracked separately; no thresholds, live runtime files, sealed artifacts, IAM, EC2, or S3 objects were changed for this report.
