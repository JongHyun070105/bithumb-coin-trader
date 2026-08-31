# AWS collector deployment plan — 2026-08-29

## 판정과 범위

- AWS application resource: **NOT CREATED**
- IAM bootstrap: **CREATED — boundary + provisioner role + reviewed inline policy only**
- Terraform apply: **PROHIBITED PENDING EXPLICIT APPROVAL**
- AWS application authentication: **DEDICATED LOGIN + MFA / PROVISIONER ASSUME VERIFIED**
- Provider-backed plan: **23 add / 0 change / 0 destroy with reviewed template; live policy reconciliation still pending**
- V9: **CLOSED / 72H SOAK PASS / DATA QUALITY FAIL**
- V9.1: local deployment-readiness baseline only
- V9.1 official state: **FROZEN LOCAL DEPLOYMENT-READINESS BASELINE**
- Dashboard: read-only mock UI, production 연결 없음
- Alpha: **BLOCKED**
- Live trading: **DISABLED**

이 단계는 AWS epoch의 reviewable infrastructure definition을 만드는 작업이다. collector 시작, raw upload, dashboard 배포, trading credential 저장, alpha mining, paper/live trading은 범위 밖이다.

이전 browser-login session은 account root identity였으나 일반 role AssumeRole이 거부되어 폐기했다. 현재는 dedicated IAM login identity + MFA의 temporary session으로 provisioner role을 AssumeRole한다. account ID, credential body, credit ID는 문서나 Git에 기록하지 않는다.

2026-08-29 read-only IAM inventory에서는 재사용 가능한 deployment role이 없었다. service-linked role과 다른 프로젝트 workload role은 trust/permission 경계가 맞지 않아 재사용하지 않는다. 2026-08-30 승인된 IAM bootstrap으로 boundary와 1시간 provisioner role 및 reviewed inline policy만 생성했다. root-only + MFA trust는 account-wide delegation을 막지만 AWS는 root account의 `AssumeRole` 자체를 거부했다. 따라서 root trust는 폐기하고 application 권한이 없는 dedicated IAM login identity의 `aws login` temporary session → exact provisioner `AssumeRole` 구조로 교체한다. static access key와 기존 administrator user 재사용은 금지한다. 상세 evidence와 identity gate는 [`infra/aws/identity/README.md`](../infra/aws/identity/README.md)에 기록한다.

## Provider-backed plan 검증 결과

2026-08-29 `ap-northeast-2a`를 review AZ로 선택하고 비밀값 없는 plan-review provenance를 명시해 provider-backed plan을 실행했다. 결과는 **23 to add, 0 to change, 0 to destroy**였다. plan 파일과 Terraform state는 저장하지 않았고 AWS resource는 생성하지 않았다.

| 계획 항목 | 수량 | 목적 |
|---|---:|---|
| VPC / Internet Gateway | 각 1 | 격리 network와 outbound public endpoint 경로 |
| public subnet / route table / association | 각 1 | 단일 collector node route 구성 |
| security group | 1 | ingress 0, outbound TCP/443만 허용 |
| EC2 | 1 | `t3.medium` x86_64 collector runtime |
| root EBS | EC2 내 1 | encrypted 100 GiB gp3 hot buffer |
| IAM role / inline policies / instance profile | 각 1 이상 | static key 없는 최소 권한 S3·CloudWatch·SSM 접근 |
| S3 bucket / public-access block / ownership / encryption / versioning / TLS policy | 각 1 | private selective archive와 transport protection |
| CloudWatch log group | 1 | operational log only |
| CloudWatch alarms | 5 | writer, queue, disk 70/80/90% 감시 |

Secrets Manager, S3 lifecycle, Budget resource는 현재 입력에서 계획되지 않았다. SSM agent는 permissions boundary와 일치하는 inline core policy로 활성화하고 Parameter Store 읽기 권한은 포함하지 않는다. root EBS의 `delete_on_termination=false`는 데이터 보호 의도지만 instance 제거 뒤 orphan 비용 위험이 있으므로 apply 승인 때 명시적으로 수용하고 종료 runbook에 volume 정리를 포함해야 한다.

