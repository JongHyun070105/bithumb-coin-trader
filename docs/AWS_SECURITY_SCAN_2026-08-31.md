# AWS pre-apply security validation — 2026-08-31

## Scope and safety

검증 범위는 `infra/aws/` Terraform과 IAM policy example이다. 이번 작업에서는
`terraform apply`, AWS application resource 생성, collector 실행, credential 출력/저장을
수행하지 않았다.

Codex Security 앱 deep scan은 두 개의 작업공간에서 MCP proxy 오류 `-32000`으로
시작되지 않았다. 따라서 앱 스캔 결과는 **NOT VERIFIED — MCP PROXY FAILURE**이며,
이를 PASS로 대체하지 않는다. 아래 결과는 독립적인 로컬 검증이다.

## Identity evidence

- AWS CLI: dedicated `bitcoin-trader-bootstrap` browser-login session + MFA — **VERIFIED**
- provisioner AssumeRole session, max 1 hour — **VERIFIED**
- root account, MFA, recovery, and zero-access-key posture — **PRESERVED**
- root was used only to reconcile the existing provisioner inline policy after local policy
  validation and Access Analyzer returned zero findings. The root CLI session was then logged
  out; root was not used by Terraform.
- Terraform used an explicit temporary `bitcoin-trader-terraform-provisioner` assumed-role
  session after an STS identity guard. Root/default profile fallback was not used.
- application resource creation — **NOT RUN**
- static access key creation — **NOT RUN**; root access keys remain zero

## Live IAM reconciliation — 2026-09-01

Before the authorized IAM-only update, the live provisioner inline policy differed from the
reviewed template. The reviewed template was rendered only in process with current non-secret
account-derived resource names; no rendered policy was written to the repository.

- reviewed JSON parse and manual semantic assertions — **PASS**
- IAM Access Analyzer identity-policy validation — **PASS, zero findings**
- pre-update custom-policy positive/negative simulation — **PASS**
- authorized write — existing `bitcoin-trader-terraform-provisioner` inline policy only
- live/template canonical SHA-256 after read-back —
  `3ce03c7d3d5206bf9b926e7dfc03c3fef4aa532c864b3680eaf82046855baed5`, **MATCHED**
- collector permissions-boundary live/template canonical SHA-256 —
  `87decd98fb752576ed3ccf7cb6fcdaa57983b5527183fa5e847b0fdd4aeeebb5`, **MATCHED**

The live provisioner simulation allowed only the reviewed Seoul provisioning, exact archive
bucket control plane, exact collector role/profile, exact boundary-constrained role creation,
and exact collector-role PassRole to EC2. It denied both non-Seoul write cases, another bucket,
archive object Get/Put/Delete (explicit deny), IAM user creation, arbitrary role creation,
Secrets Manager, KMS key creation, arbitrary PassRole, boundary-free collector role creation,
Organizations/Identity Center administration, boundary mutation, trust-policy mutation, and
managed-policy attachment.

## Static validation

- `terraform fmt -check -recursive` — **PASS**
- `terraform init -backend=false -lockfile=readonly` — **PASS**
- `terraform validate` — **PASS**
- IAM example JSON parse — **PASS**
- `git diff --check` — **PASS**
- secret-pattern scan — **PASS** (only intentional empty/example references remain)
- Trivy 0.74.0 — **5 existing findings, no new finding**
- Python compile — **PASS**
- Python regression — **478 PASS**
- Python dependency check — **PASS**
- Dashboard typecheck, lint, 4 tests, and build — **PASS**
- Dashboard npm audit — **0 vulnerabilities**

Trivy classifications remain unchanged for the initial public-data soak:

| ID | Decision |
|---|---|
| AWS-0104 unrestricted TCP/443 egress | ACCEPT; dynamic exchange/AWS endpoints, zero ingress |
| AWS-0132 SSE-S3 rather than CMK | ACCEPT; public market data and no private account data |
| AWS-0178 VPC Flow Logs absent | OPTIONAL HARDENING |
| AWS-0017 CloudWatch CMK absent | ACCEPT; operational-only logs |
| AWS-0089 S3 access logging absent | OPTIONAL HARDENING |

## Changes included in this review

- Replaced the broad `AmazonSSMManagedInstanceCore` attachment with an inline agent policy
  matching the collector permissions boundary and excluding Parameter Store reads.
- Added tag-scoped Session Manager operator actions to the reviewed provisioner template.
- Added explicit provisioner deny for archive object data-plane actions, preventing a bucket
  policy self-grant from bypassing the intended provisioning boundary.
