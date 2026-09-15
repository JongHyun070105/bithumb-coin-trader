# AWS 30H 무인 검증 V3 사전 점검 및 봉인 확인서 (2026-09-15)

## 1. 판정과 불변 상태

- **30H 승인 준비 완료 (READY TO AUTHORIZE NEW 30H)**: **YES** — 별도 인간 승인 대기
- **30H 실행 시작 (NEW 30H STARTED)**: **NO**
- **launch_authorized**: `false`
- **actual_start_time_utc**: `null`
- **actual_start_evidence**: `absent`
- **V3 systemd 실행 / validation 프로세스**: `0 / 0`
- **ALPHA / PAPER / LIVE / PRIVATE API**: `UNPROVEN / NOT STARTED / DISABLED / DISABLED`
- **V2 30H (`aws-validation-30h-20260912-6576f63`)**: `FAIL — IMMUTABLE`; 연구용 영구 보존 (`KEEP_RESEARCH`)
- **Old 72H (`aws-72h-soak-20260905-8017b83e`)**: `KEEP_RESEARCH`; 마이크로스트럭처 preregistration `CYCLE-01` 연구 보존
- **과거 증거/로그/리시트**: `KEEP_EVIDENCE`; 삭제 0건 (`FILES DELETED = 0`, `BYTES DELETED = 0`)

---

## 2. V3 암호학적 신원

| 항목 | 값 |
|---|---|
| 준비 브랜치 | `codex/aws-30h-v3-preparation-20260915-e9d5d5a` |
| collector epoch | `aws-validation-30h-20260915-v3` |
| collector run ID | `aws-validation-30h-run-20260915T013000Z-v3` |
| S3 prefix | `market-data/temporary/aws-validation-30h-20260915-v3` |
| guest runtime worktree | `/var/lib/bitcoin-trader/runtime-worktrees/aws-validation-30h-20260915-v3` |
| local runtime root | `/var/lib/bitcoin-trader/30h-validation/aws-validation-30h-20260915-v3` |
| systemd unit name | `bitcoin-trader-30h-aws-validation-30h-run-20260915T013000Z-v3.service` |
| canonical config fingerprint | `ff913e3c1468e6827e133a3c11a62421d2ac1bb07d6645fe32079f8eb49eedee` |

Run ID 안의 시각은 식별자 생성 시각이며 실제 시작 증거가 아니다. 실제 시작은 별도 승인된 실행에서 생성되는 `actual_start_time_utc`만 권위가 있으며 현재는 `null`이다.

---

## 3. 봉인 아티팩트

| 파일 | SHA-256 | 검증 상태 |
|---|---|:---:|
| `aws-validation-30h-20260915-v3.feed-universe.json` | `52adb4e5f7dc06cbc85ae1926c043ffd44dd47b86f70c09e248c16568e428500` | 봉인 완료 (76 feeds) |
| `aws-validation-30h-20260915-v3.timing-contract.json` | `adfcc4fc24d8cda14a8935a48a90683b56907c955f62437b326bb46905000284` | 봉인 완료 (Strictly-Next 30h) |
| `aws-validation-30h-20260915-v3.heartbeat-contract.json` | `e8d6ebfa15425daecfbefd482dea129a43d79b3444db67e958701c4ecc0ad929` | 봉인 완료 (probe 10s, max 30s) |
| `aws-validation-30h-20260915-v3.runtime.json` | `b79e702e7c634c34e81b5e407de63763ddffb7132b14707e0dc39d215c2e08f7` | 봉인 완료 |
| `aws-validation-30h-20260915-v3.launch-command.json` | `48648a56fce3907048bfd0be5a774febe88bfa065c79b00edb0c969d954478a6` | 봉인 완료 |
| `aws-validation-30h-20260915-v3.launch-wrapper.sh` | `509968d0b9e63e226864f5f4bc09641c624e248b7727f6316ea3aee834d3b0d3` | 봉인 완료 |
| `aws-validation-30h-20260915-v3.launch-provenance.json` | `50ac75d44842059031caaade55a40d221b9518c34d06a7dd1cec921f6260053e` | 봉인 완료 |
| `aws-validation-30h-20260915-v3.authorization-evidence.json` | `84c7f16a99c13a947409fb412c950f19e0c9b477e4a22dff8277e585d8d1a65f` | `launch_authorized=false` |

