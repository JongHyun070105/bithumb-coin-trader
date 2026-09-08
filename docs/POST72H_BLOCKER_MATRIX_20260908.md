# Post-72H Audit Blocker Matrix (2026-09-08)

## 1. 개요 (Overview)

본 문서는 72시간 무중단 수집기(Collector) 실행(`aws-72h-soak-20260905-8017b83e`)에 대한 사후 과학 감사(Post-72H Final Audit)를 재개하기 위해 요구되는 모든 증거 항목과 게이트의 현재 상태, 신뢰도, 결측 지점 및 해결 경로를 정의한다.

현재 AWS 인증은 복구되었으나, 프로비저너 역할에 게스트 검증 명령 실행 권한(`ssm:SendCommand`)이 부여되지 않아 게스트 내부 감사 단계에서 과학 감사가 **FAIL-CLOSED 차단(BLOCKED)**되어 있다.

---

## 2. 증거 및 프로세스 게이트 매트릭스 (Blocker Matrix)

| 증거 / 게이트 (Evidence / Gate) | 가용 여부 (Available?) | 권위성 (Authoritative?) | 현재 출처 (Current Source) | 결측 조치 / 경로 (Missing Action / Path) | Astra 재개 전 필수 여부 (Needed before Resume?) |
|---|---|---|---|---|---|
| **런타임 봉인 (Runtime Seal)** | 예 (YES) | 예 (사전 정의 권위) | `infra/aws/seals/aws-72h-soak-20260905.runtime.json` (SHA: `cb3dee...`) | 없음 (로컬 봉인 완료) | 완료 (YES) |
| **기동 프로비넌스 (Launch Provenance)** | 예 (YES) | 예 (기동 시점 권위) | `infra/aws/seals/aws-72h-soak-20260905.launch-provenance.json` | 없음 (로컬 기록 완료) | 완료 (YES) |
| **EC2 인스턴스 활성 상태 (Power State)** | 예 (YES) | 부분 (전원 상태 한정) | AWS EC2 `DescribeInstances` (`i-008bc503c1136349f`, `running`) | 없음 (수집기 프로세스 완주와 무관) | 완료 (수집기 증거 대체 불가) |
| **SSM 에이전트 핑 (Control Plane Ping)** | 예 (YES) | 예 (SSM 제어 플레인) | AWS SSM `DescribeInstanceInformation` (`Online`) | 없음 (명령 실행 권한과는 별개) | 완료 (수집기 증거 대체 불가) |
| **실제 기동 시각 (Actual Start)** | 아니오 (NO) | 미확인 | 없음 (`launch_provenance.created_at_utc` 대용 불가) | 게스트 systemd 유닛 시작 타임스탬프 또는 첫 RAW 레코드 타임스탬프 수집 (`ssm:SendCommand` 필요) | **필수 (YES)** |
| **프로세스 / systemd 라이프사이클** | 아니오 (NO) | 미확인 | 없음 | `systemctl show` (`ActiveState`, `SubState`, `Result`, `ExecMainStatus`, `ExecMainCode`) 및 `ps aux` 확인 | **필수 (YES)** |
| **자연 완주 검증 (Natural Completion)** | 아니오 (NO) | 미확인 | 없음 (CloudWatch 알람/S3 객체로 추론 금지) | 게스트 슈퍼바이저 결과 파일 및 systemd `Result=success`, 종료 코드 `0` 확인 | **필수 (YES)** |
| **슈퍼바이저 완주 결과 (Supervisor Result)** | 아니오 (NO) | 미확인 | 없음 | `/var/lib/bitcoin-trader/72h-soak/.../supervisor_result.json` 확인 및 해시 바인딩 | **필수 (YES)** |
| **최종 RAW 쓰기 및 플러시 (Final RAW Write)** | 아니오 (NO) | 미확인 | 없음 | RAW 디렉토리 내 최신 파티션 파일 `stat`, 마지막 레코드 타임스탬프, 활성 파티션 클로저 확인 | **필수 (YES)** |
| **런타임 카운터 (WriterErrors / QueueDrops)** | 아니오 (NO) | 미확인 | 없음 (CloudWatch GetMetricStatistics 권한 거부됨) | 게스트 `collector_metrics.json` 직접 수집 (결측치를 0으로 임의 대체 금지) | **필수 (YES)** |
| **원격 아카이브 코호트 (S3 Archive Cohorts)** | 예 (YES, 후보 수준) | 비권위 (후보 증거) | S3 `market-data/temporary/...` (228개 객체, 3개 시간대 `05`시만 업로드 확인) | 게스트 로컬 디스크 상 미압축/미업로드 파티션 현황 및 아카이브 영수증 대조 | **필수 (YES)** |
| **아카이브 영수증 (Archive Receipts)** | 아니오 (NO) | 미확인 | 없음 (S3에는 영수증 미저장, DataPlane 403) | 게스트 `/var/lib/bitcoin-trader/.../archive-receipts/*.json` 확인 | **필수 (YES)** |
| **풀스캔 리포트 (Full-Scan Reports)** | 아니오 (NO) | 미확인 | 없음 | 게스트 `/var/lib/bitcoin-trader/.../archive-receipts/full_scan_*_report.json` 확인 | **필수 (YES)** |
| **디스크 임계치 및 사용량 (Disk Usage)** | 부분 (PARTIAL) | 비권위 (알람 상태) | CloudWatch `DiskUsedPercent` 알람 `ALARM` (데이터 수신 중단으로 breaching 전이) | 게스트 `df -h`, `du -sh /var/lib/bitcoin-trader` 직접 측정 | **필수 (YES)** |
| **재연결 폭풍 / 저널 에러 (Reconnects)** | 아니오 (NO) | 미확인 | 없음 (CloudWatch Logs `DescribeLogStreams` 거부됨) | 게스트 `journalctl -u bitcoin-trader-72h-soak*` 필터링 수집 | **필수 (YES)** |
| **에포크 루트 입력 (Epoch Root Inputs)** | 아니오 (NO) | 미확인 | 없음 | 완주 확인 및 권위적 에포크 계약 체결 후 게스트 아카이브/RAW 해시 매니페스트 취합 | 프로세스 완주 확인 후 진행 |
| **심층 데이터 품질 검증 (Deep DQ)** | 아니오 (NO) | 미확인 | 없음 (실행 불가) | 에포크 루트 바인딩 완료 후 `verify_soak_reproducibility` 및 DQ 파이프라인 실행 | 에포크 루트 완료 후 진행 |
| **적격성 평가 (Qualification)** | 아니오 (NO) | 미확인 | 없음 (실행 불가) | 심층 DQ 통과 시에만 진행 (Case D 미충족 시 중단) | DQ 완료 후 진행 |

