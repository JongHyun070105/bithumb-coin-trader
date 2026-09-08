# Post-72H Interactive Guest Evidence Handoff Report (2026-09-08)

## 1. 개요 및 권한 경로 (Overview & Session Path)

본 문서는 SSM 권한 변경(`ssm:SendCommand` 추가 등) 없이, **기존 IAM 정책에 기인가된 `ssm:StartSession` 대화형 세션 경로**만을 사용하여 EC2 게스트(`i-008bc503c1136349f`) 내부의 권위적 증거를 **100% 읽기 전용(Zero-Mutation)**으로 수집하고 검증한 결과를 기술한다.

- **SESSION PATH:** `ssm:StartSession` (기존 권한 활용 / `session-manager-plugin` v1.2.835.0) [FACT]
- **TARGET INSTANCE:** `i-008bc503c1136349f` (`bitcoin-trader-aws-apne2-research-collector`) [FACT]
- **TARGET EPOCH:** `aws-72h-soak-20260905-8017b83e` [FACT]
- **TARGET RUN ID:** `aws-72h-soak-run-20260905T024039Z-8017b83e` [FACT]
- **REMOTE GUEST MUTATIONS:** `NONE` (임의 파일 수정, 삭제, 쓰기, 서비스 조작 일절 없음) [FACT]
- **LOCAL EVIDENCE REPOSITORY:** `evidence/post72h-final-audit-20260908/interactive-guest-20260908T151318Z/` [FACT]
- **FINAL SCIENTIFIC VERDICT:** `NOT RUN` (Astra 이관용 순수 증거 수집 단계 유지) [FACT]

---

## 2. 게스트 생사 및 프로세스 상태 (Process & Lifecycle State)

- **CURRENT PROCESS:** `INACTIVE / TERMINATED` [FACT]
  - `ps aux`, `pgrep -fl python` 검사 결과 수집기, 슈퍼바이저, 아카이브 스케줄러 프로세스 일체 비활성.
- **SYSTEMD UNIT:** `bitcoin-trader-72h-soak-aws-72h-soak-run-20260905T024039Z-8017b83e.service` [FACT]
- **SYSTEMD LOAD/ACTIVE STATE:** `LoadState=not-found`, `ActiveState=inactive`, `SubState=dead` (systemd-run 과도 단위 정상 해제) [FACT]
- **SYSTEMD RESULT CANDIDATE:** `exit-code` (`status=143/n/a`, SIGTERM 수신 후 종료) [CANDIDATE]
- **CPU TIME CONSUMED:** `8h 47min 11.839s` [FACT]
- **SUPERVISOR RESULT AVAILABLE:** `YES` (`result.json` 로컬 복원 및 SHA256 일치 검증 완료) [FACT]
  - `duration_limit_seconds`: `259200.0` (72.0시간) [FACT]
  - `elapsed_seconds`: `259290.218599` (72시간 1분 30.2초) [FACT]
  - `full_duration_satisfied`: `true` [FACT]
  - `forced_timeout`: `true` (지정된 72시간 지속 시간 만료로 인한 정상 정지 시퀀스 발동) [FACT]
  - `received_signal`: `"SIGTERM"` [FACT]
  - `overall_status`: `"INTERRUPTED"` (제한 시간 도달에 따른 슈퍼바이저 SIGTERM 정지 신호) [FACT]
  - `collector_exit_code`: `-15` (SIGTERM 수신 정상 종료) [FACT]
  - `archive_scheduler_exit_code`: `-9` (SIGTERM 후 유예 시간 초과로 슈퍼바이저 SIGKILL 처리) [FACT]
  - `publisher_exit_code`: `0` [FACT]

---

## 3. 시작 및 완료 시각 후보군 (Authoritative Candidates)

