# 72H Acceptance Criteria (2026-09-05)

> [!IMPORTANT]
> **These criteria were frozen while the outcome of the 72H soak was still unknown to this offline workstream.**
>
> 본 문서는 실제 실행 중인 AWS 72시간 무중단 공공 시장 데이터 수집(Soak) 파이프라인의 결과나 실시간 텔레메트리를 일절 확인하지 않은 상태에서, 사전에 고정된 런타임 코드(`9532cebc902856d954bf80b51dbe567b543dc8e2`), 프로토콜, 이전 45분/120분 검증 교훈, 테라폼 및 IAM 봉인 정의를 바탕으로 작성되었습니다. 사후 결과에 맞추어 판정 기준을 완화하거나 변경하는 것을 방지하기 위해 작성 직후 SHA-256 해시를 생성하여 커밋으로 사전 봉인합니다.

---

## 1. 개요 및 권위적 봉인 기준선 (Authoritative Sealed Baseline)

- **런타임 소프트웨어 커밋 (Runtime Code Commit):** `9532cebc902856d954bf80b51dbe567b543dc8e2`
- **수집 에포크 (Collector Epoch):** `aws-72h-soak-20260905-8017b83e`
- **수집 실행 식별자 (Collector Run ID):** `aws-72h-soak-run-20260905T024039Z-8017b83e`
- **정규 런타임 설정 지문 (Canonical Config Fingerprint):** `a023fb5723830c38a7f7d47f2439334fcb44d2c6559939dba7a7cb1c2f88783b`
- **런타임 설정 봉인 파일 SHA-256:** `cb3dee0331cebed2ede5b43a0092fad0b2aad0989be63f7666d3e6547a66c11c` (`infra/aws/seals/aws-72h-soak-20260905.runtime.json`)
- **목표 수집 지속 시간 (Target Duration):** `259,200초` (정확히 72시간 0분 0초)
- **피드 유니버스 (Feed Universe):**
  - Bithumb 20개 마켓 (`orderbook`, `trade`, `ticker`) = 시간당 60개 파일
  - Binance 4개 마켓 (`orderbook`, `trade`, 공식 웹소켓 443 포트 전용) = 시간당 8개 파일
  - Upbit 4개 마켓 (`orderbook`, `trade`) = 시간당 8개 파일
  - **시간당 폐쇄 파티션 파일 총합:** 정확히 **76개** 파티션 (`.jsonl`)
  - **72시간 총 폐쇄 파티션 기대치:** 71개 완전 폐쇄 시간대 * 76 = 최소 5,396개 폐쇄 파티션 (+ 시작/종료 부분 시간대)

---

## 2. 하드 패스 요건 (HARD PASS REQUIREMENTS)

72시간 Soak 수집 전체가 성공으로 최종 승인되기 위해 반드시 **모두** 만족해야 하는 필수 조건입니다.

1. **지속 시간 완전성 (Full Intended Duration):**
   - 수집기가 중간 중단 없이 의도된 259,200초(72시간) 동안 동작 완료.
2. **프로세스 정상 종료 (Normal Exit Behavior):**
   - 수집기 프로세스(`run_cross_market_collector.py`) 종료 코드가 `0`.
   - 메트릭 발행기(`publish_collector_metrics.py`) 종료 코드가 `0`.
   - 바운디드 슈퍼바이저(`run_bounded_short_smoke.py`) 종료 코드가 `0`.
3. **최종 메트릭 및 라이프사이클 무결성 (Final Metrics & Lifecycle):**
   - `collector_metrics.json` 내 `process_id`가 실제 수집기 PID와 일치하고 `schema_version = 1`.
   - `active_partition_files`가 종료 시점에 빈 배열(`[]`)로 정상 플러시.
   - `collector-lifecycle.json` 내 `final_manifest_flush_observed = true`.
4. **무결손 데이터 레코드 (Zero Data Loss):**
   - `WriterErrors = 0` (누적 0건).
   - `QueueDrops = 0` (누적 0건).
   - `UnpersistedRecords = 0` (누적 0건).
5. **프로비넌스 일관성 (Provenance Invariant):**
   - 게스트 실행 코드 커밋, S3 메타데이터, `result.json`, `collector_metrics.json` 내 모든 에포크, 런 ID, 지문, 커밋 SHA가 봉인된 값과 100% 일치.
