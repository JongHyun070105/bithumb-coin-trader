# AWS 45M independent review and remediation design (2026-09-09)

## Independent historical review

| Claim | Verdict | Basis |
|---|---|---|
| 45M PROCESS PASS | CONFIRMED | `result.json` records supervisor PASS, collector/publisher/scheduler exit 0, no signal or forced timeout, final metrics valid, final manifest observed, and 2731.59 s elapsed below the 2820 s ceiling. |
| Natural lifecycle finalization PASS | CONFIRMED | Lifecycle evidence records `COMPLETE`, 152 manifests, and final flush; finalization took 16.14 s. |
| Scheduler cooperative shutdown PASS | CONFIRMED | Scheduler started, was stopped after collector, and exited 0. |
| Archive autonomy observed | CONFIRMED | One timestamp-derived eligible cohort produced 76 autonomous receipts; all failed closed before remote upload. |
| S3 403 root cause is old-epoch IAM boundary | PARTIALLY CONFIRMED | The root cause is IAM, but both the role inline policy and permissions boundary were hard-coded to the old epoch. Bucket policy and KMS were not causal. |
| Runtime source code itself innocent | PARTIALLY CONFIRMED | Remote-key generation matches the fresh epoch and the fail-closed state machine behaved as coded. The report's claimed Put-then-Head sequence was wrong: Head-on-existence-check happens before Put. No runtime defect caused the denial. |
| 76 feed universe correct | CONFIRMED | Runtime seal, receipts, and manifests agree on Bithumb 60 / Binance 8 / Upbit 8. |
| Report/evidence internally consistent | REJECTED | The summary had the wrong exchange breakdown and a nonexistent full commit SHA; it also described an upload that never occurred. The PASS/FAIL verdicts themselves match the evidence. |

## Exact IAM/S3 cause

- Execution role: the instance profile resolves to the dedicated collector role.
- Identity policy and permissions boundary: each allowed S3 access only under the old `aws-72h-soak-20260905-8017b83e` canonical/temporary prefixes.
- Live policy simulation: old-prefix `GetObject` and `PutObject` were allowed; the fresh 45m prefix was implicit-deny and not allowed by the boundary.
- Bucket policy: only denies insecure transport.
- Encryption: bucket default is SSE-S3 (`AES256`); no KMS permission is required for this path.
- Object existence: the representative fresh-epoch object returned not found to the independent read identity, and the fresh prefix listed no objects.
- Operation order: local raw verify -> compress -> local decompression verify -> `HeadObject` existence check -> conditional `PutObject` -> `HeadObject` verification -> `GetObject` restore. The first remote `HeadObject` failed, so `PutObject` was never reached.

## Minimal remediation

### Before

- S3 allow scope: exact old canonical and temporary epoch prefixes.
- Actions included unused `ListBucket` and multipart verbs.
- A fresh epoch required another policy rewrite and boundary reconciliation.

### After

- S3 allow scope: exact bucket, temporary objects matching `market-data/temporary/aws-validation-*/*`.
- Actions: `s3:GetObject` and `s3:PutObject` only. `HeadObject` is authorized by `GetObject`.
- Fresh validation epochs must start with `aws-validation-`; this includes the fresh 45m revalidation and the conditional 30h cross-date soak.
- Canonical paths, old epoch paths, other temporary namespaces, other buckets, delete operations, and trading/private APIs remain denied.

This is a narrower action and data-class scope than the old policy while removing per-run hard-coding. The Terraform role policy and bootstrap boundary template must remain identical in S3 scope.

### Rollback

Before applying, preserve the current inline policy JSON and current boundary version ID. If static/live validation fails, restore the inline JSON and set the previous boundary version as default. Do not delete the previous boundary version during this remediation.

## Validation sequence

1. Policy regression tests, Terraform format/validate, Access Analyzer, and policy simulation.
2. Terraform plan must be only in-place IAM policy changes: no add, destroy, replace, drift, security-group, storage, or compute changes.
3. Apply the reviewed IAM change; verify a no-change post-apply plan and live principal simulation.
4. Create a fresh seal, epoch, run ID, evidence path, and launch provenance.
5. Run one unattended 45m validation. Do not touch the historical failed run.
6. Independently review the fresh evidence. Start no 30h run unless every 45m gate passes.

## Future validation diagnostic and post-run guard

During an official unattended validation, diagnostics against the active epoch and S3 namespace must be read-only. `PutObject`, `DeleteObject`, manual archive, receipt rewriting, and test writes under the active prefix are prohibited. If a write diagnostic is ever separately authorized, it must use a distinct diagnostic namespace that can never qualify as validation evidence.

After natural process completion, derive eligibility from actual timestamps and inspect archive terminal evidence first. A run with failed receipts or missing required fullscan remains FAIL; do not create artifacts to requalify it. For a potentially qualifying run, preserve actual-start evidence, compose the official epoch contract, run `build_epoch_manifest.py --strict`, and only then run `audit_72h_soak.py` bound to that sealed root. `epoch_manifest.json` is a post-run evidence artifact, not a collector-runtime output.