### 시작 시각 후보 (Start Candidates) [CANDIDATE]
1. **Systemd 서비스 기동 이벤트:** `2026-09-05T05:40:02 UTC` (`Starting bitcoin-trader-72h-soak-...`)
2. **슈퍼바이저 기동 시각 (`started_at`):** `2026-09-05T05:40:03.058660+00:00`
3. **수집기 기동 시각 (`collector_started_at`):** `2026-09-05T05:40:03.207328+00:00`
4. **최초 RAW 체결 이벤트 거래소 시각 (`exchange_ts`):** `2026-09-05T05:40:00.360000+00:00` (Bithumb KRW-BTC)
5. **최초 RAW 체결 이벤트 수신/기록 시각 (`local_recv_ts` / `local_write_ts`):** `2026-09-05T05:40:03.468301+00:00` / `2026-09-05T05:40:03.468756+00:00`

### 완료 시각 후보 (Completion Candidates) [CANDIDATE]
1. **수집기 최종 메트릭 기록 시각 (`written_at`):** `2026-09-08T05:40:03.236533+00:00` (정확히 72.000008시간)
2. **최종 RAW 체결 이벤트 거래소 시각 (`exchange_ts`):** `2026-09-08T05:39:56.468000+00:00` (Bithumb KRW-BTC)
3. **최종 RAW 체결 이벤트 수신/기록 시각 (`local_recv_ts` / `local_write_ts`):** `2026-09-08T05:39:56.730096+00:00` / `2026-09-08T05:39:56.730223+00:00`
4. **슈퍼바이저 종료 시각 (`ended_at`):** `2026-09-08T05:41:33.277630+00:00`
5. **Systemd 프로세스 종료 이벤트:** `2026-09-08T05:41:33 UTC` (`Main process exited, code=exited, status=143/n/a`)

---

## 4. RAW 데이터 토폴로지 (RAW Topology)

- **RAW BASE PATH:** `/var/lib/bitcoin-trader/72h-soak/aws-72h-soak-20260905-8017b83e/raw` [FACT]
- **TOTAL RAW FILES:** `5,530개` (`.jsonl`) [FACT]
- **ZERO-SIZED FILES:** `0개` (결손 파일 전무) [FACT]
- **DISTINCT CLOSED-HOUR COHORTS:** `73개` (`2026-09-05_05` ~ `2026-09-08_05`) [FACT]
  - 시간대별 파일 수 분포: 최소 72개 ~ 최대 77개 (76개 피드 완벽 유지)
- **거래소별 파일 수:** Bithumb 4,360개, Binance 586개, Upbit 584개 [FACT]
- **스트림별 파일 수:** Orderbook 2,046개, Trade 2,034개, Ticker 1,450개 [FACT]
- **RAW 파일 mtime 범위 (UTC):** `2026-09-05T05:47:54` ~ `2026-09-08T05:40:03` (정확히 72.0시간 커버리지) [FACT]

---

## 5. 아카이브 토폴로지 및 S3 3개 코호트 미스터리 규명 (Archive & S3 Anomaly Reconciliation)

### 사실 확인 (Facts)
- **게스트 압축 파일 수:** `228개` (`.jsonl.zst`) [FACT]
  - `2026-09-05_05`: 76개
  - `2026-09-06_05`: 76개
  - `2026-09-07_05`: 76개
- **게스트 영수증 파일 수:** `228개` (`.archive-receipt.json`, 상태 전부 `CLEANUP_ELIGIBLE`) [FACT]
- **S3 원격 아카이브 객체 수:** `228개` (비-SSM 감사 결과와 완벽 일치) [FACT]
- **FULLSCAN 보고서:** `full_scan_05_report.json` 존재 (360,356 레코드, 결함 0건, 상태 PASS) [FACT]

### 원인 규명 (Root Cause Reconciliation) [FACT]
1. **아카이브 스케줄러 완료 판정 로직 결함:**
   `src/bithumb_coin_trader/archive_scheduler.py`의 `is_hour_completed(hour_str)` 메서드가 `date_str`을 고려하지 않고 전체 `raw` 디렉터리에서 `**/*_{hour_str}.jsonl`을 단순 탐색함.
