# Terraform provisioning identity design

이 디렉터리는 AWS resource를 만들지 않는 **bootstrap review input**이다. JSON의 `${...}`는 placeholder이며 그대로 실행하지 않는다. account ID나 credential을 Git에 기록하지 않는다.

## read-only account finding — 2026-08-29

- current browser-login session: verified root identity; read-only inspection에만 사용
- account MFA: enabled
- IAM users: 1
- IAM roles: 7; AWS service-linked role 4개와 다른 `openloop` workload role 3개뿐
- reusable Terraform deployment role: **없음**
- IAM Identity Center instance: 현재 session에서 accessible instance를 확인하지 못함
- tracked account ID, username, principal ARN: 없음; bootstrap 직전 local in-memory rendering에서만 사용

기존 `openloop` role은 trust와 runtime permission이 다른 프로젝트에 묶여 있으므로 재사용하지 않는다.

## bootstrap result — 2026-08-30

사용자의 root-bootstrap-only 승인을 받아 다음 IAM 변경만 실행했다.

1. customer-managed permissions boundary `bitcoin-trader-collector-boundary` 생성.
2. deployment role `bitcoin-trader-terraform-provisioner` 생성, maximum session 1 hour.
3. trust의 `Principal` account ARN은 `aws:PrincipalArn`이 exact root ARN이고 MFA가 존재할 때만 일치하도록 제한. 단순 account-wide delegation, IAM user/role, external principal은 허용하지 않음.
4. `terraform-provisioner-permissions-policy.json.example`과 exact-match인 inline policy 1개 적용. managed policy attachment는 0개.
5. collector role에 위 permissions boundary를 지정하도록 Terraform을 수정함.

Access Analyzer는 boundary, provisioner policy, trust 모두 error/warning 0이었다. 다른 region, 다른 S3 bucket, Secrets Manager, IAM user 생성, arbitrary PassRole, boundary 없는 collector role 생성은 simulation에서 모두 implicit deny였다.

그러나 root browser-login credential의 `sts:AssumeRole`은 AWS가 `Roles may not be assumed by root accounts`로 거부했다. trust/MFA 조건은 완화하지 않았고 provisioner temporary session, provider-backed re-plan, application resource는 생성·실행하지 않았다. root login cache는 즉시 제거했다.

다음 단계에는 root가 아닌 사용 가능한 MFA 관리 identity가 필요하다. IAM Identity Center를 구성하거나 기존 관리 identity의 인증을 복구한 뒤, 별도 승인으로 provisioner trust를 그 exact principal로 교체해야 한다. 현재 root-only trust는 account-wide exposure는 없지만 operationally unusable하므로 final apply gate를 통과하지 못한다.

## permission boundary 목적

provisioner는 exact collector role에 inline policy를 쓸 수 있어야 한다. boundary가 없으면 그 inline policy를 과도하게 바꿔 privilege escalation할 수 있다. `collector-permissions-boundary.json.example`은 collector가 가질 수 있는 최대 권한을 다음으로 제한한다.

- Amazon SSM agent core channel
- exact epoch S3 canonical/temporary prefix
- `BitcoinTrader/Collector` metric namespace
- exact operational log group

Secrets Manager, IAM, trading credential, order/account API 권한은 없다.

## provisioner scope와 잔여 한계

- EC2 write action은 `ap-northeast-2`와 현재 plan에 필요한 API verb로 제한한다.
- S3, IAM role/profile, CloudWatch log/alarm은 exact name/ARN으로 제한한다.
- collector role은 exact permissions boundary와 `AmazonSSMManagedInstanceCore`만 attach할 수 있다.
- `iam:PassRole`은 exact collector role을 EC2에 전달하는 경우만 허용한다.
- Budgets가 비활성이므로 Budgets permission은 없다.
- Secrets Manager, KMS customer key, live trading 관련 permission은 없다.

VPC/subnet/route association처럼 create 전 ARN이 없거나 tag condition 지원이 일관되지 않은 EC2 API는 region-level write scope가 남는다. 따라서 이 role은 1시간 session, explicit profile, reviewed plan SHA, apply 직전 identity 재검증으로 보완한다.

## approval 이후 검증 순서

1. placeholder를 실제 non-secret account-derived ARN과 sealed epoch/bucket name으로 rendering하되 rendered policy는 Git에 저장하지 않는다.
2. IAM Access Analyzer `validate-policy`와 policy simulation을 수행한다.
3. root가 아닌 approved MFA management identity를 마련하고 trust를 exact principal로 별도 review한다.
4. 해당 identity → `sts:AssumeRole`로 temporary profile을 얻는다.
5. temporary role로 `sts get-caller-identity`; report에는 account ID를 쓰지 않는다.
6. `terraform fmt -check -recursive`, `terraform validate`, provider-backed `terraform plan`을 실행한다.
7. 기존 결과 **23 add / 0 change / 0 destroy**와 다르면 apply 금지 후 원인을 분석한다.
8. credit/billing을 다시 read-only 확인한다.
9. 별도 final apply 승인을 받기 전에는 `terraform apply`를 실행하지 않는다.

IAM Identity Center 또는 정상적인 MFA 관리 identity를 마련하고 provisioner trust에서 root account principal을 제거하는 것이 현재 blocking security backlog다.
