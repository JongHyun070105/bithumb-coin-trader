# 수집기 파이프라인 장애 모델 및 관측성 맵 (Collector Failure Model & Observability Map)

- **작성 일시**: 2026-09-17T01:30:00Z
- **작성 주체**: 자율 수집 신뢰성 및 관측성 엔지니어링 디렉터 (Autonomous Collector Reliability & Observability Engineering Director)
- **적용 버전**: v9.2.0 (관측성 분리 및 독립 증거 아키텍처)
- **핵심 목표**: V4와 같은 미확인 장애 재발 방지, 5분 이내 장애 진단성 확보, 단일 상태 불리언(Running/Failed) 지양 및 컴포넌트별 독립 관측

---

## 1. 수집·아카이브 파이프라인 엔드-투-엔드 토폴로지

```
[EXCHANGE WEBSOCKET]
       │
       ▼ (1) 세션 수립 / 구독
[SESSION / SUBSCRIPTION]
       │
       ▼ (2) 메시지 수신 / 역직렬화
[CANONICAL EVENT]
       │
       ▼ (3) 인메모리 버퍼링
[QUEUE]
       │
       ▼ (4) 디스크 I/O (원시 쓰기)
[WRITER]
       │
       ▼ (5) 시간 코호트 파티셔닝
[LOCAL RAW]
       │
       ▼ (6) 시간 마감 및 유예(Grace) 경과
[COHORT CLOSE]
       │
       ▼ (7) Zstandard 압축
[COMPRESS]
       │
       ▼ (8) S3 업로드
[ARCHIVER]
       │
       ▼ (9) 객체 스토리지 저장
[S3]
       │
       ▼ (10) 커버리지 및 체크섬 영수증 발행
[RECEIPT / COVERAGE / MANIFEST]
       │
       ▼ (11) 최종화 검증
[FINALIZATION]
```

---

## 2. 파이프라인 엣지별 상세 장애 매핑 (Failure Mode Matrix)