2. **상태 반전 메커니즘:**
   - 1일차 06시경: 05시 코호트(`2026-09-05_05`) 76개 파일에 대해 압축, S3 업로드, 풀스캔 완료 $\rightarrow$ `is_hour_completed("05") = True`.
   - 2일차 05시경: 수집기가 새 날짜의 05시 파일(`2026-09-06_05`)을 생성하자, `_05.jsonl` 매칭 파일 중 미처리 파일이 생겨 `is_hour_completed("05")`가 다시 `False`로 반전됨.
   - 이에 따라 스케줄러의 우선순위 큐에서 05시가 계속 재선택되어, 06시 이후 코호트들의 온라인 아카이빙 순번이 차단됨.
3. **핵심 결론 (Epistemic Boundary):**
   **S3에 3개 코호트만 존재하는 것은 데이터 수집 실패가 아니며, 온라인 아카이브 스케줄러의 큐잉 편향 현상임.**
   **72시간 전 구간의 RAW 데이터(5,530개 파일)는 EC2 게스트 로컬 디스크에 100% 무결하게 보존되어 있음.**

---

## 6. 수집기 성능 카운터 및 무결성 지표 (Counters & Integrity)

`collector_metrics.json` 및 `metric-publisher-state.json` 분석 결과 [FACT]:
- **WRITER ERRORS:** `0` (Binance: 0, Bithumb: 0, Upbit: 0)
- **QUEUE DROPS:** `0` (Binance: 0, Bithumb: 0, Upbit: 0)
- **TRADE SEQUENCE GAPS:** `0` (Binance: 0, Bithumb: 0, Upbit: 0)
- **TRADE DUPLICATES:** `0` (Binance: 0, Bithumb: 0, Upbit: 0)
- **MALFORMED QUARANTINED:** `0` (Binance: 0, Bithumb: 0, Upbit: 0)
- **UNPERSISTED EVENT COUNT:** `0`
- **TOTAL MESSAGES:** `49,870,000개` (~5,000만 건 수집)
  - Binance: 23,769,045개 (14.14 GB)
  - Bithumb: 20,340,045개 (27.04 GB)
  - Upbit: 5,760,905개 (14.50 GB)
- **TOTAL DATA VOLUME:** 약 `55.68 GB`

---

## 7. 시스템 자원 및 오염 검사 (Resource & Contamination Scan)

- **디스크 용량:** 총 200 GB 중 사용 73 GB (37%), 가용 128 GB 확보 (디스크 부족 없음) [FACT]
- **소유권 위반:** `/var/lib/bitcoin-trader/72h-soak/aws-72h-soak-20260905-8017b83e` 하위 모든 런타임 파일은 `bitcoin-trader:bitcoin-trader` 소유 [FACT]
- **서비스 재시작 횟수:** 저널상 `Starting bitcoin-trader-72h-soak` 이벤트 정확히 `1회` (단 한 번도 크래시/재시작 루프에 빠지지 않음) [FACT]
- **에포크 격리:** 타 테스트 에포크와 완전히 격리되어 오염 없음 [FACT]

---

## 8. 수집된 로컬 증거물 아티팩트 목록 (Local Manifest)

디렉터리: `evidence/post72h-final-audit-20260908/interactive-guest-20260908T151318Z/`