plan-review용 epoch/run ID, config fingerprint와 bucket name은 launch seal이 아니다. 실제 apply 전 승인된 commit/config/environment로 새로 봉인하고 plan을 다시 검토한다.

## AWS의 실제 역할

AWS의 최종 목적은 **장기 raw archive 서버가 아니라 24/7 automated trading runtime**이다. 단계가 진행되면 이 단일 운영 기반에서 collector, paper/trading engine, risk engine, monitoring/dashboard backend와 최근 raw hot buffer를 실행한다. 현재 Terraform 단계는 그중 V9.1 collector infrastructure만 준비하며 trading/backend는 배포하지 않는다.

진행 경계:

`Mac V9 failure evidence → V9.1 local short validation → AWS clean infrastructure soak → clean prospective dataset → alpha research → paper → 별도 live approval`

AWS 72시간 soak PASS는 infrastructure validation일 뿐 `alpha_ready=true`가 아니다. V9 raw는 alpha dataset으로 사용하지 않는다.

## 권장 토폴로지

1. `ap-northeast-2`의 격리 VPC와 명시적으로 고정할 단일 AZ.
2. public subnet의 단일 EC2. NAT Gateway 비용을 피하기 위해 public IPv4 한 개를 사용한다.
3. security group ingress는 0개다. SSH 22와 dashboard/API 포트는 열지 않는다.
4. 운영 접속은 SSM Session Manager만 사용한다.
5. EC2는 instance role의 임시 credential만 사용한다.
6. 암호화된 100 GiB gp3를 OS/application/active raw/compression staging용 hot buffer로 사용한다.
7. S3에는 canonical research/audit/reproduction evidence와 검토된 temporary artifact만 올린다.
8. raw event는 CloudWatch Logs에 복제하지 않는다. durable counter와 운영 로그만 전송한다.

EKS, ECS, RDS, ALB, NAT Gateway는 현재 필요하지 않다.

## compute 선택 gate

| 후보 | 상태 | 월 730h 정가 | 선택 조건 |
|---|---|---:|---|
| `t4g.medium` / ARM64 | recommended candidate | US$30.37 | Amazon Linux ARM build/test/zstd/websocket/120분 smoke PASS |
| `t3.medium` / x86_64 | safe fallback | US$37.96 | ARM gate가 미완료 또는 실패할 때 사용 |

Terraform 기본값은 `t3.medium`/`x86_64`다. 소스와 package declaration이 단순하다는 사실은 Linux ARM runtime 검증을 대신하지 않는다.

clean soak에서 CPU, RAM, network, disk throughput, compression CPU, event rate를 측정한다. headroom이 과하면 Amazon Linux gate를 거쳐 `t3.small` 또는 `t4g.small`로 낮출 수 있다. `t3.medium`을 영구 최소 사양으로 간주하지 않는다.

## 100 GiB hot-buffer sizing

historical ingestion 24 GiB/day 기준 uncompressed raw는 3일 72 GiB, 5일 120 GiB다. 따라서 100 GiB를 5일 uncompressed 저장소로 사용할 수 없다.

정상 pipeline:

`active raw → partition finalize → manifest/raw SHA → zstd → decompression/SHA verification → S3 class/retention decision → verified uncompressed cleanup`

Mac 표본에서 zstd 결과는 level 1 3.72%, level 3 4.20%, level 6 3.49%였다. 5일 raw 120 GiB가 같은 범위로 압축되면 약 4.2–5.0 GiB지만, 이 값은 sizing evidence이지 AWS 보장치가 아니다. OS/application 15–20 GiB, active/finalization lag, compressed files, decompression verification, logs와 safety margin을 포함하면 정상 pipeline에서 100 GiB가 합리적인 초기 후보다.

비압축 pipeline 장애 기준 단순 모델(OS/application 15 GiB 가정):

| gp3 | 70% warning까지 raw 여유 | 90% critical까지 raw 여유 | 의미 |
|---:|---:|---:|---|
| 80 GiB | 약 41 GiB / 1.7일 | 약 57 GiB / 2.4일 | minimum, 대응 여유 짧음 |
| 100 GiB | 약 55 GiB / 2.3일 | 약 75 GiB / 3.1일 | **recommended initial** |
| 120 GiB | 약 69 GiB / 2.9일 | 약 93 GiB / 3.9일 | headroom candidate |

