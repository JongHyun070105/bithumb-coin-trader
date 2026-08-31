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
- root credential fallback / AWS environment credential variables — **NOT PRESENT**
- application resource creation — **NOT RUN**
- current provisioner policy read-back — existing policy remains older than the reviewed template; IAM-only policy refresh is required before final plan

## Static validation

- `terraform fmt -check -recursive` — **PASS**
- `terraform init -backend=false -lockfile=readonly` — **PASS**
- `terraform validate` — **PASS**
- IAM example JSON parse — **PASS**
- `git diff --check` — **PASS**
- secret-pattern scan — **PASS** (only intentional empty/example references remain)
- Trivy 0.74.0 — **5 existing findings, no new finding**

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

1. Update the existing provisioner inline policy through an explicitly approved IAM-only
   bootstrap, then re-run Access Analyzer validation and policy simulation. Do not broaden
   permissions beyond the reviewed template.
2. Re-read the collector permissions boundary and exact epoch/bucket prefixes.
3. With the temporary provisioner session only, run the provider-backed plan using a pinned
   AL2023 AMI and sealed provenance values. A plan is not an apply approval.
4. Re-check credits/cost immediately before any separately approved application apply.

The Terraform plan is therefore **NOT VERIFIED after this code change** until the live
provisioner policy is reconciled and a fresh provider-backed plan is generated. AWS resources
created: **NO**. Terraform apply: **NOT RUN**. Alpha: **BLOCKED**. Live: **DISABLED**.
