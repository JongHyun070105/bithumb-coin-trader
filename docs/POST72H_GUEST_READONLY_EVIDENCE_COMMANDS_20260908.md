# Post-72H Audit: Fixed Read-Only Guest Evidence Commands (2026-09-08)

## 1. 개요 및 안전 원칙 (Overview & Safety Principles)

본 문서는 SSM 권한 부여 후 Astra 또는 과학 감사관이 EC2 게스트(`i-008bc503c1136349f`) 내부에서 실행할 **고정형 읽기 전용 감사 명령 번들**을 정의한다.

**안전 불변성 (Safety Invariants):**
- **STRICTLY READ-ONLY:** 일체의 파일 삭제(`rm`), 이동(`mv`), 수정(`sed -i`, `truncate`), 권한 변경(`chmod`, `chown`), 서비스 조작(`systemctl start/stop/restart`), 프로세스 종료(`kill`, `pkill`) 명령을 일절 포함하지 않는다.
- **SSM 출력 크기 제한 보호 (Bounded Output Size):** SSM Command 응답은 24 KiB(또는 S3 연동 시 대용량) 제한이 있으므로, 수 기가바이트의 RAW 파일을 직접 출력하거나 수십만 개의 파일을 개별 나열하지 않고 `stat`, `tail`, `wc -l`, 카운트, 해시 집계 방식을 사용한다.

---

## 2. 런타임 경로 및 식별자 바인딩 (Runtime Path Binding)

리포지토리 봉인 문서(`infra/aws/seals/aws-72h-soak-20260905.runtime.json` 및 `launch-provenance.json`) 기준:

- **대상 인스턴스 ID:** `i-008bc503c1136349f`
- **에포크 (Collector Epoch):** `aws-72h-soak-20260905-8017b83e`
- **런 ID (Collector Run ID):** `aws-72h-soak-run-20260905T024039Z-8017b83e`
- **베이스 경로:** `/var/lib/bitcoin-trader/72h-soak/aws-72h-soak-20260905-8017b83e`
- **예상 Systemd 유닛명:**
  - `bitcoin-trader-72h-soak-aws-72h-soak-run-20260905T024039Z-8017b83e.service`
  - 또는 `bitcoin-collector-72h.service`

---

## 3. 고정 감사 명령 번들 명세 (Approved Command Bundle)

### CMD 1. 게스트 시스템 시각 및 타임존 확인 (Host Clock & NTP)
```bash
date -u +"%Y-%m-%dT%H:%M:%SZ"
chronyc tracking || chronyc sources || timedatectl status
```

### CMD 2. 프로세스 활성 상태 검사 (Process Activity Check)
```bash
ps aux | grep -E "(run_unified_collector|run_cross_market_collector|bounded_supervisor|archive_scheduler)" | grep -v grep || echo "NO_MATCHING_PROCESS"
pgrep -fl python || echo "NO_PYTHON_PROCESS"
```

### CMD 3. Systemd 유닛 등록 및 라이프사이클 상세 속성 검사 (Systemd Unit Inspection)
```bash
systemctl list-units --all "bitcoin-*" --no-pager
systemctl status "bitcoin-*" --no-pager || true

# 상세 머신 판독 속성 (ActiveState, SubState, Result, ExitStatus, Timestamps)
systemctl show "bitcoin-trader-72h-soak-aws-72h-soak-run-20260905T024039Z-8017b83e.service" \
  -p Id,ActiveState,SubState,Result,ExecMainStatus,ExecMainCode,ExecMainStartTimestamp,ExecMainExitTimestamp,StateChangeTimestamp \
  --no-pager || true

systemctl show "bitcoin-collector-72h.service" \
  -p Id,ActiveState,SubState,Result,ExecMainStatus,ExecMainCode,ExecMainStartTimestamp,ExecMainExitTimestamp,StateChangeTimestamp \
  --no-pager || true
```