6. **시간 단조성 및 스키마 검증 (Timestamp Monotonicity & Schema Integrity):**
   - 모든 RAW 및 복원 레코드가 v4 스키마 규칙을 100% 준수.
   - 파티션 파일 내 수신 단조 타임스탬프(`receive_ts_mono_ns`)의 역전(Reversal) 건수가 0건.
7. **아카이브 및 무손실 복원 동등성 (Lossless Archive Equivalence):**
   - 폐쇄 시간대 파티션에 대한 S3 업로드 아티팩트가 zstd 레벨 1 압축을 준수하고 영구 보존.
   - S3 원격 복원 스트림의 SHA-256 및 바이트 수가 로컬 원본 RAW 파일과 100% 비트 단위 일치.
8. **풀스캔 최종 상태 (Full-Scan Terminal State):**
   - 모든 폐쇄 코호트에 대한 오프라인 풀스캔 보고서(`full_scan_*.json`)의 최종 판정이 `PASS`.
   - 검사된 모든 레코드의 상태가 정상(Corrupted/Truncated 레코드 0건).
9. **소유권 및 보안 불변식 (Ownership & Security Invariants):**
   - 모든 로컬 런타임 데이터 및 영구 디렉토리의 소유권이 `bitcoin-trader:bitcoin-trader`, 권한 `0700` 유지 (Root 소유 파일 발생 0건).
   - S3 `DeleteObject` API 호출 0건.
   - 보안 그룹 인바운드 규칙 0개 유지 (SSH 없음).

---

## 3. 하드 페일 조건 (HARD FAIL CONDITIONS)

다음 항목 중 **단 1개라도** 발생하는 경우, 72시간 Soak은 즉시 **HARD FAIL**로 판정됩니다.

1. **데이터 유실 및 드롭:**
   - `WriterErrors > 0`
   - `QueueDrops > 0`
   - `Unpersisted > 0`
2. **프로세스 비정상 중단:**
   - 수집기 프로세스가 SIGSEGV, SIGBUS, OOM, 비정상 예외(Exit code != 0)로 중단된 경우.
   - 일회성 systemd transient unit이 타임아웃 전에 실패 상태(`failed`)로 전이된 경우.
3. **아카이브 무결성 파괴:**
   - 원본 RAW 파일과 S3 복원 스트림 간의 SHA-256 불일치.
   - 활성 파티션(Active Partition, 아직 작성 중인 현재 시간대 파일)이 아카이브에 포함되거나 압축된 경우.
   - 파티션 매니페스트 SHA-256과 실제 파일 해시의 불일치.
4. **프로비넌스 오염:**
   - 커밋 해시 != `9532cebc902856d954bf80b51dbe567b543dc8e2`.
   - 에포크 != `aws-72h-soak-20260905-8017b83e`.
   - 런 ID != `aws-72h-soak-run-20260905T024039Z-8017b83e`.
   - 런타임 설정 지문 != `a023fb5723830c38a7f7d47f2439334fcb44d2c6559939dba7a7cb1c2f88783b`.
5. **디스크 임계치 초과 및 파괴적 조치:**
   - 디스크 사용률이 `Critical Threshold (90%)`를 초과하여 비상 조치가 발동된 경우.
   - 비인가된 로컬 RAW 파일 삭제(`cleanup_verified = true` 오발동) 발생.
   - S3 `DeleteObject` 호출 발생.
6. **네트워크 프로토콜 위반:**
   - 비인가된 포트(예: Binance 9443)로의 연결 시도.
   - Bithumb 비인가 Private API 호출 또는 주문 API 호출 시도.
7. **재연결 폭풍 (Reconnect Storm) — [PREDECLARED TODAY BEFORE RESULT INSPECTION]:**
   - 단일 피드에서 5분 이내 연속 10회 이상의 웹소켓 재연결 실패가 발생하고 정상 복구가 불가한 경우.

---

## 4. 운영 편차 판정 기준 (DEVIATION CONDITIONS)

시스템이 중단되지는 않았으나 정해진 자율 운영 원칙에서 벗어난 경우로, **OPERATIONAL DEVIATION**으로 명시하고 최종 판정 보고서에 별도 기록해야 합니다.

