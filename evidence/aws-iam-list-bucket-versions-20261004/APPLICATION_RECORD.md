# IAM application record — s3:ListBucketVersions (applied 2026-10-04)

Supersedes the READY_FOR_GO status of `PRE_APPLICATION_RECORD.md` (which is preserved unchanged as history).
Authorization: explicit user GO in the resume prompt (bootstrap user granted temporary narrow IAM rights).

## Identities
* bootstrap: `arn:aws:iam::080109295433:user/bitcoin-trader-bootstrap`
* provisioner: `arn:aws:sts::080109295433:assumed-role/bitcoin-trader-terraform-provisioner/codex-preapply-validation`

## Live pre-state (`live-pre-state-20261004/`, hashes in `SHA256SUMS.txt`)
Boundary default v6; versions v2..v6 (5); v2 non-default; boundary v6 and collector inline `collector-epoch-access`
byte-for-byte equal (as JSON) to the recorded `pre-state/`. Proposal differed from live only by the one new statement. No material difference.

## Applied (`apply-log/`)
1. `aws iam delete-policy-version --version-id v2` (boundary `bitcoin-trader-collector-boundary`; v2 preserved in `live-pre-state-20261004/boundary-v2.json`).
2. `aws iam create-policy-version --set-as-default` from `proposed-boundary-v7.json` -> **v7 (default)**; live v7 == proposed (v6 + exactly one statement `ListTerminalReceiptVersions`). v6 retained non-default.
3. `aws iam put-role-policy` (provisioner) collector `collector-epoch-access` from `proposed-collector-epoch-access.json`; live == proposed. Other inline policy `session-manager-agent-core` untouched. Bucket policy untouched.

## Verification
* `iam simulate-principal-policy` (`apply-log/simulation.txt`): ALLOW only for exact `.../aws-validation-TEST/terminal/terminal-receipt.json`;
  implicitDeny for `archive-receipts/`, `terminal-receipt.json.bak`, non-`aws-validation-` prefix, `market-data/`, empty prefix, and an unrelated bucket.
* Real collector role on EC2 (`apply-log/ec2-collector-verification.txt`, SSM session):
  COLLECTOR_CALLER_ARN=`arn:aws:sts::080109295433:assumed-role/bitcoin-trader-aws-apne2-research-collector/i-008bc503c1136349f`;
  LIST_EXACT_TERMINAL_RECEIPT_VERSIONS=PASS (1 version `CyNf97lAI6BWXpXHoiSEZZ4X_PtSRjv5`, IsLatest=true);
  LIST_UNRELATED_PREFIX=AccessDenied (DENY).
