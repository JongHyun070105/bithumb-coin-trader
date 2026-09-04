# AWS 72-HOUR UNATTENDED INFRASTRUCTURE SOAK PLAN

**문서 일자:** 2026-09-04 (UTC)  
**기획 대상:** AWS EC2 환경 72시간 무개입 연속 운용 실증 계획  
**상태:** CANDIDATE PLANNING DRAFT (기동 전 최종 승인 대기)  

---

## 1. 목적 (Purpose)

본 72시간 연속 운용(Soak) 검증의 목적은 다음을 완전 무개입(unattended) 상태로 증명하는 데 있다:
1. **장기 수명 주기 신뢰성:** 259,200초(72시간) 동안 프로세스 충돌, OOM, 누수 없이 멀티 거래소(Bithumb, Binance, Upbit) 웹소켓 피드를 무손실 수집할 수 있는가?
2. **자율적 시간대별 아카이브 스케줄링:** 작업자 개입이나 대화형 세션 없이 매 시간 닫힌 파티션이 정확히 `HH:10:00 UTC`(+600초 유예)에 감지되어 S3 업로드, 체크섬/버전 검증, 복원 검증까지 완료되는가?
3. **감독된 디태치드 풀스캔:** 글로벌 락(동시성=1) 하에서 매 시간대 152개 파일(RAW 76 + ZST 76)의 스트리밍 풀스캔이 타임아웃 없이 순차 완료되고 백로그가 안정적으로 해소되는가?
4. **오너십 및 보안 불변성:** 120M에서 발생했던 오너십 위반(Deviation A)과 풀스캔 타임아웃(Deviation B)이 원천 배제되었는가?

---

## 2. 72H 검증이 증명하는 것과 증명하지 못하는 것

### 2.1 본 검증이 증명하는 것 (What it Proves)
- 네트워크/소켓 복원력, 버퍼 관리, 디스크 I/O 처리 능력.
- 시간대 회전(Cross-Hour Rotation) 및 자율 아카이브 파이프라인의 72회 연속 성공.
- 선형 스트리밍 검증기(`scan_jsonl`)의 고속 무결성 감사 지속성.
- Fail-Closed 원칙에 입각한 무조작 데이터 수집.

### 2.2 본 검증이 증명하지 못하는 것 (What it DOES NOT Prove)
- **알파 모델 유효성:** 본 검증은 순수 인프라/데이터 파이프라인 실증이며, 거래 알파나 신호의 수익성을 증명하지 않음 (**ALPHA: BLOCKED**).
- **페이퍼 트레이딩 완료:** 모의 주문 체결 엔진 검증은 수행하지 않음 (**PAPER: NOT STARTED**).
- **라이브 트레이딩 허용:** 실계좌 주문 권한은 비활성화 유지 (**LIVE: DISABLED**).
- **임의 네트워크 확장 안전성:** 바이낸스 443 외의 비표준 포트(9443 등) 확장의 안전성을 증명하지 않음.

---

## 3. 후보 런타임 및 메타데이터 (Provenance & Metadata)

- **Authoritative Base Frozen Runtime:** `f98abcabbda45bc673702c7a66344a4dcff7299c`
- **Candidate Runtime Code Commit:** `9532cebc902856d954bf80b51dbe567b543dc8e2`
- **Candidate 72H Epoch:** `aws-72h-soak-20260904-43e79055`
- **Candidate 72H Run ID:** `aws-72h-soak-run-20260904T151622Z-43e79055`
- **Candidate Config Fingerprint:** `a023fb5723830c38a7f7d47f2439334fcb44d2c6559939dba7a7cb1c2f88783b`
- **Runtime Seal File:** `infra/aws/seals/aws-72h-soak-20260904.runtime.json`
- **Runtime Duration:** 259,200초 (정확히 72.0시간)

---

## 4. 거래소 피드 구성 (Feed Universe)

기존 승인된 공개 피드 구성을 100% 엄격히 유지함 (확장 금지):
- **Bithumb (20 Markets):** KRW-BTC, KRW-ETH, KRW-XRP, KRW-SOL, KRW-DOGE, KRW-ADA, KRW-XLM, KRW-LINK, KRW-AVAX, KRW-BCH, KRW-ETC, KRW-NEAR, KRW-SUI, KRW-APT, KRW-TRX, KRW-SHIB, KRW-SAND, KRW-MANA, KRW-AXS, KRW-DOT
- **Binance (4 Symbols):** btcusdt, ethusdt, solusdt, xrpusdt (오직 443 포트 사용)
- **Upbit (4 Markets):** KRW-BTC, KRW-ETH, KRW-SOL, KRW-XRP

---

## 5. 아키텍처 구성 (Supervisor & Archive Architecture)

```
Transient 72H Supervisor (systemd-run, RuntimeMaxSec=259260s)
    |
    +-- Collector Process (duration=259200s, uid=bitcoin-trader)
    |
    +-- Metrics Publisher (every 60s -> CloudWatch BitcoinTrader/Collector)
    |
    +-- Closed-Hour Archive Scheduler (polls every 30s)
            |
            +-- Archive Orchestrator (flock exclusive, concurrency=1)
                    |
                    +-- Detached Full-Scan Supervisor (setsid, wall-clock timeout=1800s)
                            |
                            +-- Linear Streaming Scanner (audit_raw_integrity_offline)
```

- **Collector Launch Mode:** `bounded-transient-systemd`
- **Full-Scan Architecture:** `supervised-detached-setsid`
- **Scheduler Grace Period:** 600초 (매 정시 10분에 폐쇄 파티션 감지)
- **Archive Concurrency:** 1 (순차 처리, 락 경합 방지)
- **Full-Scan Concurrency:** 1 (글로벌 커널 flock `.full_scan_runner.lock`)
- **Cleanup Mode:** `CLEANUP_OFF` (원천 RAW 로컬 보존, S3 업로드 후에도 삭제 금지)

