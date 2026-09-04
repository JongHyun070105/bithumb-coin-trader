# AWS 72H SOAK OPERATIONAL FAILURE PLAYBOOK (A to Z)

**문서 버전:** 1.0.0  
**적용 대상:** AWS 72시간 무개입 시장 데이터 수집기(Cross-Market Collector) 및 자동화 아카이브/풀스캔 파이프라인  
**원칙:** Fail-Closed, 무조작 원칙(No False-Green), 증적 보존 최우선, 비인가 자동 복구 금지  

---

## 1. 개요 및 비상 정지 원칙

본 문서는 AWS EC2 상에서 72시간 동안 무개입 연속 운용되는 시장 데이터 수집기 및 아카이브 스케줄러 운용 중 발생 가능한 모든 장애 시나리오(A~Z)에 대한 표준 운영 절차(SOP)를 규정한다.

- **절대 금지:** 데이터 손실 위험 감수, 장애 증적 덮어쓰기/삭제, S3 `DeleteObject` 호출, 임의 sudo chown 실행, 실패한 작업을 강제로 PASS 처리하는 행위.
- **Fail-Closed 준수:** 모호하거나 위험한 상태에서는 추가 쓰기 또는 무리한 재시도를 중단하고 상태를 동결 보존한다.

---

## 2. 세부 장애 대응 절차 (Scenarios A through Z)

### Scenario A: COLLECTOR PROCESS EXITS EARLY (수집기 프로세스 조기 종료)
- **증상:** 예정된 72시간(259,200초) 도달 전 수집기 프로세스(`run_cross_market_collector.py`)가 0 또는 비정상 코드로 종료됨.
- **판정:** **FAIL** (단 1초라도 조기 종료 시 72H soak 완주 실패).
- **조치 절차:**
  1. 동일 에포크에서 수집기를 자동으로 재시작하지 않는다 (침묵의 단절 은폐 금지).
  2. Supervisor는 즉시 Publisher 및 Archive Scheduler를 정상 종료(graceful shutdown) 처리한다.
  3. `collector_metrics.json`, `result.json`, `supervisor.log`, `collector-lifecycle.json`을 즉시 보존한다.
  4. 새로운 에포크를 자동으로 열지 않는다.
  5. 종료 원인(SIGTERM, OOM-Killer, 미처리 예외 등)을 추출하여 최종 보고서에 기록한다.

### Scenario B: WRITER ERROR > 0 (디스크 쓰기 오류 발생)
- **증상:** `collector_metrics.json` 내 `writer_errors > 0` 또는 `WriterErrors` CloudWatch 메트릭 발생.
- **판정:** **FAIL**.
- **조치 절차:**
  1. 오류를 무시하고 수집을 지속하지 않는다.
  2. 어떤 파티션 파일(거래소/마켓/스트림)에서 I/O 에러가 발생했는지 로그를 확인한다.
  3. 파일시스템 손상 또는 권한 위반 여부를 확인하고 증적을 보존한다.

### Scenario C: QUEUE DROP > 0 (내부 큐 메시지 유실)
- **증상:** WebSocket 버퍼 큐 오버플로우로 인한 메시지 폐기 발생 (`QueueDrops > 0`).
- **판정:** **FAIL**.
- **조치 절차:**
  1. 발생 시각, 거래소(Bithumb/Binance/Upbit), 스트림(orderbook/trade/ticker), 유실 건수를 정확히 정량화한다.
  2. 시스템 부하 및 디스크 I/O 병목 상태를 수집한다.
  3. 무결성 결손이 발생하였으므로 해당 구간 데이터는 불완전(Degraded/Failed)으로 분류한다.

### Scenario D: UNPERSISTED > 0 (종료 시 미기록 이벤트 잔존)
- **증상:** 수집기 종료 시점 `unpersisted_event_count > 0`.
- **판정:** **FAIL**.
- **조치 절차:**
  1. 버퍼 드레인(writer drain) 미완료 상태에서 프로세스가 강제 종료되었음을 의미하므로 증적 보존.
  2. 정상적인 45초 shutdown grace 내에 드레인이 완료되지 못한 원인을 분석한다.

### Scenario E: WEBSOCKET RECONNECT (웹소켓 연결 단절 및 재연결)
- **증상:** 거래소 소켓 단절 후 재연결 이벤트 발생 (`reconnect_count > 0`).
- **판정:** **정밀 조사 후 조건부 판정** (자동 PASS 또는 무조건 FAIL 처리 금지).
- **조치 절차:**
  1. 이벤트 발생 시각, 거래소명, 스트림, 단절 사유, 복구 소요 시간(초)을 추출한다.
  2. 단절 구간 중 메시지 유실 여부와 오더북 스냅샷 재수신/재구독 일관성을 확인한다.
  3. **반복적인 재연결 폭풍(Reconnect Storm)** 또는 오더북 스냅샷 불일치가 확인될 경우 즉시 **FAIL**로 격상한다.