- Removed unnecessary `iam:UpdateAssumeRolePolicy` from the reviewed provisioner template.
- Added Access Analyzer validation and policy-simulation permissions to the reviewed template;
  the live role has not been changed automatically.
- Mandatory provenance tags can no longer be overridden by `additional_tags`.
- Apply requires a reviewed, pinned `ami_id_override`; latest-AMI discovery remains available
  only for local static validation.

## Apply blockers and next gate

The IAM drift is reconciled. A fresh provider-backed plan ran with the temporary provisioner
session and the following non-secret review provenance:

- source commit: `abfc8f38a95c5e99e2dabd48853e8336cde85f23`
- plan-review ID: `plan-review-b9da26ff-5389-4643-93ae-6396de0c7871`
- plan config fingerprint:
  `ad7ba4a986b5c3fc89196edb2928bb1dd101530171eff2c0ad4dcaee6d7143ba`
- pinned AMI: `ami-08d82cf148c92fcc3`, Amazon-owned AL2023 x86_64, HVM/EBS, available
- result: **23 add / 0 change / 0 destroy**
- temporary plan file: **REMOVED**

The review ID above is not an actual collector process run ID. A new launch identity and config
fingerprint must be sealed immediately before a separately approved apply/launch.

Plan review confirmed zero security-group ingress, no SSH/dashboard/trading port, public IPv4
with HTTPS-only egress, `t3.medium`/x86_64, encrypted 100 GiB gp3 with
`delete_on_termination=false`, the exact collector permissions boundary, SSM-only
administration, and all four S3 Block Public Access settings. No Secrets Manager or trading
resource is present.

## Operational readiness gaps

- CloudWatch alarm infrastructure — **IMPLEMENTED**: five alarms use namespace
  `BitcoinTrader/Collector`, metrics `WriterErrors`, `QueueDrops`, and `DiskUsedPercent`, and
  `EnvironmentId` dimension. Missing data is treated as breaching.
- collector custom-metric publisher — **NOT IMPLEMENTED**: repository code contains no
  `PutMetricData` publisher. Alarm resources alone are not operational monitoring.
- closed-hour detection and raw SHA manifest generation — **IMPLEMENTED AND TESTED**, but the
  finalization command is offline/manual rather than an unattended AWS pipeline.
- zstd compression, compressed SHA, decompression verification, S3 upload, restore, restore
  SHA, verified uncompressed cleanup, and `.zst` FULL-SCAN support — **NOT IMPLEMENTED**.
- 100 GiB hot buffer — **PRE-SOAK GAPS**: do not start the 72-hour AWS collector until the
  metric publisher and fail-closed compression/archive/restore pipeline are implemented and
  smoke-tested on Amazon Linux.

The official `AmazonSSMManagedInstanceCore` policy contains the same agent actions plus
`ssm:GetParameter` and `ssm:GetParameters`. The reviewed collector policy intentionally excludes
those Parameter Store reads. Core `ssm`, `ssmmessages`, and `ec2messages` permissions match, but
the real Session Manager connection remains a post-provision smoke gate.

## Cost and credit gate

The 23-resource plan retains the conservative first-month planning envelope of about
**US$57.58/30 days** before credits, approximately **US$0.079/hour**, **US$1.92/day**,
**US$5.76/72 hours**, and **US$9.60/5 days**. Actual S3, log, and metric usage remains variable.

The user re-verified the AWS Billing Credits screen on 2026-09-01: US$120.00 total issued,
US$5.51 actual used / US$114.49 actual remaining, and US$6.82 estimated used / US$113.18
estimated remaining, expiring 2026-12-13. Credit identifiers are intentionally omitted. One
US$20 promotional credit may have eligibility restrictions, so the general-credit-only
estimated remainder is approximately US$93.18. Cost Explorer MTD and forecast remain
**NOT VERIFIED — NON-BLOCKING**; Billing permission is not added to the provisioner role.

The exact final deployment commit is the clean `HEAD == origin/main` used by the final plan and
is reported alongside the plan fingerprint. It is not hard-coded into the commit that contains
this sentence because a commit cannot include its own final SHA without changing that SHA.

AWS application resources created: **NO**. Terraform apply: **NOT RUN**. Codex Security app:
**NOT VERIFIED — MCP infrastructure failure -32000**. Local substitute security evidence:
**PASS with the five accepted/optional Trivy findings above**. Alpha: **BLOCKED**. Live:
**DISABLED**.