봉인된 실행 경로는 operator → guest `launch.sh` → `launch_short_smoke_transient.py` → `TransientLaunchConfig` → `render_systemd_run()` → `systemd-run` → `run_bounded_short_smoke.py` → collector / publisher / archive scheduler 순서다.

Render-only 사전 검증은 `Restart=no`, `--uid=bitcoin-trader`, `RuntimeMaxSec=111900s`, `--required-qualifying-full-hours 30`, `--maximum-collection-window-seconds 111600`을 완벽하게 확인했다. `--launch`는 절대 전달하지 않았다.

---

## 4. 타이밍 및 Feed 계약

| 설정 | 값 |
|---|---:|
| qualification rule | `STRICTLY_NEXT_UTC_HOUR` |
| required qualifying full hours | `30` (후보 30개 코호트) |
| feed universe | `76` = Bithumb `60` + Binance `8` + Upbit `8` |
| total expected coverage slots | `2,280` slots |
| maximum collection window | `111,600s` (31시간 고정 ceiling) |
| finalization timeout | `180s` |
| supervisor shutdown grace | `45s` |
| supervisor hard ceiling | `111,825s` ($111600 + 180 + 45$) |
| systemd runtime maximum | `111,900s` ($111825 + 75$) |
| archive poll / grace | `30s / 600s` |
| cleanup | `false` |
| heartbeat probe / timeout / max gap | `10s / 10s / 30s` (31s FAIL) |

---

## 5. 인프라 및 환경 Gate 검증

- **Authoritative EC2 Target**:
  - Instance ID: `i-008bc503c1136349f`
  - Instance State: `running`
  - Instance Type: `t3.medium`
  - Availability Zone: `ap-northeast-2a`
  - Attached EBS: `vol-0d46ca4af0d463549` (gp3, 200 GiB, encrypted with KMS)
  - Execution User: non-root (`ssm-user` / `bitcoin-trader`)
- **Storage Gate**:
  - 디스크 전체 크기: `199.93 GiB` (214,668,652,544 B)
  - 디스크 사용량: `97.30 GiB` (104,479,367,168 B, 48.67%)
  - 현재 가용 공간: **`102.62 GiB`** (110,189,285,376 B, **51.33%**)
  - 디스크 게이트 기준 ($\ge 50\text{ GiB}$): **PASS** (+52.62 GiB 초과 여유)
- **Active Process Gate**:
  - Running Collector: **0건** (`NO`)
  - Running Archive Scheduler: **0건** (`NO`)
  - Running 30H Supervisor: **0건** (`NO`)
  - Running Bitcoin systemd units: **0건**
- **S3 Official Namespace Emptiness Gate**:
  - Bucket: `bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433`
  - Prefix: `market-data/temporary/aws-validation-30h-20260915-v3`
  - Object Count: **0건** (`KeyCount = 0`, 완전 공백 입증)
  - 진단 객체 업로드: 없음 (`0 written`)
- **IAM Runtime Safety Gate**:
  - Permissions Boundary: `arn:aws:iam::080109295433:policy/bitcoin-trader-collector-boundary` version `v6`
  - 권한 범위: 퍼블릭 마켓 데이터 수집, `market-data/temporary/aws-validation-*/*` S3 Get/Put, CloudWatch Metrics/Logs
  - 제한 사항: 비인가/트레이딩 API, 계좌 잔고, 주문, IAM 변경 권한 일체 없음 -> **PASS**
- **Terraform Read-Only Gate**:
  - State Lineage: `5e8ff4b0-1d32-ed2e-9ed0-a9e68d3f3ccf`, Serial: `51`, Resources: `27`
  - Plan 결과: **0 to add, 0 to change, 0 to destroy** (`No changes. Your infrastructure matches the configuration.`) -> **PASS**

---

## 6. 결론 및 Launch 전 완전 정지

```text
READY TO AUTHORIZE NEW 30H: YES
LAUNCH AUTHORIZED: FALSE
NEW 30H STARTED: NO
STOPPED BEFORE LAUNCH: YES
```

본 확인서는 V3 30H 무인 검증을 위한 모든 과학적·인프라적 사전 점검과 봉인이 완료되었음을 입증하며, 실제 실행은 별도의 명시적 인간 승인이 있기 전까지 결코 시작되지 않습니다.
