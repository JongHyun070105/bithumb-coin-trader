# AWS Long-Running Collector Operator Runbook

## 1. 목적 (Purpose)

본 런북은 AWS EC2 상에서 72시간 동안 무중단(Unattended)으로 실행되는 `bitcoin-trader` 공공 시장 데이터 수집기(Collector)의 운영, 상태 점검, 장애 대응, 안전 종료 및 사후 감사 절차를 정의한다. 본 문서에는 일체의 비밀키, API 토큰 또는 계정 자격 증명이 포함되지 않는다.

---

## 2. 승인된 시작 절차 (Approved Start Procedure)

수집기는 고립된 `systemd-run` transient 서비스로 기동되며, SSH 터미널이나 SSM 세션 연결 종료와 무관하게 백그라운드에서 지속 동작하도록 설계되었다.

### 2.1 사전 점검 확인 (Preflight Check)
시작 전 아래 조건이 모두 충족되었는지 확인한다:
- 인스턴스: `i-008bc503c1136349f` (ap-northeast-2)
- 디스크 가용 용량: `/` 파일시스템 최소 150 GiB 이상 가용
- 실행 중인 잔여 수집기 프로세스: `0`개 확인
- Git commit: 사전에 봉인된 런타임 커밋(`9532cebc902856d954bf80b51dbe567b543dc8e2`) 체크아웃 확인
- 런타임 디렉토리 권한: `0700`, `bitcoin-trader:bitcoin-trader` 확인

### 2.2 기동 명령어 (Systemd Transient Launch)
EC2 인스턴스 내에서 SSM을 통해 아래 명령어로 기동한다:
```bash
sudo systemd-run \
  --unit="bitcoin-collector-72h" \
  --uid="bitcoin-trader" \
  --gid="bitcoin-trader" \
  --property="WorkingDirectory=/var/lib/bitcoin-trader/72h-soak/aws-72h-soak-20260904-43e79055" \
  --property="StandardOutput=append:/var/lib/bitcoin-trader/72h-soak/aws-72h-soak-20260904-43e79055/logs/collector_stdout.log" \
  --property="StandardError=append:/var/lib/bitcoin-trader/72h-soak/aws-72h-soak-20260904-43e79055/logs/collector_stderr.log" \
  --property="TimeoutStopSec=180" \
  --property="KillMode=mixed" \
  /var/lib/bitcoin-trader/venv/bin/python -m scripts.run_unified_collector \
    --config /var/lib/bitcoin-trader/72h-soak/aws-72h-soak-20260904-43e79055/config.json
```

---

## 3. 실시간 모니터링 및 상태 확인 (Health Checks)

### 3.1 서비스 상태 점검
```bash
# 1. Systemd 유닛 활성 상태 확인
systemctl status bitcoin-collector-72h

# 2. 프로세스 트리 및 리소스 사용량 점검
pgrep -fl "python.*run_unified_collector"
ps aux | grep "[b]itcoin-collector"

# 3. 실시간 저널 로그 스트리밍 (최근 50줄)
journalctl -u bitcoin-collector-72h -n 50 -f
```

### 3.2 핵심 메트릭 지표 해석 (Metrics Interpretation)

| 메트릭 지표 | 정상 범위 | 이상 기준 | 운영자 조치 |
| :--- | :--- | :--- | :--- |
| `WriterErrors` | `0` | `> 0` | 즉시 에러 로그 확인, 디스크 I/O 오류 여부 점검 |
| `QueueDrops` | `0` | `> 0` | 인메모리 버퍼 병목 또는 디스크 쓰기 지연 점검 |
| `Unpersisted` | `< 1,000` | `> 10,000` (지속 증가) | 라이터 스레드 중단 여부 및 플러시 주기 점검 |
| `Reconnects` | 시간당 `< 5` | 시간당 `> 20` | 거래소 웹소켓 엔드포인트 네트워크 연결 상태 확인 |
| `DiskUsage` | 사용량 점진 증가 | 잔여 공간 `< 20 GiB` | 백로그 적체 원인 파악 및 긴급 알림 |
| `FullScanFailures` | `0` | `> 0` | 스캔 실패 파티션 격리 로그 및 데이터 손상 여부 확인 |