### CMD 4. Systemd 저널 시작 및 종료 이벤트 발췌 (Journal Lifecycle Excerpts)
```bash
# 기동 초기 30줄
journalctl -u "bitcoin-*" --no-pager -n 30 --reverse=false | head -n 30

# 종료 또는 최근 50줄
journalctl -u "bitcoin-*" --no-pager -n 50
```

### CMD 5. 수집기 최종 런타임 메트릭 및 퍼블리셔 상태 수집 (Runtime Metrics Artifacts)
```bash
BASE="/var/lib/bitcoin-trader/72h-soak/aws-72h-soak-20260905-8017b83e"
test -f "$BASE/collector_metrics.json" && cat "$BASE/collector_metrics.json" || echo "METRICS_FILE_MISSING"
test -f "$BASE/metric-publisher-state.json" && cat "$BASE/metric-publisher-state.json" || echo "PUBLISHER_STATE_MISSING"
test -f "$BASE/supervisor_result.json" && cat "$BASE/supervisor_result.json" || echo "SUPERVISOR_RESULT_MISSING"
```

### CMD 6. 디스크 파티션 및 사용량 검사 (Disk Usage & Quotas)
```bash
df -h /
du -sh /var/lib/bitcoin-trader/72h-soak/aws-72h-soak-20260905-8017b83e/* 2>/dev/null || du -sh /var/lib/bitcoin-trader/*
```

### CMD 7. RAW 파티션 파일 통계 및 최신 쓰기 검증 (RAW Partitions & Last Writes)
```bash
RAW="/var/lib/bitcoin-trader/72h-soak/aws-72h-soak-20260905-8017b83e/raw"
if [ -d "$RAW" ]; then
  echo "=== RAW File Count ==="
  find "$RAW" -type f -name "*.jsonl" | wc -l

  echo "=== Earliest 5 RAW files ==="
  find "$RAW" -type f -name "*.jsonl" -exec stat -c "%y %n" {} + | sort | head -n 5

  echo "=== Latest 10 RAW files ==="
  find "$RAW" -type f -name "*.jsonl" -exec stat -c "%y %n" {} + | sort | tail -n 10
else
  echo "RAW_DIR_MISSING"
fi
```

### CMD 8. 아카이브 압축 및 영수증 / 풀스캔 결과 검사 (Archive & Full-Scan Receipts)
```bash
RCP="/var/lib/bitcoin-trader/72h-soak/aws-72h-soak-20260905-8017b83e/archive-receipts"
CMP="/var/lib/bitcoin-trader/72h-soak/aws-72h-soak-20260905-8017b83e/compressed"
if [ -d "$RCP" ]; then
  echo "=== Archive Receipts Count ==="
  find "$RCP" -type f -name "*.json" | wc -l
  echo "=== Full-Scan Reports ==="
  find "$RCP" -type f -name "full_scan_*_report.json" -exec cat {} + 2>/dev/null || echo "NO_FULL_SCAN_REPORTS"
fi
if [ -d "$CMP" ]; then
  echo "=== Compressed Partitions Count ==="
  find "$CMP" -type f -name "*.zst" | wc -l
fi
```

### CMD 9. 루트 소유권 변조 및 에포크 오염 검사 (Anomaly & Ownership Scan)
```bash
echo "=== Root Owned Files Under /var/lib/bitcoin-trader ==="
find /var/lib/bitcoin-trader -user root -ls 2>/dev/null | head -n 20 || echo "NONE"

echo "=== Distinct Epoch Directories ==="
ls -la /var/lib/bitcoin-trader/72h-soak/ 2>/dev/null || ls -la /var/lib/bitcoin-trader/
```

### CMD 10. 배포 코드 Git Commit 및 런타임 지문 바인딩 (Git & Fingerprint Binding)
```bash
if [ -d "/var/lib/bitcoin-trader/repo" ]; then
  cd /var/lib/bitcoin-trader/repo && git rev-parse HEAD
elif [ -d "/home/ec2-user/bitcoin-trader" ]; then
  cd /home/ec2-user/bitcoin-trader && git rev-parse HEAD
else
  find / -name "run_unified_collector.py" -exec dirname {} + 2>/dev/null | head -n 5
fi
```
