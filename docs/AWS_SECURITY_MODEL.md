# AWS security model

## 기본 경계

- Internet ingress: **NONE**
- SSH 22: **CLOSED**
- Dashboard/API public exposure: **NONE**
- 운영 접속: **SSM Session Manager ONLY**
- EC2 AWS authentication: instance role temporary credentials
- Repository/Terraform/user-data static AWS key: **PROHIBITED**
- Exchange private credential: public market-data soak에서는 **NOT REQUIRED**
- Alpha/live control: **NOT DEPLOYED**

Session Manager는 inbound port, bastion, SSH key 없이 관리 접속을 제공한다: <https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager.html>. EC2 workload는 instance profile을 통해 임시 credential을 받아야 한다: <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/iam-roles-for-amazon-ec2.html>.

## network

- collector는 outbound WebSocket/HTTPS와 AWS API 사용을 위해 public IPv4를 가진다.
- security group은 ingress block이 전혀 없고 TCP/443 outbound만 허용한다.
- subnet route는 Internet Gateway를 향하지만 inbound allow rule이 없으므로 SSH/dashboard는 도달할 수 없다.
- public dashboard, load balancer, NAT Gateway, RDS, open SSH는 계획에 포함하지 않는다.
- 실제 plan review에서 SG ingress 개수 0과 port 22 부재를 다시 검사한다.

## instance hardening

- Amazon Linux 2023 공식 AMI만 architecture별로 조회한다.
- IMDSv2 token을 강제하고 hop limit 1을 사용한다.
- root gp3는 암호화한다.
- termination protection을 기본 활성화한다.
- Terraform은 collector를 자동 실행하거나 live control을 설치하지 않는다.
- Amazon Time Sync Service와 chrony 상태를 launch gate에서 검사한다.
- 100 GiB gp3는 영구 archive가 아니라 최근 raw와 compression staging을 위한 hot buffer다.
- disk-used 70/80/90% alarm은 경고/고위험/fail-closed 보호 신호이며 검증되지 않은 raw 삭제 트리거가 아니다.

## IAM 최소 권한

instance role은 다음만 허용한다.

- `AmazonSSMManagedInstanceCore`를 통한 SSM agent 통신
- 해당 epoch S3 prefix의 List/Get/Put/multipart abort
- `BitcoinTrader/Collector` namespace의 metric publish
- 지정 operational log group의 stream 생성/기록

다음은 허용하지 않는다.

- 다른 bucket/prefix 접근
- IAM, EC2, budget resource 변경
- S3 bucket policy/ACL 변경
- wildcard secret 읽기
- trading/account/order API credential

public market-data AWS soak에는 secret이 필요하지 않으므로 Secrets Manager resource를 만들지 않는다. 나중에 private credential이 필요하면 별도 threat/cost review 후 secret ARN을 명시하고 그 ARN의 `GetSecretValue`만 추가한다.

## S3 data protection

- Block Public Access 네 설정 모두 true
- BucketOwnerEnforced ownership
- default SSE-S3 encryption
- TLS가 아닌 요청을 bucket policy로 거부
- versioning 활성화
- `canonical/<epoch>`와 `temporary/<epoch>` prefix별 instance-role 권한
- canonical transition과 temporary expiration 모두 기본 비활성
- temporary expiration은 명시적 flag, 최소 7일, 별도 review가 있어야만 plan에 나타남

AWS는 Block Public Access를 account와 bucket 수준에서 함께 사용하는 것을 권장한다: <https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-control-block-public-access.html>. 이 Terraform은 bucket 수준을 설정하며 account 수준은 provider-backed plan 전에 read-only로 확인한다.

S3 객체 자체만으로 성공을 주장하지 않는다. local SHA, manifest SHA, S3 checksum/ETag semantics, restored byte SHA를 분리해서 검증한다.

data classification:

