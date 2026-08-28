# V9.1 → AWS handoff — 2026-08-29

## Handoff 판정

- V9: **CLOSED / SOAK PASS / DATA QUALITY FAIL**
- V9.1: **FROZEN LOCAL DEPLOYMENT-READINESS BASELINE**
- AWS: **NOT PROVISIONED**
- Alpha: **BLOCKED**
- Live: **DISABLED**

이 문서는 provisioning 지시가 아니다. 계정 상태·가격·설계를 확인하고 사용자가 생성 대상을 승인하기 전에는 AWS resource를 만들지 않는다.

## V9에서 발견한 문제

- Binance orderbook combined-stream identity 유실로 `UNKNOWN` market 9,417,381건
- local receive wall-clock reversal 277건, monotonic clock 전 record 누락
- partition-local duplicate trade IDs 198건
- exchange/local timestamp offset outlier 118,186건
- durable queue/drop/reconnect/writer counter 부재
- feed completeness와 replay determinism 검증 불가
- launch-time exact in-memory source fingerprint 검증 불가

V9 raw와 V9의 assumptions는 alpha, causal ordering, completeness 또는 live readiness 증거로 재사용하지 않는다.

## V9.1에서 해결하거나 강화한 항목

- Binance stream symbol 보존과 `UNKNOWN` 제거
- wall-clock + monotonic receive timestamp, collector run ID
- durable connection/queue/backpressure/drop/writer metrics
- writer fail-closed와 unpersisted-event accounting
- bounded queue와 graceful drain
- atomic metrics/manifest replacement
- manifest schema v4와 stale/path/size validation
- bounded balanced status sampling, offset outlier count, corrected disk projection

120초 isolated validation은 13,211 records와 7/7 manifests에서 해당 기본 동작을 확인했다. 이는 **LOCAL DEPLOYMENT-READINESS SHORT VALIDATION PASS**이지 장기 soak PASS가 아니다.

## 아직 검증되지 않은 것

- 실제 network failure 뒤 reconnect와 subscription recovery의 장기 정확성
- host/process crash 시 raw append record atomicity와 fsync durability
- 장시간 queue pressure와 disk-full 복구
- raw → normalized replay → feature output의 deterministic lineage
- compression/decompression 후 record count와 SHA 보존
- S3 upload/download 및 partial-transfer 복구
- AWS NTP/clock offset, scheduler, filesystem, AZ/network 특성
- exchange feed completeness와 true one-way latency
- alpha/paper/live readiness

## AWS가 별도 epoch인 이유

AWS에서는 network path, RTT, ISP, host clock/NTP, OS/kernel, scheduler, filesystem/write latency, instance architecture, availability zone, reconnect 특성이 모두 바뀐다. Mac V9/V9.1과 데이터를 환경 구분 없이 합치면 receive-time과 장애 특성의 provenance가 깨진다.

새 epoch metadata에는 최소 다음을 포함한다.

- `collector_epoch`, `environment_id`, `region`, `availability_zone`
- `instance_type`, `architecture`, `host_id`, `run_id`
- `collector_version`, `git_commit`, `config_fingerprint`, `schema_version`
- `clock_source`, `process_start`

epoch 명칭은 provisioning 승인 시 고정하며 예시 `V9.2-AWS`를 확정값으로 미리 박지 않는다.

## AWS 직전 prerequisite checklist

- [ ] 실제 Billing/Credits에서 현재 잔액과 만료일 확인
- [ ] credit eligible services 확인
- [ ] `ap-northeast-2` 최신 EC2 가격 확인
- [ ] EBS, snapshot, S3, data transfer, CloudWatch 예상 비용 계산
- [ ] EC2 instance 후보와 steady-state CPU/memory/network 비교
- [ ] ARM64/x86_64 Python dependency compatibility 확인
- [ ] finalized copy를 사용한 compression/decompression SHA benchmark
- [ ] S3 archive naming, checksum, multipart failure, restore protocol 설계
- [ ] least-privilege IAM, SSM access, credential rotation 설계
- [ ] log/metric retention과 budget alarm 설계
- [ ] Terraform plan 작성 및 review; apply는 별도 승인
- [ ] AWS epoch naming과 immutable environment/config fingerprint 확정
- [ ] 생성 resource·월 예상 비용·중단/삭제 절차에 대한 사용자 승인
- [ ] AWS deployment smoke와 graceful shutdown 검증
- [ ] AWS clean 72h infrastructure soak
- [ ] soak 후 FULL-SCAN audit와 Mac baseline 비교

참고로 과거 대화의 약 US$114 credit와 2026-12-13 만료 정보는 오래될 수 있는 사용자 제공 snapshot이다. provisioning 판단의 static truth로 사용하지 않는다.

## AWS에서 반드시 재검증할 항목

1. 세 거래소별 connection/reconnect/disconnect와 subscription continuity.
2. exchange-labelled timestamp와 local wall/monotonic receive 분포.
3. writer throughput, queue high-water/backpressure/drop/unpersisted counter.
4. disk latency, free-space projection, log/metric persistence.
5. manifest v4 coverage, raw SHA, path/size/schema/duplicate/reversal/outlier count.
6. process manager restart policy와 run/epoch boundary.
7. S3 archive/restore byte equality와 partial failure recovery.
8. 72시간 종료 후 fail-closed final ledger.

AWS 이전은 live trading 승인이 아니다. AWS soak가 성공하더라도 alpha 연구와 paper/live 승격에는 별도의 데이터·재현성·전진 검증 gate가 필요하다.