### Scenario F: BINANCE 443 FAILURE (바이낸스 웹소켓 443 포트 장애)
- **증상:** 바이낸스 스트림 연결 불가 (DNS 실패, 핸드셰이크 타임아웃 등).
- **판정:** **조사 및 보수적 격상**.
- **조치 절차:**
  1. **절대 금지: 보안그룹을 9443 등으로 임의 확장하지 않는다.** 프로덕션 포트는 오직 443이다.
  2. DNS 확인 (`stream.binance.com`), 아웃바운드 443 TLS 핸드셰이크 상태, 바이낸스 거래소 공식 공지/인시던트를 확인한다.
  3. 일시적 단절 후 443으로 정상 복구되었는지 로그를 추적한다.

### Scenario G: S3 PUT 403 / ACCESS DENIED (S3 임시 아카이브 업로드 권한 거부)
- **증상:** 아카이브 오케스트레이터의 S3 `PutObject` 호출 시 403 Access Denied 발생.
- **판정:** **ARCHIVE_SUB_FAIL / 72H BLOCKED**.
- **조치 절차:**
  1. 운용 도중 권한을 수정하기 위해 임의로 IAM을 변경하지 않는다.
  2. 실패한 파티션은 로컬 RAW 상태로 안전하게 보존하며, 아카이브 작업은 FAILED로 기록하고 재시도를 대기한다.
  3. 로컬 잔여 용량이 안전할 경우 수집기는 지속될 수 있으나, 전체 72H 최종 판정은 PASS로 선언될 수 없다.

### Scenario H: S3 5XX / THROTTLING / NETWORK ERROR (S3 일시적 서버 오류)
- **증상:** 500, 503 SlowDown 등 일시적 AWS S3 에러 발생.
- **판정:** **BIASED EXPONENTIAL RETRY (최대 3회)**.
- **조치 절차:**
  1. 사전 검증된 지수 백오프(최대 3회) 내에서만 안전하게 재시도한다.
  2. 무한 재시도 루프에 빠지지 않도록 유한 차단한다.
  3. 이미 업로드된 객체에 대한 덮어쓰기는 금지되며, SHA256 체크섬을 통한 멱등성을 보장한다.

### Scenario I: FULL-SCAN TIMEOUT (풀스캔 실행 시간 초과)
- **증상:** 디태치드 풀스캔 프로세스가 하드 타임아웃(기본 1800초)을 초과함.
- **판정:** **FAIL / BLOCKED**.
- **조치 절차:**
  1. 감시 프로세스(`run_full_scan_supervisor`)가 SIGTERM을 전송하고 5초 유예 후 SIGKILL로 안전하게 회수한다.
  2. 해당 시간대에 대해 `full_scan_{hour}_report.json`에 `status: FAIL`, `error: TIMEOUT`을 강제 기록한다.
  3. `failed_full_scan_jobs` 메트릭을 1 증가시킨다.
  4. 시스템 리소스(I/O, CPU, 메모리) 교착 상태를 조사한다.

### Scenario J: FULL-SCAN CHILD EXIT NONZERO (풀스캔 자식 프로세스 비정상 종료)
- **증상:** 풀스캔 검증 자식 프로세스가 0이 아닌 exit code로 종료됨.
- **판정:** **FAIL**.
- **조치 절차:**
  1. 감시 프로세스가 `full_scan_{hour}_report.json`에 `status: FAIL`, `exit_code: N`을 기록한다.
  2. 해당 시간대를 절대 "완료(completed)"로 마킹하지 않고 백로그 실패로 유지한다.
  3. 상세 실패 로그(`full_scan_{hour}.log`)를 분석하여 원인 파악.

### Scenario K: GLOBAL FLOCK HELD (글로벌 풀스캔 락 경합)
- **증상:** 후속 시간대 아카이브가 완료되었으나 이전 시간대 풀스캔이 아직 실행 중(`FULL_SCAN_GLOBAL_LOCK_NAME` 점유).
- **판정:** **NORMAL QUEUING (정상 직렬화 대기)**.
- **조치 절차:**
  1. 후속 시간대 풀스캔을 병렬로 중복 기동하지 않는다 (동시성 1 철저 보장).
  2. 후속 시간대는 `pending_full_scan_jobs`로 유지된다.
  3. 아카이브 스케줄러는 다음 폴링 주기에서 이전 락이 해제된 후 가장 오래된 시간대(oldest-first)부터 순차 기동한다.
  4. 만약 최장 대기 시간(`oldest_pending_age_seconds`)이 3600초를 초과하면 백로그 경보를 발생시킨다.

