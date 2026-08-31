# AWS infrastructure — review-only baseline

이 디렉터리는 별도 AWS collector epoch의 **provisioning review 입력**이다. 현재 상태는 apply 승인이나 배포 완료를 의미하지 않는다.

## 안전 경계

- `terraform apply`는 사용자의 별도 명시적 승인 전 금지한다.
- EC2/S3/IAM 등 AWS provisioning CLI 명령도 금지한다.
- Terraform state, plan, crash log, 실제 tfvars는 Git에 저장하지 않는다.
- static AWS access key를 Terraform, user-data, 환경 예시 또는 저장소에 넣지 않는다.
- Dashboard는 공개하지 않으며 mock/read-only 상태를 유지한다.
- Alpha는 `BLOCKED`, live trading은 `DISABLED`다.

## AWS의 역할

AWS는 장기 raw data lake가 아니라 항상 켜져 있는 collector/trading runtime의 기반이다. 초기에는 V9.1 clean infrastructure soak만 수행하며, 이후 별도 gate를 통과할 때 paper/trading engine, risk engine, monitoring backend가 같은 운영 기반을 사용할 수 있다. alpha와 live는 현재 비활성이다.

EBS는 active raw와 압축 staging을 위한 **hot buffer**이고, S3는 선택된 canonical research/audit/reproduction evidence와 검토된 temporary artifact만 보관한다. 모든 raw를 EBS나 S3에 무기한 누적하지 않는다.

## 계획된 구성

- 서울 리전의 격리 VPC와 단일 public subnet
- public IPv4 한 개를 가진 collector EC2
- security group ingress **0개**; SSH 22와 dashboard 모두 닫힘
- SSM Session Manager 운영 접속
- IMDSv2 강제와 EC2 instance role 기반 최소 권한
- Amazon Linux 2023, apply 전 검토된 AMI를 pin한 암호화된 100 GiB gp3 hot buffer
- private S3 bucket, Block Public Access, versioning, SSE-S3, TLS 강제
- canonical/temporary epoch prefix에만 제한된 S3 권한
- operational metric/alarm/log만 CloudWatch에 저장
- collector role의 SSM agent는 boundary와 일치하는 inline policy만 사용하며 Parameter Store를 읽지 않음
- optional budget; 알림 주소가 없으면 resource 자체를 계획하지 않음
- Secrets Manager resource 없음. public market-data soak에는 private exchange credential이 필요하지 않음
- disk-used 70/80/90% warning/high/critical alarm. 검증되지 않은 raw 자동 삭제 없음

## hot-buffer pipeline

`active raw → finalized partition → manifest/SHA → zstd → decompression verification → archive/retention decision → verified uncompressed cleanup`

100 GiB는 5일치 uncompressed raw를 담는 크기가 아니다. zstd level 1 pipeline이 정상 작동하고, finalized uncompressed raw는 압축·복원·SHA·archive 결정이 끝난 뒤에만 정리한다. compression/archive가 지연되면 70/80/90% alarm에 따라 optional work를 줄이고 fail closed한다. EBS는 이후 무중단 확장할 수 있으므로 처음부터 과대 할당하지 않는다.

S3 분류:

- `canonical`: 실제 연구 dataset, holdout, final audit, reproduction evidence
- `temporary`: intermediate/debug/운영 artifact. expiration은 기본 off이며 별도 retention review 뒤에만 활성화 가능

## 인스턴스 선택

기본값은 검증되지 않은 ARM을 확정하지 않기 위해 `t3.medium`/`x86_64`다. `t4g.medium`/`arm64`는 비용상 권장 후보지만 Amazon Linux에서 build, test, websocket, zstd, 120분 smoke를 통과한 뒤에만 선택한다.

## 검증 명령

아래 명령은 resource를 생성하지 않는다.

```bash
cd infra/aws
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
```

Provider-backed `terraform plan`은 유효한 AWS 인증과 실제 provenance 값이 모두 필요하다. 인증이 없을 때 skip-check, fake credential, provider safety-check 비활성화를 사용하지 않는다.

승인 이후 실제 값을 넣는 절차:

1. `terraform.tfvars.example`을 Git에서 제외되는 `terraform.tfvars`로 복사한다.
2. exact commit, canonical non-secret config SHA-256, run ID, epoch, AZ를 고정한다.
3. ARM 후보라면 먼저 별도 smoke gate를 완료한다.
4. 유효한 AWS identity와 credit/비용 상태를 다시 확인한다.
5. `terraform plan -out=review.tfplan`을 실행하고 plan을 Git에 저장하지 않는다.
6. plan과 월 비용을 사용자에게 제시한다. apply는 별도 승인 전 실행하지 않는다.

관련 문서:

- `docs/AWS_DEPLOYMENT_PLAN_2026-08-29.md`
- `docs/AWS_COST_ESTIMATE_2026-08-29.md`
- `docs/AWS_SECURITY_MODEL.md`
- `docs/AWS_72H_SOAK_PLAN.md`