---

## 4. SSM 세션 단절 및 재연결 대응 (SSM Reconnect)

1. SSM CLI 세션 타임아웃 또는 로컬 네트워크 단절이 발생하더라도, `systemd-run`으로 격리 기동된 수집기 및 풀스캔 백그라운드 프로세스는 계속 동작한다.
2. 재연결 시:
```bash
# 새로운 SSM 세션 시작
aws ssm start-session --target i-008bc503c1136349f

# 기존 실행 상태 무결성 확인
scripts/monitor_soak_progress.py --epoch <current_epoch>
```

---

## 5. 절대 금지 행위 (What NEVER to Do)

- **절대 `kill -9`로 수집기를 강제 종료하지 않는다**: 내부 버퍼가 디스크에 플러시되지 못하고 파티션 파일이 비정상 절단(Truncation)된다. 반드시 `SIGINT` 또는 `SIGTERM`을 통해 최대 180초의 그레이스풀 드레인을 거친다.
- **실행 중 권한(`chmod` / `chown`)을 임의 변경하지 않는다**: 런타임 락 파일 및 디렉토리 권한 불일치로 아카이버 프로세스 충돌이 발생할 수 있다.
- **실행 중인 설정 파일(`config.json`)을 직접 수정하지 않는다**.
- **운영 중인 파티션 디렉토리 내의 미완료 RAW 파일을 임의 삭제/이동하지 않는다**.
- **Root 계정으로 대화형 수집 프로세스를 기동하지 않는다** (반드시 `bitcoin-trader` 비특권 계정 사용).

---

## 6. 장애 에스컬레이션 및 안전 종료 절차 (Failure Escalation & Safe Shutdown)

### 6.1 에스컬레이션 기준
- 디스크 여유 공간 10 GiB 미만 도달 시.
- 연속 3시간 이상 아카이브 파티션 압축/스캔이 실패하여 백로그가 10개 이상 누적된 경우.
- 인스턴스 OOM 킬러에 의해 수집기가 비정상 종료된 경우.

### 6.2 안전 종료 절차 (Graceful Shutdown)
```bash
# 1. Systemd 유닛에 SIGTERM 전송 (드레인 대기)
sudo systemctl stop bitcoin-collector-72h

# 2. 프로세스 정상 종료 대기 확인 (최대 180초)
while pgrep -f "run_unified_collector" > /dev/null; do
    echo "Waiting for collector to flush and terminate..."
    sleep 3
done

# 3. 잔류 풀스캔 락 및 백그라운드 프로세스 종료 대기
while pgrep -f "orchestrate_closed_hour_archive" > /dev/null; do
    echo "Waiting for active archive orchestrator..."
    sleep 5
done
```

---

## 7. 증적 보존 및 사후 감사 (Evidence Preservation & Post-Run Audit)

1. **로그 및 영수증 보존**:
   - `logs/` 디렉토리 내 모든 표준 출력/에러 로그 복사.
   - `archive-receipts/` 내의 모든 `.json` 영수증 및 `.done` 마커 확인.
2. **사후 감사 스크립트 실행**:
```bash
python3 scripts/audit_72h_soak.py \
  --epoch aws-72h-soak-20260904-43e79055 \
  --base-dir /var/lib/bitcoin-trader/72h-soak

python3 scripts/generate_72h_final_report.py \
  --epoch aws-72h-soak-20260904-43e79055 \
  --output /var/lib/bitcoin-trader/72h-soak/aws-72h-soak-20260904-43e79055/FINAL_72H_REPORT.json
```
3. **무삭제 원칙**: 72시간 소크 종료 후 데이터 및 영수증에 대한 일체의 임의 삭제를 금지하며, 원격 S3 미러링 검증이 완료될 때까지 로컬 디스크 원본을 온전히 보존한다.