실제 OS 사용량과 stream burst를 AWS smoke에서 다시 측정한다. 100 GiB가 부족하면 gp3를 확장하며 검증되지 않은 raw를 임의 삭제하지 않는다.

disk-used 정책:

- 70% warning: compression/upload lag 조사, capacity projection 갱신.
- 80% high: optional research/debug work 중단, verified finalize/compress/archive 우선.
- 90% critical: 새 nonessential operation을 차단하고 writer가 손실 없이 fail closed하도록 준비. unverified raw 자동 삭제 금지.

## provisioning 전 immutable seal

다음 값이 모두 확정되지 않으면 `aws_instance.collector` precondition이 provider-backed plan을 거부한다.

- exact 40-character `git_commit`
- canonical non-secret config의 SHA-256 `config_fingerprint`
- `collector_epoch`
- `run_id`
- `environment_id`
- region과 AZ
- instance type과 architecture
- raw/manifest schema version
- wall clock source
- compression candidate

이 값들은 EC2, EBS, IAM role tag와 Terraform review output에 남긴다. 비밀값은 config fingerprint 입력에서 제외하고 이름/참조만 canonicalize한다.

## launch 전 단계

1. AWS 로그인과 caller identity, credit 잔액/만료/적용 서비스 재검증.
2. 공식 서울 가격 갱신 및 비용표 재승인.
3. 후보 commit을 clean working tree에서 만들고 origin SHA와 일치 확인.
4. non-secret config canonicalization 규칙을 고정하고 SHA-256 생성.
5. run ID와 epoch 이름 생성 후 변경 불가능하게 ledger에 기록.
6. Amazon Linux architecture smoke gate 수행.
7. `terraform fmt`, `init -backend=false`, `validate`, security scan.
8. 실제 identity로 provider-backed plan 생성 및 review.
9. 사용자가 plan/resource/monthly cost를 명시적으로 승인한 뒤에만 apply 가능.

## ARM Amazon Linux smoke gate

- Python 지원 버전과 package 설치
- 전체 Python unit/regression tests
- collector compile/import
- zstd level 1/3/6 압축·해제 SHA 및 CPU/RSS benchmark
- 7 exchange×stream subscription과 Binance symbol identity
- wall/monotonic timestamp persistence
- durable metrics, queue/drop, writer fail-closed
- SSM 접속과 chrony source 확인
- 최소 120분 isolated run과 graceful shutdown

Mac의 478.45 MiB benchmark는 후보 선택 근거일 뿐 AWS 성능 PASS가 아니다.

## apply 이후에도 자동 시작하지 않는 항목

Terraform은 collector code 배포·실행과 systemd enable/start를 수행하지 않는다. infrastructure smoke, provenance seal, configuration review가 끝난 뒤 별도 승인된 launch 절차로 시작한다.

## 중단과 비용 통제

- budget email이 승인되지 않으면 Terraform은 budget resource를 만들지 않는다.
- termination protection 기본값은 true다.
- soak 종료 뒤 graceful shutdown과 final freeze가 끝나기 전 instance를 terminate하지 않는다.
- finalized raw는 compression/decompression SHA와 archive/retention 결정이 완료된 뒤에만 hot buffer에서 정리한다.
- S3 `canonical`은 실제 연구 dataset, holdout, final audit, reproducibility evidence용이다.
- S3 `temporary`는 intermediate/debug artifact용이며 expiration은 기본 비활성이다.
- temporary expiration은 분류·restore·retention test와 별도 review 뒤에만 활성화한다.

## infrastructure 이후 연구 순서

clean soak PASS 뒤 필요한 기간의 prospective data를 별도로 수집한다. 그 다음에만 feature generation → development → robustness → validation → sealed holdout → paper 순서로 진행한다. OBI, aggressive trade imbalance, microprice/queue pressure, spread, volume shock, cross-exchange lead/lag 연구는 clean AWS data 확보 후 별도 범위에서 수행한다.

`AWS infrastructure PASS ≠ Alpha PASS ≠ Paper PASS ≠ Live approval`이다.