### Scenario L: ROOT-OWNED / WRONG-OWNER ARTIFACT (오너십 위반 발생)
- **증상:** 런타임 디렉토리 또는 락 파일이 `root` 또는 비인가 사용자로 생성됨.
- **판정:** **FATAL FAIL-CLOSED (즉시 작업 중단)**.
- **조치 절차:**
  1. `verify_runtime_ownership()`에서 `OwnershipViolationError` 발생.
  2. **절대 금지: 프로덕션 운용 중 자동으로 `chown`을 실행하지 않는다.**
  3. 즉시 작업을 Fail-Closed 중단하고, 비인가 프로세스 침범 여부 및 관리자 개입 이력을 확인한다.

### Scenario M: DISK 70% (디스크 용량 경고)
- **증상:** 루트 EBS 사용량이 70%에 도달함.
- **판정:** **WARNING (주의 모니터링)**.
- **조치 절차:**
  1. CloudWatch 경보 발생 확인.
  2. 현재 시간당 생성률을 재측정하여 80% 및 90% 도달 예상 시간을 재산출한다.
  3. **절대 금지: 용량을 확보하기 위해 데이터를 임의 삭제하거나 cleanup을 켜지 않는다.**

### Scenario N: DISK 80% (디스크 용량 심각 경고)
- **증상:** 루트 EBS 사용량이 80%에 도달함.
- **판정:** **HIGH CAPACITY WARNING (고위험 단계)**.
- **조치 절차:**
  1. 잔여 72H 완주 가능 여부를 긴급 평가한다.
  2. 디스크 증가 추세가 지속될 경우 90% 도달 전 계획된 정지 절차를 준비한다.
  3. 비필수 파일(오래된 임시 로그 등) 외의 원천 마켓 데이터는 절대 삭제하지 않는다.

### Scenario O: DISK 90% (디스크 임계치 도달 - 절대 정지선)
- **증상:** 루트 EBS 사용량이 90%에 도달함 (`disk_critical_percent=90.0`).
- **판정:** **OVERALL SOAK FAIL & CONTROLLED SHUTDOWN**.
- **조치 절차:**
  1. 아카이브 파이프라인 및 수집기는 새 파티션 쓰기로 인한 파일시스템 고갈을 방지하기 위해 즉시 제어된 정상 셧다운(Controlled Shutdown)을 수행한다.
  2. 긴급 조치라는 이유로 원천 RAW 데이터를 비인가 삭제하는 행위를 절대 금지한다.
  3. 파일시스템 무결성을 확보한 상태에서 증적을 동결한다.

### Scenario P: FILESYSTEM ENOSPC (디스크 완전 고갈)
- **증상:** `errno 28 No space left on device` 발생.
- **판정:** **CRITICAL FAIL**.
- **조치 절차:**
  1. 즉시 프로세스 쓰기 중단.
  2. 저장된 파일의 손상 여부를 확인하고, 불완전 기록 상태를 보고한다.
  3. 결코 "정상 완료"나 "무손실 수집"으로 주장하지 않는다.

### Scenario Q: CLOUDWATCH PUBLISHER FAILURE (메트릭 퍼블리셔 오류)
- **증상:** CloudWatch PutMetricData 전송 실패 또는 퍼블리셔 비정상 종료.
- **판정:** **OBSERVABILITY DEGRADED (수집기 강제 중단 금지)**.
- **조치 절차:**
  1. 모니터링 실패를 숨기기 위해 컬렉터를 임의 종료하지 않는다.
  2. 로컬 디스크의 `collector_metrics.json`과 `logs/metric-publisher-ops.jsonl`이 영구 증적으로 유지되므로 이를 신뢰한다.
  3. 관측성 결손 사실을 최종 평가에 기록한다.

### Scenario R: SSM DISCONNECT (AWS Systems Manager 세션 단절)
- **증상:** 운영자의 대화형 SSM 터미널 연결이 끊어짐.
- **판정:** **NORMAL DISCONNECTED RUN (비장애)**.
- **조치 절차:**
  1. 수집기 및 아카이브 스케줄러는 `systemd-run` 및 `start_new_session` 디태치드로 분리되어 있으므로 SSM 단절과 무관하게 독립 완주한다.
  2. SSM 재연결 후 `systemctl status` 및 런타임 결과 파일을 확인하여 상태를 조회한다.