---

## 3. 핵심 차단 원인 분석 (Root Blocker Analysis)

1. **원격 S3 및 CloudWatch만으로는 완주 판정 불가:**
   - S3 버킷에 228개 아카이브 파일(3일간 매일 05시 분량)이 존재하나, 나머지 시간대의 데이터가 로컬 디스크에 온전히 존재하는지, 수집기가 정상 완주했는지는 S3만으로 알 수 없다.
   - CloudWatch 메트릭 알람은 2026-09-08 05:55 UTC경부터 메트릭 미수신으로 인해 `ALARM` 상태로 전이되었으나, 이는 정상 완주 후 프로세스 종료에 의한 것인지 이상 중단인지 외부에서 구분할 수 없다.
2. **게스트 파일시스템 및 systemd 상태 접근 단절:**
   - `terraform-provisioner` IAM 정책 상 `ssm:StartSession`(대화형 셸)만 허용되어 있고, 비대화형 자동 감사 증거 수집에 필요한 `ssm:SendCommand`가 의도적으로 제외되어 있다.
   - 따라서 게스트 내부의 `systemctl`, `collector_metrics.json`, `supervisor_result.json`, 로컬 RAW 파티션 상태를 읽지 못하는 것이 유일한 원천 블로커(Single Root Blocker)이다.