| 엣지 | 단계 | 발생 가능한 장애 (Failure Mode) | 관측 가능한 신호 (Observable Signal) | 기존 시스템 증거 한계 | 신규 필수 관측 증거 (New Required Evidence) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **(1)** | WebSocket $\to$ Session | 거래소 연결 거부, DNS 실패, 핸드셰이크 타임아웃, 침묵 연결(Silent TCP hang) | `reconnect_count` 폭증, `last_websocket_activity` 갱신 중단, 소켓 상태 UNCONNECTED | 단순 에러 로그만 남고 프로세스 자체는 살아있어 외부에서 감지 불가 | 세션별 TCP 상태 및 최근 웹소켓 핑/퐁 타임스탬프(`last_websocket_activity`), 재연결 횟수 추적 |
| **(2)** | Session $\to$ Event | 한산한 마켓의 거래량 전무 vs 파서 크래시/침묵 예외 | `last_canonical_event` 지연, `quarantine_count` 증가, 비정상 메시지 포착 | "최근 체결 이벤트 부재"를 프로세스 사망과 혼동함 | 프로세스 루프 하트비트(`last_loop_heartbeat`)와 마켓 이벤트 수신 시각 분리 기록 |
| **(3)** | Event $\to$ Queue | 이벤트 폭증으로 인한 큐 백로그, 메모리(RSS) 누수, OOM | `queue_depth` 지속 단조 증가, `max_queue_depth` 경보, `queue_dropped_events` 발생 | 큐 상태가 파일로 기록되지 않아 OOM 직전 상황 파악 불가 | 실시간 `queue_depth`, `unpersisted_count`, `rss_bytes`를 10초 스냅샷에 기록 |
| **(4)** | Queue $\to$ Writer | 디스크 I/O 블로킹, 권한 오류, 파일 디스크립터(FD) 고갈 | `last_dequeue` 지연, `last_local_raw_write` 중단, `writer_errors` 증가 | 로컬 파일 생성 실패 여부를 S3 업로드 시점(1시간 뒤)에야 인지 | `current_open_raw_count`, `writer_status`, 최근 쓰기 시각을 10초 주기로 로컬 원자적 기록 |
| **(5)** | Writer $\to$ Local RAW | 시간 경계 미전환, 파일 플러시 실패, 불완전 기록 | 활성 코호트와 파일 경로 불일치, 파일 크기 0B 고착 | 코호트 롤오버 실패 시 파일이 잠겨 아카이버로 인계 불가 | 활성 코호트(`utc_hour`), 열린 파일 목록, 관측된 피드 수(`observed_feed_count`) 감시 |
| **(6)** | Local RAW $\to$ Cohort Close | 유예 시간(Grace) 미준수, 미기록 파일 방치, 코호트 누락 | 유휴 코호트 체류 시간 초과, `last_closed_cohort` 갱신 지연 | 아카이브 스케줄러가 이전 시간대를 건너뛰는 현상 미감지 | 시간 마감 판정기록, 자격 획득(Eligibility) 타임스탬프 로깅 |
| **(7)** | Cohort Close $\to$ Compress | Zstandard 압축 CPU 스파이크, 임시 파일 잔류, 디스크 용량 부족 | 압축 진행 시간 지연, `disk_free_bytes` 급감, `last_compression` 정체 | 압축 프로세스 실패 시 예외가 스레드에 갇혀 침묵 | `archive_queue_depth`, 압축 소요 시간, 컴포넌트별 최종 예외 추적 |
| **(8)** | Compress $\to$ Archiver | S3 권한 오류, 네트워크 끊김, AWS Throttling, 동시성 락 충돌 | `last_s3_put` 지연, `upload_failures` 증가, `archive_errors` 누적 | V4처럼 커버리지(534KB)만 올리고 RAW 압축본은 안 올라가는 불일치 | S3 업로드 성공/실패 카운트, RAW vs COVERAGE 전송 분리 카운트 |
| **(9)** | Archiver $\to$ S3 | 원시 데이터 누락, S3 멀티파트 업로드 중단 | S3 키 인벤토리 내 `RAW_MARKET_DATA` 카운트 0, 커버리지 불완전 | S3 원격 조회를 정기적으로 하지 않아 30시간 내내 실패 지속 | 시간 마감 카나리(`Hour-Close Canary`)가 시간 경계마다 S3 적합성 전수 검증 |
| **(10)** | S3 $\to$ Receipt | 76개 피드 중 일부 누락(Missing slots), 체크섬 불일치 | `receipts_terminal < 76`, `unknown_missing > 0` | V2처럼 과거 RAW를 불필요하게 재스캔하며 셧다운 타임아웃 초과 | 코호트별 폐쇄 영수증의 정확한 76개 슬롯 일치 및 DATA_PRESENT/VERIFIED_ZERO 검증 |
| **(11)** | Entire $\to$ Supervisor/Witness | 프로세스 크래시, SIGKILL/OOM, RuntimeMaxSec 초과 강제 종료 | `SERVICE_RESULT != success`, `EXIT_STATUS != 0`, EC2는 살고 데몬 사망 | V4에서 SSM 권한 부재로 프로세스 종료 상태가 `NOT_VERIFIABLE`로 남음 | `systemd ExecStopPost` 기반 **터미널 증인(Terminal Witness)**이 종료 즉시 영수증 S3 업로드 |
| **(12)** | Observer $\to$ Independent S3 | 수집기는 정지했으나 옵저버는 생존 / 반대로 옵저버만 크래시 | 옵저버 분당 증거 업로드 단절 또는 옵저버가 수집기 STALE 보고 | 수집 프로세스와 관측 프로세스가 한 몸이라 수집기 죽으면 관측도 소멸 | 수집기와 완전히 분리된 독립 systemd 서비스/타이머가 60초마다 S3 증거 발행 |

---

## 3. 핵심 아키텍처 원칙 (Separation of Concerns)

1. **컴포넌트 분리**:
   - `COLLECTOR` (웹소켓 수신)
   - `WRITER` (로컬 RAW 쓰기)
   - `ARCHIVER` (압축 및 S3 업로드)
   - `EVIDENCE` (커버리지, 영수증, 매니페스트)
   - `SUPERVISOR` (systemd 서비스 수명주기)
   - `OBSERVER` (독립 상태 관측 및 S3 분당 증거 발행)
   - `TERMINAL WITNESS` (systemd `ExecStopPost` 기반 최종 종료 증인)
2. **단일 상태 불리언(`RUNNING/FAILED`) 폐기**:
   - 컴포넌트별 5단계 상태 명시: `HEALTHY`, `DEGRADED`, `STALE`, `FAILED`, `UNKNOWN`.
3. **무간섭 관측(Non-Intervention) 원칙**:
   - 옵저버는 오직 탐지(Detect), 기록(Record), 분류(Classify), 증거 업로드(Upload Evidence)만 수행.
   - 절대 수집기 재기동, 파일 복구, 설정 변경, 데이터 수정을 하지 않음 (Fail-Explicit).
4. **5분 이내 진단 가능성(5-Minute Diagnosability)**:
   - 장애 발생 후 5분 이내에 어떤 컴포넌트가 어디서 멈췄는지 대화형 SSM 없이도 S3 증거만으로 100% 특정 가능해야 함.
