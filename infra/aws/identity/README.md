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

## proposed bootstrap changes — 별도 승인 필요

현재 금지된 resource creation 없이 least-privilege session을 만들 수 없으므로 다음 IAM 변경은 아직 실행하지 않는다.

1. customer-managed permissions boundary `bitcoin-trader-collector-boundary` 생성.
2. deployment role `bitcoin-trader-terraform-provisioner` 생성, maximum session 1 hour.
3. trust의 `Principal` account ARN은 `aws:PrincipalArn`이 exact root ARN이고 MFA가 존재할 때만 일치하도록 제한. 단순 account-wide delegation, IAM user/role, external principal은 허용하지 않음.
4. role inline policy는 `terraform-provisioner-permissions-policy.json.example`의 범위만 허용.
5. collector role에 위 permissions boundary를 지정하도록 Terraform을 수정하고, boundary ARN이 없으면 plan을 fail closed하도록 함.
6. root browser-login은 bootstrap과 provisioner `sts:AssumeRole` source에만 사용하고 즉시 logout. Terraform은 1시간 temporary assumed-role session만 사용하며 static access key는 생성하지 않음.

bootstrap은 IAM boundary, provisioner role, role policy라는 AWS 변경이므로 사용자의 별도 승인이 필요하다. application Terraform의 23-resource plan과 분리해 기록한다.

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
3. approved root bootstrap session으로 boundary와 provisioner role만 생성한다.
4. root + MFA → `sts:AssumeRole`로 temporary profile을 얻은 즉시 root login cache를 제거한다.
5. temporary role로 `sts get-caller-identity`; report에는 account ID를 쓰지 않는다.
6. `terraform fmt -check -recursive`, `terraform validate`, provider-backed `terraform plan`을 실행한다.
7. 기존 결과 **23 add / 0 change / 0 destroy**와 다르면 apply 금지 후 원인을 분석한다.
8. credit/billing을 다시 read-only 확인한다.
9. 별도 final apply 승인을 받기 전에는 `terraform apply`를 실행하지 않는다.

이 root trust는 개인 단일-account 환경을 위한 temporary operational compromise다. IAM Identity Center 또는 정상적인 MFA 관리 identity가 준비되면 provisioner trust에서 root account principal을 제거하는 것을 security hardening backlog로 유지한다.
