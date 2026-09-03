# AWS short-smoke boundary reconciliation — 2026-09-03

## Result

- Short-smoke boundary reconciliation: **PASS**
- Short-smoke provenance apply: **PASS**
- Collector, archive worker, metric publisher, WebSocket, and guest deployment: **NOT STARTED**
- Alpha: **BLOCKED**
- Live trading: **DISABLED**

This maintenance changed only the collector permissions-boundary epoch and the
three previously reviewed in-place Terraform provenance/prefix updates. It did
not deploy runtime commit `c965fa08608cf33a81ce29994bdc97b7a6f2d66b` and did
not start any collector component.

## Why the earlier apply was blocked

Before reconciliation, both the collector inline policy and its permissions
boundary allowed only `aws-v91-clean-soak-20260830`, while the reviewed
Terraform plan changed only the inline policy to
`aws-short-smoke-20260902-38cb8a72`. Applying that plan directly would have
made the effective intersection empty: the old prefix would fail the new
inline policy and the new prefix would fail the old boundary. Stopping the
earlier apply was therefore correct.

## Boundary reconciliation evidence

- Current default before: `v1`, OLD epoch
- Policy version count before: `1`
- Non-default version deletion: **NOT NEEDED**
- Candidate source: unchanged generic
  `infra/aws/identity/collector-permissions-boundary.json.example`
- Candidate semantic diff: exactly four OLD-to-NEW epoch substitutions
  (canonical/temporary ListBucket prefix and canonical/temporary object ARN)
- Action additions/removals: `0 / 0`
- Bucket, condition, region, namespace, SSM, and Logs semantic changes: `0`
- Access Analyzer before write: errors `0`, security warnings `0`
- New default: `v2`
- Immediate live read-back: **MATCHED**
- Canonical boundary SHA-256:
  `200a6e8e9f8089c18728d7d55e47d55f7d7557c2ea17e20f16d7e9ce5d0fe946`
- Root account: preserved; root access keys: `0`
- Privileged root CLI session: terminated immediately after boundary and
  transition verification

No boundary detach, policy deletion, bucket widening, old/new dual allowance,
or new IAM/S3 action occurred.

## Safe transition evidence

Immediately after the boundary switch and before Terraform apply:

| Effective check | Result |
|---|---|
| OLD temporary object actions | DENY |
| OLD canonical object actions | DENY |
| NEW temporary object actions | DENY |
| NEW canonical object actions | DENY |
| SSM core checks | ALLOW |
| `PutMetricData` with `BitcoinTrader/Collector` | ALLOW |
| `PutMetricData` with a wrong namespace | DENY |

The temporary S3 blackout was expected because boundary=NEW and inline=OLD.
No collector component was running during the transition.

Operational Logs policy resources and actions were unchanged. IAM simulation
using the policy's exact intended `collector:*` resource pattern returned
ALLOW for CreateLogStream, DescribeLogStreams, and PutLogEvents. A simulator
probe using a concrete synthetic log-stream ARN returned an action-specific
inconsistency (Describe allowed, Create/Put implicit deny) even though AWS's
official guidance identifies the existing `log-group:...:collector:*` form as
the scoped all-stream form. No policy widening was made in response. See the
[CloudWatch Logs identity-policy guidance](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/iam-identity-based-access-control-cwl.html).

## Terraform evidence

- Execution identity: temporary assumed role
  `bitcoin-trader-terraform-provisioner`, 3600-second maximum session
- Root Terraform: **NO**
- AWS credential fallback present before the session: **NO**
- Planning commit: `165d8157d25cc87518a1fa967a2788ed26518dee`
- Runtime provenance commit:
  `c965fa08608cf33a81ce29994bdc97b7a6f2d66b`
- Fresh plan: `0 add / 3 change / 0 destroy / 0 replace`
- Updates: collector role tags, collector inline archive epoch, EC2/root-EBS
  provenance tags
- AMI, instance type, EBS size/encryption, VPC, subnet, routing, security
  group, S3 controls, alarms, and boundary attachment: unchanged
- Exact saved-plan apply: **SUCCESS**
- Fresh post-apply provider plan: **NO CHANGES**

## Effective IAM after apply

| Effective check | Result |
|---|---|
| NEW temporary ListBucket/object actions | ALLOW |
| NEW canonical ListBucket/object actions | ALLOW |
| OLD temporary object actions | DENY |
| OLD canonical object actions | DENY |
| DeleteObject | DENY |
| Other bucket | DENY |
| Exact CloudWatch namespace | ALLOW |
| Wrong CloudWatch namespace | DENY |

Post-live Access Analyzer validation of both the actual inline policy and the
actual boundary returned errors `0` and security warnings `0`.

## Provenance read-back

- CollectorEpoch: `aws-short-smoke-20260902-38cb8a72`
- CollectorRunId:
  `aws-short-smoke-run-20260902T145020Z-38cb8a72`
- CollectorCommit: `c965fa08608cf33a81ce29994bdc97b7a6f2d66b`
- ConfigFingerprint:
  `c1f470b71db0b6b654daeef166fae04b7969bb116f6c0ee695ccc28269a41355`
- Collector role tags: **MATCH**
- EC2 tags: **MATCH**
- Root EBS tags: **MATCH**

## Infrastructure and state read-back

- EC2 identity: unchanged; state: running
- AMI and availability zone: unchanged
- Instance type: `t3.medium`
- Root EBS identity: unchanged; `100 GiB`, encrypted `gp3`,
  `delete_on_termination=false`
- Security-group ingress: `0`; SSH: none
- S3: private/BPA true, versioning enabled, SSE-S3, TLS-only policy
- CloudWatch alarms: `5`, unchanged
- NAT gateway / Elastic IP / VPC endpoint: `0 / 0 / 0`
- New/destroyed resources: `0 / 0`
- Terraform state: healthy, 29 managed/data addresses, mode `0600`, Git-ignored,
  stored on a FileVault-enabled volume
- Fresh pre-apply and post-apply backups: mode `0600`, SHA-256 equality verified
- Temporary saved plan: removed
- Terraform state: preserved

The backup files and state remain Git-ignored. No state contents, account ID,
full sensitive ARN, credential, session token, access-key ID, MFA material,
password, or role unique ID are recorded here.

## Stop boundary

Guest deployment, collector start, WebSocket connection, production metric
publisher loop, archive worker, raw cleanup, 120-minute validation, 72-hour
soak, alpha, paper, and live trading remain outside this approval and were not
performed. The next separate gate is runtime commit deployment plus launch
preflight.
