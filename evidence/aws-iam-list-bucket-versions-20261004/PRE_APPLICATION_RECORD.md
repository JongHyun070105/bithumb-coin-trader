# IAM pre-application record — s3:ListBucketVersions for terminal-receipt version provenance

Status: **READY_FOR_GO** (not applied). Written 2026-10-04 before any IAM mutation.

## Observed denial (historical, immutable)

Source: `evidence/aws-short-e2e-3h-20261004-a4704f91/frozen-v2-bundle/terminal/s3-readback.json`

| Field | Value |
| --- | --- |
| CALLER_ARN | `arn:aws:sts::080109295433:assumed-role/bitcoin-trader-aws-apne2-research-collector/i-008bc503c1136349f` |
| BUCKET | `bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433` |
| PREFIX / key | `market-data/temporary/aws-validation-observability-3h-20261004-20261004T044500Z-a4704f91/terminal/terminal-receipt.json` |
| API | S3 `ListObjectVersions` (IAM action `s3:ListBucketVersions`, bucket-level, `Prefix=<exact key>`) |
| ERROR_CODE | `AccessDenied` (`version_list_outcome`) |

The unversioned `GetObject` on the same key succeeded (VersionId `CyNf97lAI6BWXpXHoiSEZZ4X_PtSRjv5`), so the denial is specific to the version inventory.

## Root cause

`s3:ListBucketVersions` is absent from (a) the collector inline policy `collector-epoch-access`
and (b) the permissions boundary `bitcoin-trader-collector-boundary` default version `v6`
(snapshots in `pre-state/`). Effective permission is the intersection, so both lack it. The
provisioner role only has `s3:ListBucket` on the bucket, and the bootstrap user only has
`sts:AssumeRole`; neither has `s3:ListBucketVersions`. This is a permission gap, not a capture bug.

## Does Frozen V2 really require a version inventory?

Yes. `scripts/audit_fresh_30h_terminal_v2.py` (frozen, byte-pinned) requires `terminal/s3-readback.json`
with non-empty `version_ids` containing the GET `VersionId` exactly once, `requested_version_id` null and
`latest_delete_marker` false. `GetObject`/`HeadObject` return only the one VersionId they read; they cannot show that no
newer version or delete marker exists, which is what the inventory proves. Get/Head are therefore insufficient.

## Existing-role preference

No existing role or profile can be used without a policy change: no available principal holds the action.
The change therefore has to be made to the principal that already performs the exact-key GET during capture
(the collector instance role). Alternatives rejected:

* Bucket policy grant to the collector role: would route around the permissions boundary that caps the collector;
  rejected as a boundary bypass.
* Granting the provisioner role the action: also needs an IAM-admin edit, and the capture uses one S3 client for GET + versions, so it would need
  a capture redesign. No gain.
* Weakening or changing the frozen auditor: forbidden.

## Proposed minimal change (no new infrastructure)

Add exactly one statement to BOTH documents (`proposed-boundary-v7.json`, `proposed-collector-epoch-access.json`):

```json
{
  "Sid": "ListTerminalReceiptVersions",
  "Effect": "Allow",
  "Action": "s3:ListBucketVersions",
  "Resource": "arn:aws:s3:::bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433",
  "Condition": {"StringLike": {"s3:prefix": "market-data/temporary/aws-validation-*/terminal/terminal-receipt.json"}}
}
```

* Exact missing permission: `s3:ListBucketVersions` (metadata listing only; no object read, no delete, no version-read).
* Resource scope: the one archive bucket; the `s3:prefix` condition limits it to the `terminal/terminal-receipt.json` key of an `aws-validation-*` epoch.
* Simulation (`iam simulate-principal-policy`, proposed inline + proposed boundary, run with the provisioner's read-only simulate right):
  allowed for the exact terminal-receipt key; `implicitDeny` for a coverage key, bucket-root prefix `market-data/`, a foreign prefix, and
  `terminal-receipt.json.bak`; `s3:ListBucket` and `s3:DeleteObjectVersion` stay `implicitDeny`. Current state: `implicitDeny`.
* Repo mirror updated in this worktree: `infra/aws/main.tf` (`collector` policy document) and `infra/aws/identity/collector-permissions-boundary-validation.json.example`.

## Why this stopped at READY_FOR_GO

The inline policy can be applied by the provisioner (`iam:PutRolePolicy` on the exact collector role), but the boundary is a customer-managed policy and
neither available profile can edit it: provisioner `iam:ListPolicyVersions` and the bootstrap user `iam:GetPolicyVersion` are AccessDenied, and the provisioner design deliberately
cannot edit the boundary. The managed policy already has 5 versions (v2..v6), so v2 must be deleted before v7 can be created; its content
should be preserved first (it is recorded in the account history only through the AWS API).

Human (IAM administrator) steps, after reviewing this record:

```sh
B=arn:aws:iam::080109295433:policy/bitcoin-trader-collector-boundary
aws iam get-policy-version --policy-arn $B --version-id v2 > boundary-v2.preserved.json   # preserve before deleting
aws iam delete-policy-version --policy-arn $B --version-id v2
aws iam create-policy-version --policy-arn $B --set-as-default \
  --policy-document file://evidence/aws-iam-list-bucket-versions-20261004/proposed-boundary-v7.json
```

Then the provisioner session applies the inline policy:

```sh
aws iam put-role-policy --role-name bitcoin-trader-aws-apne2-research-collector --policy-name collector-epoch-access \
  --policy-document file://evidence/aws-iam-list-bucket-versions-20261004/proposed-collector-epoch-access.json
```

Verification after applying: `iam simulate-principal-policy` must show allowed only for the exact key prefix, then the targeted witness E2E must record
`VERSION_PROVENANCE=PASS` from a real `ListObjectVersions`.

IAM_CHANGE_REQUIRED=YES, IAM_CHANGE_APPLIED=NO.
