# AWS 72H Post-Soak Verification Checklist

## 1. 개요 (Overview)

본 문서는 AWS 72시간 시장 데이터 수집(Soak)이 종료된 후, 데이터 무결성, 오케스트레이션 정상성, S3 업로드 완결성 및 연구 경계 준수를 검증하기 위한 최종 결정론적 13개 검증 체크리스트이다.

모든 항목은 실측 데이터와 아티팩트에 근거하여 `PASS`, `FAIL`, 또는 `DEVIATION`으로 명확히 판정되어야 한다.

---

## 2. 13개 핵심 사후 검증 체크리스트 (13-Point Checklist)

### 1. All Processes Stopped (모든 프로세스 정상 종료 확인)
- [ ] 수집기 메인 프로세스 종료: `pgrep -fl "run_unified_collector"` 결과가 정확히 `0`개인가?
- [ ] 메트릭 발행기 종료: `pgrep -fl "cloudwatch_metrics_publisher"` 결과가 `0`개인가?
- [ ] 아카이브 러너 및 풀스캔 백그라운드 프로세스 종료: `0`개인가?
- [ ] Systemd transient unit 상태가 `inactive (dead)`이며 정상 종료 코드(exit 0)인가?

### 2. Final Metrics Valid (최종 수집 메트릭 무결성)
- [ ] 최종 `metrics.json` 내 `writer_errors == 0` 인가?
- [ ] 최종 `queue_drops == 0` 인가?
- [ ] 최종 미저장 큐 잔여량 `unpersisted_messages == 0` 인가?
- [ ] 비정상 스레드 충돌 또는 예외 카운트가 `0`인가?

### 3. Active Partition Files Empty (활성 파티션 비움)
- [ ] 최종 매니페스트 또는 메트릭 보고서의 `active_partition_files`가 빈 배열(`[]`)로 정상 플러시 및 닫힘(Closed) 처리되었는가?

### 4. Final Partial RAW Preserved (종료 시점 마지막 파티션 보존)
- [ ] 72시간 경계 시점에 아직 60분이 차지 않아 아카이브되지 않은 마지막 시간대의 부분 RAW 파일이 임의 삭제되지 않고 로컬 디스크에 그대로 보존되었는가?

### 5. Remaining Eligible Scans Complete (종료 후 잔여 적격 스캔 완결)
- [ ] 종료 시점까지 60분이 경과하여 closed 처리된 모든 적격 아카이브 시간에 대해 `full_scan_{hour}_report.json`이 누락 없이 생성되었는가?
- [ ] 글로벌 락(`full_scan_runner.lock`)이 정상 해제되었는가?

### 6. Audit All Closed Hours (전체 닫힌 시간대 정밀 전수 감사)
- [ ] 총 수집된 시간당 파티션 수가 사전에 정의된 규격(예: 76개 파티션/시간)과 정확히 일치하는가?
- [ ] `scripts/audit_72h_soak.py` 실행 결과:
  - `invalid_json == 0`
  - `schema_mismatch == 0`
  - `non_finite_numeric == 0`
  - `malformed_timestamps == 0`
  - `scan_failures == 0`
  - `quarantine.records == 0`

### 7. S3 Restore Validation (S3 원격 복원 및 무결성 대조)
- [ ] S3 버킷에 업로드된 ZST 압축 파일과 아카이브 영수증의 개수가 로컬 생성 수량과 일치하는가?
- [ ] 무작위 샘플링 복원 테스트에서 원격 S3 객체의 SHA-256 해시가 로컬 영수증의 해시와 100% 일치하는가?

### 8. Disk State (디스크 및 파일시스템 상태)
- [ ] 소크 완료 후 디스크 사용량이 사전 용량 계획(EBS 200 GiB) 범위 내에 안정적으로 유지되었는가? (예: 사용률 < 80%)
- [ ] 파일시스템 Inode 고갈 또는 디스크 손상 흔적이 없는가? (`xfs_repair -n` 또는 `df -i` 확인)

### 9. CloudWatch State (모니터링 및 알람 상태)
- [ ] 72시간 동안 발생한 치명적 알람(Critical Alarm)이 `0`건인가?
- [ ] 메트릭 수집 누락 구간이 허용치 이내인가?

### 10. Final Report Generator (결정론적 최종 보고서 생성)
- [ ] `scripts/generate_72h_final_report.py`가 에러 없이 실행되어 `FINAL_72H_REPORT.json` 및 서머리를 산출하였는가?

### 11. No Cleanup (원천 데이터 무삭제 원칙 준수)
- [ ] 로컬 및 원격 아카이브 영수증의 `cleanup_completed_at` 필드가 모두 `null`인가?
- [ ] 소크 전 기간 동안 S3 `DeleteObject` API 호출이 단 1건도 발생하지 않았는가? (`0`건)

### 12. No Alpha (연구/거래 경계 엄격 격리)
- [ ] 수집 환경 내에서 알파 모델 훈련, 전략 파라미터 튜닝, 수익률 시뮬레이션, 페이퍼/실거래 주문 코드가 실행되지 않았는가?

### 13. Final Classification (최종 종합 판정 도출)
- [ ] 위 1~12번 항목의 객관적 증적을 종합하여 최종 상태가 엄격히 판정되었는가?
  - `PASS`: 모든 게이트 100% 무결 충족
  - `PASS WITH DEVIATION`: 데이터 손실 없는 운영상 사소한 편차 존재
  - `FAIL`: 데이터 손상, 손실 또는 핵심 게이트 위반
