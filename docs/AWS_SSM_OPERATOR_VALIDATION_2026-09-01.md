# AWS Session Manager operator validation — 2026-09-01

## Scope and safety boundary

This validation reconciled the existing Terraform provisioner role's Session Manager
operator permissions and exercised one read-only guest session. It did not run Terraform,
create application resources, install or start the collector, call a private exchange API, or
place an order. Account IDs, role unique IDs, session IDs, credentials, MFA data, and rendered
account-specific policies are intentionally omitted.

## IAM ownership model

The prior lifecycle resource pattern was not valid for assumed-role sessions: the actual
Session Manager session name is derived from the assumed role's principal ID, not the visible
STS assumed-role ARN path. The reviewed policy now uses the AWS-managed session ownership tag:

```json
{
  "Action": [
    "ssm:ResumeSession",
    "ssm:TerminateSession"
  ],
  "Resource": "*",
  "Condition": {
    "StringLike": {
      "ssm:resourceTag/aws:ssmmessages:session-id": "${aws:userid}*"
    }
  }
}
```

The wildcard resource is limited to these two lifecycle actions and constrained by the AWS
system tag. It does not broaden `StartSession`: starting a session remains limited to the
tagged collector instance and the exact `SSM-SessionManagerRunShell` document. The shell
document ARN is account-scoped in this region. `ssmmessages:OpenDataChannel` remains a separate,
action-only wildcard statement because that action does not support resource-level scoping.

Official references: [restrict Session Manager access by session tags](https://docs.aws.amazon.com/systems-manager/latest/userguide/getting-started-restrict-access-examples.html),
[Systems Manager authorization actions and condition keys](https://docs.aws.amazon.com/service-authorization/latest/reference/list_awssystemsmanager.html),
and [Session Manager plugin installation](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html).

Validation evidence:

- JSON parse and manual semantic diff — **PASS**
- IAM Access Analyzer before and after the live update — **PASS, zero findings**
- matching ownership-tag simulation — **ALLOW**
- mismatching and absent ownership-tag simulations — **DENY**
- unrelated IAM, Secrets Manager, KMS, Organizations, Identity Center, arbitrary PassRole,
  boundary-free role creation, and archive object data-plane simulations — **DENY**
- authorized live write — the existing provisioner inline policy only
- canonical live/template SHA-256 after read-back —
  `6f902291fdd1751ea4a48a297276fec5b5b4d116bb8f63ad55db70f3389a5905`, **MATCHED**
- root account, MFA, recovery, and zero-access-key posture — **PRESERVED**
- root IAM-only execution session — **TERMINATED**; root Terraform use — **NO**

## Session lifecycle smoke

The local Apple Silicon Session Manager plugin was installed from the Amazon-signed,
Apple-notarized package and verified at version `1.2.835.0`.

- managed node status — **ONLINE**
- old test session — **TERMINATED** using the same role-session-name ownership context
- new provisioner assumed-role session start — **SUCCESS**
- guest shell identity — `ssm-user`
- normal shell exit and AWS history status — **SUCCESS / TERMINATED**
- separate same-owner lifecycle session and `TerminateSession` API call — **SUCCESS / TERMINATED**
- active sessions after the test — **0**
- other-session termination — **DENY by policy simulation**; no second live shell was opened

## Guest read-only smoke

- OS/instance — Amazon Linux 2023, x86_64, `t3.medium`, 2 vCPU, about 3.7 GiB RAM
- SSM agent — enabled and active, version `3.3.4624.0`
- endpoint access — regional `ssm` and `ssmmessages` DNS resolution and TLS verification pass;
  unauthenticated endpoint probes return HTTP 400 as expected
- IMDS — IMDSv2 token and instance metadata read succeed
- time — `chronyd` enabled and active, Amazon Time Sync selected, leap status normal
- storage — 100 GiB XFS root volume, about 3% used at validation time
- runtime inventory — Python 3.9.25 present; AWS CLI is not installed on the guest
- collector process — **NONE**

No package, repository, service, systemd collector unit, key, or application configuration was
installed on the guest.

## Initial registration delay

The immediate cause is **VERIFIED** from the guest journal. The SSM agent started at
`04:46:48 UTC`, registered the EC2 identity, but could not initially obtain instance-profile
role credentials from EC2 metadata. Its credential refresher entered a `26m43s` retry backoff
and then connected successfully with instance-profile credentials at `05:13:33 UTC`.

This establishes an initial credential-availability/backoff delay. The logs do not independently
prove whether the underlying transient was IAM propagation, instance-profile attachment timing,
or another EC2 metadata availability race, so no narrower root cause is claimed.

## Infrastructure and collector state

- security-group ingress — **0**
- SSH — **NONE**
- NAT gateways / Elastic IPs / VPC endpoints — **0 / 0 / 0**
- new application resources during this validation — **0**
- Terraform apply during this validation — **NOT RUN**
- collector / collector systemd / 120-minute smoke / 72-hour soak — **NOT STARTED**
- alpha — **BLOCKED**
- paper — **NOT STARTED**
- live — **DISABLED**
- Bithumb private API and orders — **NO ACTION**

## Remaining pre-soak blockers

- CloudWatch custom metric publisher
- zstd compression automation
- compressed SHA generation
- decompression verification
- S3 upload/archive pipeline
- restore and restore-SHA verification
- verified uncompressed-raw cleanup
- `.zst` FULL-SCAN support
- Amazon Linux collector-runtime short smoke

Session Manager operator access is validated, but it does not clear these independent pre-soak
gates and does not authorize collector deployment or execution.