- `PERMANENT / RESEARCH EVIDENCE`: 실제 전략 연구 dataset, holdout, final audit, reproducibility artifact. canonical prefix에 보관한다.
- `TEMPORARY / OPERATIONAL`: 이미 처리된 intermediate raw, short-lived debug, 재현에 불필요한 중복 artifact. temporary prefix에 분리하며 검증된 retention policy로만 삭제한다.

collector instance role에는 `DeleteObject`가 없다. local uncompressed raw 역시 finalize, manifest/raw SHA, compression, decompression equality, archive/retention decision이 모두 끝나기 전에는 정리하지 않는다.

## logging과 data minimization

CloudWatch에는 다음만 보낸다.

- process health와 uptime
- reconnect/disconnect
- queue depth/high-water/drop/backpressure
- writer errors/unpersisted count
- disk free/throughput
- finalized/uploaded/restore verification counts

raw orderbook/trade payload, API key, account ID, holdings, order data는 operational log에 넣지 않는다. 로그 retention 기본값은 14일이다.

## Terraform/state 보안

Git 제외 대상:

- `.terraform/`
- `*.tfstate`, `*.tfstate.*`
- `*.tfplan`
- crash logs
- `terraform.tfvars`, `*.auto.tfvars*`
- credential 파일

현재 단계에서는 local backend도 state를 생성하지 않도록 `terraform init -backend=false`와 validate만 수행한다. provider-backed plan이 승인되면 state backend/encryption/locking은 별도 review 대상이다. plan/state는 secret이 아니라고 가정하지 않는다.

## fail-closed 조건

다음 중 하나면 launch 또는 soak promotion을 중단한다.

- AWS identity/credit/cost 미확인
- provenance 값 미봉인
- SG ingress 존재 또는 Session Manager 실패
- architecture smoke 미통과
- clock source/chrony 비정상
- writer/unpersisted/drop counter 증가
- raw/manifest/S3 restore SHA 불일치
- disk critical 상태에서 verified cleanup/expansion 경로 부재
- secret scan finding
- Terraform plan이 미검증 또는 review되지 않음

## Terraform static security review

2026-08-29 Trivy 0.74.0 IaC scan은 5개 finding을 냈다. 이는 숨기거나 PASS로 재분류하지 않고 provisioning review 입력으로 유지한다.

| ID / severity | Finding | 현재 판단 | provisioning 전 선택지 |
|---|---|---|---|
| AVD-AWS-0104 / CRITICAL | TCP/443 egress가 `0.0.0.0/0` | **ACCEPTANCE REQUIRED**. exchange/CDN과 AWS public endpoint IP가 동적이라 고정 CIDR allowlist는 안정적이지 않다. ingress는 여전히 0이다. | 현재 port-only egress 수용 또는 비용을 포함한 egress proxy/firewall 설계 |
| AVD-AWS-0132 / HIGH | S3가 customer-managed KMS key 미사용 | **ACCEPTANCE REQUIRED**. public market data이고 SSE-S3/TLS/block-public-access를 사용한다. | SSE-S3 수용 또는 KMS key/API 비용을 추가한 SSE-KMS |
| AVD-AWS-0178 / MEDIUM | VPC Flow Logs 미사용 | **ACCEPTANCE REQUIRED**. inbound 0인 단일 노드 최소 구성이고 log ingest 비용을 피한다. | 초기 수용 또는 별도 low-volume flow log/retention 설계 |
| AVD-AWS-0017 / LOW | CloudWatch log group CMK 미사용 | **ACCEPTANCE REQUIRED**. operational-only이며 raw/secret/account data를 금지한다. | AWS-managed encryption 수용 또는 CMK 추가 |
| AVD-AWS-0089 / LOW | S3 server access logging 미사용 | **ACCEPTANCE REQUIRED**. logging bucket과 추가 object/cost를 만들지 않은 초기안이다. | CloudTrail data event 또는 별도 logging bucket 설계 |

현재 Terraform은 비용과 단순성 요구 때문에 이 5개를 자동 수정하지 않는다. 사용자 provisioning review에서 수용 또는 강화 방향을 선택해야 한다. 어떤 선택도 AWS resource apply를 자동 승인하지 않는다.