### Scenario S: AWS INSTANCE REBOOT / TERMINATION (인스턴스 재부팅 또는 삭제)
- **증상:** EC2 인스턴스가 불시에 재부팅되거나 하이퍼바이저 문제로 정지됨.
- **판정:** **FAIL**.
- **조치 절차:**
  1. 재부팅 후 동일 에포크를 이어서 수집하지 않는다 (단절된 수집기를 동일 연속 세션으로 위장 금지).
  2. 재부팅 전까지 수집된 디스크 증적을 수거하고 중단 시점을 명시한다.

### Scenario T: TIME SYNC / CLOCK PROBLEM (시간 동기화 역행 또는 괴리)
- **증상:** chrony 또는 Amazon Time Sync 동기화 실패로 시계가 역행하거나 5초 이상 드리프트 발생.
- **판정:** **FAIL / INVESTIGATE**.
- **조치 절차:**
  1. 교환소 타임스탬프와 로컬 수신 타임스탬프의 단조 증가성을 검증한다.
  2. 단조 시계(`time.monotonic()`) 기반 듀레이션 계산과 벽시계 타임스탬프의 역행 여부를 확인한다.

### Scenario U: ACTIVE PARTITION ARCHIVE ATTEMPT (활성 파티션 아카이브 시도)
- **증상:** 현재 수집기가 실시간으로 기록 중인 파티션을 아카이브하려는 시도 발생.
- **판정:** **CRITICAL SAFETY VIOLATION -> FAIL-CLOSED BLOCKED**.
- **조치 절차:**
  1. `is_closed_stable_partition` 및 `discover_eligible_hours`의 활성 파일 검증에서 즉시 차단.
  2. 해당 파티션은 절대 아카이브, 압축, 삭제하지 않는다.

### Scenario V: CONFIG FINGERPRINT MISMATCH (설정 핑거프린트 불일치)
- **증상:** 기동 시 인자로 전달된 핑거프린트와 런타임 씰 JSON의 Canonical 해시가 불일치함.
- **판정:** **DO NOT START / IMMEDIATE ABORT**.
- **조치 절차:**
  1. 수집기 기동을 즉시 거부한다.
  2. 일치하도록 핑거프린트를 임의 조작하거나 덮어쓰지 않고, 불일치 원인을 규명한다.

### Scenario W: RUNTIME COMMIT MISMATCH (런타임 커밋 불일치)
- **증상:** 게스트 Git HEAD, 로컬 HEAD, 또는 씰에 기록된 커밋 해시가 서로 다름.
- **판정:** **DO NOT START**.
- **조치 절차:**
  1. 배포 번들 동기화를 통해 세 위치의 커밋이 100% 일치할 때까지 기동을 보류한다.

### Scenario X: GIT REMOTE DIVERGENCE DURING PREP (원격 Git 브랜치 다이버전스)
- **증상:** 원격 저장소와 로컬 브랜치 사이에 충돌이나 rebase 필요성 발생.
- **판정:** **BLOCKED**.
- **조치 절차:**
  1. 강제 푸시(`--force`, `--force-with-lease`) 절대 금지.
  2. fast-forward 머지만 허용하며, 불가능할 경우 분기를 격리하고 사용자에게 보고한다.

### Scenario Y: TERRAFORM UNEXPECTED REPLACEMENT (테라폼 예기치 못한 리소스 교체)
- **증상:** `terraform plan` 결과 EC2, EBS, 보안그룹 등의 `replace` 또는 `destroy`가 1개 이상 발생.
- **판정:** **TERRAFORM_PLAN_GATE BLOCKED (절대 APPLY 금지)**.
- **조치 절차:**
  1. apply 명령을 절대 실행하지 않는다.
  2. 계획을 즉시 중단하고 리소스 교체를 유발한 변수 변경점을 역추적한다.

### Scenario Z: IAM PRIVILEGED STEP REQUIRES ROOT/MFA (루트/MFA 권한 작업 필요)
- **증상:** Boundary 버전 생성 또는 정책 변경에 상위 권한/MFA가 필요함.
- **판정:** **BLOCKED_PENDING_APPROVAL**.
- **조치 절차:**
  1. 임의의 우회 시도를 하지 않는다.
  2. 최소 권한 후보 정책 JSON 및 CLI 명령어를 패키징하여 사용자 복귀 시 승인받도록 준비한다.

---
**문서 끝.**