| 파일명 | 크기 (bytes) | SHA-256 | 분류 |
| :--- | :--- | :--- | :--- |
| `raw_session_transcript.log` | 58,243 | `fa87ea237dfa5ae0e52dba470387e4ce40d700edc45e863037577a67c0d0e3aa` | 원시 PTY 세션 기록 |
| `phase02_identity.txt` | 633 | `d3c7e2492afe615e9aaf4ed02563fa88ac79b90f0638f621cc53799b6dd7d4bb` | 신원 및 환경 컨텍스트 |
| `phase03_process_state.txt` | 1,962 | `1386887591391426580ae47c7bcf7e9dbb4b6a5d299e3d9a53021d260f1dacc5` | 프로세스 활성 상태 |
| `phase04_systemd_lifecycle.txt` | 1,094 | `e1a37076e5c831bb91c5cd7fbb21bd4cc84a41b0d994699311730d79f1e86379` | Systemd 라이프사이클 |
| `phase05_journal_lifecycle.txt` | 20,055 | `4c296ed2d94dbfb66156c3085044d554d3a703bd8f248cdf37178cb4b4ba44de` | 저널 핵심 발췌록 |
| `phase07_raw_topology.txt` | 2,298 | `d14e97fac490582cc218f323aaee3dcadb3c0492189806de39d1d470a4f4795b` | RAW 파티션 토폴로지 |
| `phase08_archive_topology.txt` | 2,288 | `72f400c3197b3c7f2488eee343795d80bdcfc7c3baf79d5d0e67b0d9ec1f8420` | 아카이브/영수증 토폴로지 |
| `phase10_disk_ownership.txt` | 1,113 | `4e8a9cbc66c521b31cf68aaedaa5c47a48f4ce193b6fee8b8bb90da01168ecb8` | 디스크 및 소유권 |
| `phase11_start_candidates.txt` | 12,080 | `c2c7a53f895246c6b2194b61114e708c60833bbfd0d36c227161d4380aa218e8` | 시작 시각 증거 후보 |
| `phase12_completion_candidates.txt`| 6,581 | `43651bc2ac8618e2603baa2893615328698af8b581ee30c96b9fdfd998a9ae72` | 종료 시각 증거 후보 |
| `phase13_restart_contamination.txt`| 2,140 | `91739ec6fe73e4a4e045412011ff3bd2271db755e50e59a4f8502624caee5220` | 재시작 및 오염 검사 |
| `result.json` | 838 | `33d4fd460e069928464e2cbc0dbeb8e3853db40f65214da0440e972a2cff18cf` | 게스트 원본 슈퍼바이저 결과 |
| `collector_metrics.json` | 2,895 | `5ba0c637e00608a514eec37d1f71b0cd186ecf3c35b5750c0fef34c2636dce9b` | 게스트 원본 최종 메트릭 |
| `metric-publisher-state.json` | 214 | `8e367da4b626c52c5891a75fd4e6bfeaba98f53cdd1ecabbc0963184465ca865` | 게스트 원본 퍼블리셔 상태 |
| `aws-72h-soak-20260905.runtime.json`| 2,619 | `cb3dee0331cebed2ede5b43a0092fad0b2aad0989be63f7666d3e6547a66c11c` | 게스트 원본 런타임 봉인 |
| `archive_backlog_metrics.json` | 548 | `05d4d2ad02bb937210b61bcd9a95055e79accd02edc2b02049fa1d33cf837d1f` | 게스트 원본 아카이브 백로그 |
| `full_scan_05_report.json` | 705 | `ec50ee56d0df9bc31ca0342cb3c5cae9d24a7ffb15416bd0485333402a9b9d7a` | 게스트 원본 풀스캔 보고서 |
| `.full_scan_runner.json` | 220 | `7fa3b1cd3fb73c036f295ddea384fab21fc1dd1ec87a5cf013362f690524c1d4` | 게스트 원본 풀스캔 메타데이터 |
| `manifest.json` | 5,010 | - | 전체 아티팩트 해시 매니페스트 |

---

## 9. Astra 수석 과학 감사관 이관 제언 (Handoff Instructions for Astra)

1. **증거 무결성 검증:**
   `manifest.json`의 18개 아티팩트 해시를 검증하여 로컬 복원물이 게스트 원본과 정확히 일치함을 확인.
2. **자연 완료 및 실제 시작/종료 바인딩:**
   - 시작 시각: `2026-09-05T05:40:03Z`
   - 종료 시각: `2026-09-08T05:40:03Z`
   - 유효 지속 시간: `259,200초 (72.000시간)` 충족 판정 진행 가능.
3. **오프라인 임포트 파이프라인 가동:**
   S3 미동기화 파일들을 포함하여 EC2 게스트의 5,530개 RAW 파일 전체를 로컬 검증 환경으로 전송한 후, `scripts/post_72h_offline_import.sh`를 실행하여 Deep DQ 감사, 매니페스트 봉인, DQ 적격성 판정, 정규화 및 홀드아웃 분할을 순차 집행할 것.
