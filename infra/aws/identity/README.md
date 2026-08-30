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

## initial bootstrap result — 2026-08-30

사용자의 root-bootstrap-only 승인을 받아 다음 IAM 변경만 실행했다.

1. customer-managed permissions boundary `bitcoin-trader-collector-boundary` 생성.
2. deployment role `bitcoin-trader-terraform-provisioner` 생성, maximum session 1 hour.
3. 최초 trust는 actual root + MFA로 제한했지만 AWS root account가 일반 IAM role을 assume할 수 없어 operationally unusable함을 확인.
4. `terraform-provisioner-permissions-policy.json.example`과 exact-match인 inline policy 1개 적용. managed policy attachment는 0개.
5. collector role에 위 permissions boundary를 지정하도록 Terraform을 수정함.

Access Analyzer는 boundary, provisioner policy, trust 모두 error/warning 0이었다. 다른 region, 다른 S3 bucket, Secrets Manager, IAM user 생성, arbitrary PassRole, boundary 없는 collector role 생성은 simulation에서 모두 implicit deny였다.

그러나 root browser-login credential의 `sts:AssumeRole`은 AWS가 `Roles may not be assumed by root accounts`로 거부했다. trust/MFA 조건은 완화하지 않았고 provisioner temporary session, provider-backed re-plan, application resource는 생성·실행하지 않았다. root login cache는 즉시 제거했다.

## dedicated browser-login identity gate

현재 승인된 교체 경로는 전용 최소 권한 IAM login identity다. AWS CLI 2.32 이상에서 지원하는 `aws login`으로 console credential과 MFA를 이용해 temporary CLI credential을 얻고, 그 session으로만 provisioner role을 assume한다. static access key는 만들지 않는다.

tracked example은 실제 account ID나 username 대신 `${ACCOUNT_ID}`와 `${BOOTSTRAP_USER_NAME}` placeholder만 가진다.

- `bootstrap-login-assume-policy.json.example`: exact provisioner role에 대한 MFA 조건부 `sts:AssumeRole`만 허용한다.
- AWS managed `SignInLocalDevelopmentAccess`: `aws login` OAuth flow에만 사용한다. application provisioning 권한이 아니다.
- `terraform-provisioner-trust-policy.json.example`: exact dedicated IAM user ARN + MFA만 신뢰한다. root, account-wide principal, 다른 user/role, external principal은 포함하지 않는다.
- 전용 user에는 EC2, S3, CloudWatch, IAM resource provisioning, Secrets Manager, trading 권한을 주지 않는다.

공식 근거: [AWS CLI browser sign-in](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html), [`SignInLocalDevelopmentAccess`](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/SignInLocalDevelopmentAccess.html), [IAM user console credentials](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_users.html).

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
3. approved root bootstrap scope 안에서 dedicated login identity, console profile, MFA와 최소 권한 policy를 만들고 provisioner trust를 exact user ARN + MFA로 교체한다.
4. root session을 종료한 뒤 dedicated identity의 `aws login` temporary session으로 provisioner role을 assume한다.
5. temporary role로 `sts get-caller-identity`; report에는 account ID를 쓰지 않는다.
6. `terraform fmt -check -recursive`, `terraform validate`, provider-backed `terraform plan`을 실행한다.
7. 기존 결과 **23 add / 0 change / 0 destroy**와 다르면 apply 금지 후 원인을 분석한다.
8. credit/billing을 다시 read-only 확인한다.
9. 별도 final apply 승인을 받기 전에는 `terraform apply`를 실행하지 않는다.

MFA가 확인된 dedicated login identity로 root trust를 교체하고 provisioner assumed-role session에서 provider-backed plan을 다시 검증하는 것이 현재 blocking gate다. IAM Identity Center/Organizations는 이 single-account 단계의 범위가 아니다.