1. **아카이버 수동 개입 (Manual Archive Invocation):**
   - 폐쇄 시간대 아카이브 스케줄러(`ClosedHourArchiveScheduler`)가 스스로 동작하지 못하여 운영자가 직접 스크립트나 커맨드로 아카이브를 강제 실행한 경우.
2. **풀스캔 수동 재실행:**
   - 자동 풀스캔 워커가 타임아웃 또는 락 경합으로 실패하여 수동으로 재실행한 경우.
3. **일시적 단일 웹소켓 재연결:**
   - 거래소 점검 또는 네트워크 일시적 지연으로 웹소켓 재연결이 1~2회 발생하였으나 데이터 누락(QueueDrop/WriterError) 없이 정상 복구된 경우 (자연스러운 네트워크 이벤트로 분류).
4. **클라우드워치 메트릭 지연:**
   - CloudWatch PutMetricData 호출이 AWS 엔드포인트 스로틀링 등으로 일시 지연되었으나 로컬 메트릭 파일 및 로그가 보존된 경우.

---

## 5. 경고 조건 (WARNING CONDITIONS)

1. **디스크 경고 (Warning Threshold 70%):**
   - 72시간 중 디스크 사용률이 70%를 상회한 경우 (200 GiB 증설로 인해 정상 운영 시 30% 미만 예상되나 모니터링 경고 대상).
2. **풀스캔 큐 누적 (Full-Scan Backlog):**
   - 폐쇄 코호트 풀스캔 소요 시간이 1시간을 초과하여 다음 코호트 풀스캔이 대기열에 쌓인 경우.
3. **타임스탬프 스큐 (Clock Skew):**
   - Amazon Time Sync Service와의 동기화 오프셋이 일시적으로 10밀리초(10ms)를 초과한 경우.

---

## 6. 증거 불충분 / 미확인 판정 기준 (UNKNOWN / INSUFFICIENT EVIDENCE)

1. **텔레메트리 누락:**
   - 특정 시간대의 메트릭 JSON 또는 로그 파일이 손상되어 WriterErrors, QueueDrops 여부를 입증할 수 없는 경우, **PASS로 간주하지 않고 UNKNOWN으로 보고**.
2. **거래소 체결 ID 유일성:**
   - 거래소(Bithumb/Upbit/Binance)의 API 자체 결함으로 체결 ID가 없거나 불명확하여 정합성 검증이 불가능한 경우 `SEMANTICS_UNRESOLVED`로 분류.

---

## 7. 정량적 임계치 사전 선언 (Predeclared Threshold Summary)

| 항목 | 사전 선언 임계치 | 판정 구분 | 근거 및 사유 |
| :--- | :--- | :--- | :--- |
| **Duration** | 259,200s (±10s) | HARD PASS / FAIL | 72시간 정밀 완주 검증 |
| **WriterErrors** | = 0 | HARD FAIL if > 0 | 데이터 파이프라인 무결손 기본 원칙 |
| **QueueDrops** | = 0 | HARD FAIL if > 0 | 수집기 내부 버퍼 용량 초과 금지 |
| **Unpersisted** | = 0 | HARD FAIL if > 0 | 디스크 미기록 방지 |
| **Active Partition Archive** | = 0건 | HARD FAIL if > 0 | 진행 중인 파일 압축 금지 |
| **RAW vs Restored SHA** | 100% Match | HARD FAIL if mismatch | 아카이브 무손실 보존 검증 |
| **Partitions per Hour** | 정확히 76개 | HARD PASS / FAIL | Bithumb 20*3 + Binance 4*2 + Upbit 4*2 |
| **Max Reconnect/5min** | 10회 | HARD FAIL if exceeded | 거래소 차단 및 폭풍 방지 (Predeclared Today) |
| **Disk Critical** | 90% | HARD FAIL if exceeded | 파일시스템 고갈 방지 |
| **Full-Scan Status** | ALL PASS | HARD PASS / FAIL | 파티션 전수 데이터 무결성 입증 |

---

*이 문서는 72H Soak 실행 결과가 관측되지 않은 2026-09-05 시점에 오프라인 연구 하드닝 스프린트의 최우선 작업으로 사전 작성 및 봉인되었습니다.*