---

## 6. 시간대별 기대 코호트 계산 (Hourly Expected Cohorts)

후보 시작 윈도우: `HH:40:00 UTC` -> `HH:40:00 UTC` (3일 후)
- **경과 시간:** 259,200초 (정확히 72.0시간).
- **교차하는 UTC 정시 경계:** 정확히 **72회**.
- **정상 종료 및 아카이브 대상 폐쇄 코호트:** **72개 시간대** (각 시간대별 76 RAW + 76 ZST = 총 10,944 파일).
- **최종 활성 부분 시간대 (Partial Hour):** **1개** (마지막 40분 수집분, 76 RAW 파일).
  - 해당 파티션은 종료 시점에 정시 경계를 넘지 않았으므로 **RAW 상태로 보존되며 아카이브되지 않음**.
- **총 로컬 RAW 파일:** $72 \times 76 + 76 = 5,548$ 파일.
- **총 S3 아카이브 ZST 파일:** $72 \times 76 = 5,472$ 파일.

---

## 7. 성능 및 용량 분석 (Performance & Capacity Gates)

### 7.1 풀스캔 성능 실측 벤치마크
- **실측 처리 속도:** **93,922 records/sec**, **100.94 MB/sec**.
- **시간당 평균 스캔 소요 시간:** **16.19초** (1시간 코호트 152개 파일 기준).
- **시간당 가동률 (Utilization Ratio):** **0.0045 (0.45%)** (기준치 <0.25 대비 압도적 우수).
- **3x 스트레스 가동률:** **0.0135 (1.35%)**.
- **FULL-SCAN CAPACITY GATE:** **PASS (EXCELLENT)**.

### 7.2 EBS 100GiB 용량 모델링 및 게이트 판정
- 현재 사용량: **3.70 GiB** (96.3 GiB 여유).
- **Model A (120M 실측치 ~0.735 GiB/h):** 72H 후 58.88 GiB (58.88%), 70%/80%/90% 도달 없음 -> **PASS**.
- **Model B (V9 이력치 1.00 GiB/h):** 72H 후 78.80 GiB (78.80%), T+63.6h에 70% 경고선 도달 -> **MONITOR**.
- **Model C (1.25x 스트레스 1.25 GiB/h):** 72H 후 97.50 GiB (97.50%), **T+66.2h에 90% 임계선 초과 (FAIL)**.
- **Model D (1.50x 스트레스 1.50 GiB/h):** **T+61.6h에 ENOSPC 완전 고갈**.
- **DISK_CAPACITY_GATE 판정:**
  - **BLOCKED (Unattended Stress Safety Margin Insufficient on 100GiB)**.
  - **권고 조치 (Option B):** 72H 기동 전 루트 EBS 볼륨을 100GiB에서 **200GiB gp3**로 확장 권고 (월 비용 증가분 약 $8.00, 무중단 온라인 확장 가능).

---

## 8. IAM 및 테라폼 변경 패키지 (Privileged Change Package)

### 8.1 IAM Permissions Boundary
- **현재 활성 버전:** `v4` (120M 에포크 `aws-120m-validation-20260904-73d8e43c` 허용).
- **후보 버전:** `v5` (`infra/aws/identity/collector-permissions-boundary-v5-candidate.json`).
- **변경점:** S3 Prefix를 신규 72H 에포크(`aws-72h-soak-20260904-43e79055`)로 교체하는 최소 diff.
- **DeleteObject:** 영구 차단 유지 (action 리스트에서 완전 제외).

### 8.2 Terraform Plan
- **Plan 변수:**
  ```bash
  terraform -chdir=infra/aws plan \
    -var="ami_id_override=ami-08d82cf148c92fcc3" \
    -var="availability_zone=ap-northeast-2a" \
    -var="collector_epoch=aws-72h-soak-20260904-43e79055" \
    -var="collector_run_id=aws-72h-soak-run-20260904T151622Z-43e79055" \
    -var="collector_git_commit=9532cebc902856d954bf80b51dbe567b543dc8e2" \
    -var="collector_config_fingerprint=a023fb5723830c38a7f7d47f2439334fcb44d2c6559939dba7a7cb1c2f88783b"
  ```
- **예상 리소스 변동:** Add: 0, Change: 3 (인라인 정책 및 태그 인플레이스 업데이트), Destroy: 0, Replace: 0.

---

## 9. 품질 판정 게이트 (Pass / Fail Criteria)

1. **프로세스 무결성:** 수집기 72시간 완주, exit code 0, 강제 타임아웃 없음, active_partition_files=[].
2. **라이터 무결성:** WriterErrors = 0, QueueDrops = 0, Unpersisted = 0.
3. **스키마 무결성:** invalid_json = 0, schema_mismatch = 0, missing_required_fields = 0, non_finite_numeric = 0, malformed_timestamps = 0, unknown_market = 0, scan_failures = 0.
4. **아카이브 및 감사:** 72개 폐쇄 코호트 전수 S3 업로드 검증 PASS, 72개 풀스캔 리포트 PASS, DeleteObject = 0.
5. **오너십 및 디스크:** 오너십 위반 0건, 디스크 사용량 90% 미만 유지.

---

## 10. 긴급 정지 및 절차 참조

장애 발생 시 `docs/AWS_72H_SOAK_FAILURE_PLAYBOOK.md`의 시나리오 A~Z에 따라 대응하며, 임의의 데이터 삭제나 에러 무시는 절대 금지된다.
수집기 종료 후 상태는 자동 보존되며, 알파 모델/페이퍼/라이브 트레이딩은 엄격히 차단된다.
