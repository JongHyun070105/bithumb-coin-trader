# AWS 30H 무인 검증 V2 사전 점검 및 봉인 확인서 (2026-09-12)

## 1. 판정과 불변 상태

- **30H 승인 준비 완료 (READY TO AUTHORIZE 30H)**: **YES** — 별도 인간 승인 대기
- **30H 실행 시작 (30H STARTED)**: **NO**
- **launch_authorized**: `false`
- **actual_start_time_utc**: `null`
- **V2 systemd 실행 / validation 프로세스**: `0 / 0`
- **ALPHA / PAPER / LIVE / PRIVATE API**: `UNPROVEN / NOT STARTED / DISABLED / DISABLED`
- **V0 / V1**: `SUPERSEDED`; 삭제·재작성·식별자 재사용 없이 역사적 준비 증거로 보존

PR #4는 regular merge commit `6576f632b3f44eb68645bad4304665c0ea87512d`로 병합됐다. 부모는 기존 main
`00464e585e50308bead0924940301d9264ee2dcf`와 PR head
`b43d6b9bf0fe7b06edfccbd6d38029dae32b47c9`이며 GitHub merge 시각은
`2026-09-12T10:31:19Z`이다. 변경 파일은 `tests/test_bounded_supervisor.py` 하나뿐이고 production runtime source 변경은 없다.

병합 후 정확한 main에서 supervisor `12 passed`, 전체 suite `1034 passed, 2 skipped`, Pyright 0 errors/0 warnings,
compileall 및 `git diff --check`가 통과했다.

## 2. V2 암호학적 신원

| 항목 | 값 |
|---|---|
| 준비 브랜치 | `codex/aws-30h-prep-v2-20260912-6576f63` |
| runtime commit | `6576f632b3f44eb68645bad4304665c0ea87512d` |
| Git tree | `5a936a1b3a48f66e938cbe234a853ec7d8c6b027` |
| deterministic `git archive --format=tar` SHA-256 | `e9270cbe17f6bfa8a83122a037d35c5acf836aad07adba3531ec0ad6e0609a42` |
| epoch | `aws-validation-30h-20260912-6576f63` |
| run ID | `aws-validation-30h-run-20260912T103507Z-6576f63` |
| S3 prefix | `market-data/temporary/aws-validation-30h-20260912-6576f63` |
| guest runtime worktree | `/var/lib/bitcoin-trader/runtime-worktrees/aws-validation-30h-20260912-6576f63` |
| local runtime root | `/var/lib/bitcoin-trader/30h-validation/aws-validation-30h-20260912-6576f63` |
| canonical config fingerprint | `d330923d6c5d57a1399317b315fcbcabcd512624db6c1dd1f4a5420a6089fecf` |

Run ID 안의 시각은 식별자 생성 시각이며 실제 시작 증거가 아니다. 실제 시작은 별도 승인된 실행에서 생성되는
`actual_start_time_utc`만 권위가 있으며 현재는 `null`이다.

## 3. 봉인 아티팩트

| 파일 | SHA-256 | 로컬/게스트 |
|---|---|---|
| `aws-validation-30h-20260912-6576f63.runtime.json` | `bec086f8f587ee3c8261f91ebd3c535145f7c61dfe4cd250c29c382d086b8ff1` | 일치 |
| `aws-validation-30h-20260912-6576f63.launch-command.json` | `3e2184e000be0310e38a712df93ce9cd3914e4f55bc2455493804b574066f41f` | 일치 |
| `aws-validation-30h-20260912-6576f63.launch-provenance.json` | `2ff4efe7843e433941b517d31dd067698e409657001aa5007dd9ebc2aa691419` | 일치 |
| `aws-validation-30h-20260912-6576f63.launch-wrapper.sh` / guest `launch.sh` | `125087ddccb9870f731a1fd67297efe49402f64b744fb6763028df5db38e6931` | 일치 |

봉인된 실행 경로는 operator → guest `launch.sh` → `launch_short_smoke_transient.py` →
`TransientLaunchConfig` → `render_systemd_run()` → `systemd-run` → `run_bounded_short_smoke.py` →
collector / publisher / archive scheduler 순서다. 최종 검토 뒤에 추가되는 실행 계층은 없다.

Render-only 검증은 `Restart=no`, `--uid=bitcoin-trader`, `RuntimeMaxSec=108400s`, 정확히 하나의 supervisor
`--collection-duration-seconds 108000`을 확인했다. `--launch`는 전달하지 않았다.

## 4. 타이밍과 feed 계약

| 설정 | 값 |
|---|---:|
| collection duration | `108000s` |
| finalization timeout | `180s` |
| supervisor hard ceiling | `108300s` |
| systemd runtime maximum | `108400s` |
| archive poll / grace | `30s / 600s` |
| cleanup | `false` |
| feeds | `76` = Bithumb `60` + Binance `8` + Upbit `8` |

## 5. 게스트 검증

- Detached worktree commit/tree/archive SHA-256은 2절 값과 정확히 일치했고 `git status --porcelain`은 비어 있다.
- 기존 전용 Python 3.11 테스트 환경과 새 worktree `PYTHONPATH`를 사용한 7개 core suite는
  **110 passed, 0 failed in 29.65s**였다. production venv에는 pytest가 없으므로 테스트 실행에 사용하지 않았다.
- 네 아티팩트의 게스트 SHA-256은 로컬과 일치한다.
- Render-only 뒤에도 guest V2 output은 없고, V2 systemd 유닛과 validation 프로세스는 각각 0개다.

## 6. S3, IAM, Terraform

- V2 active prefix `KeyCount=0`; 진단 객체를 쓰지 않았다.
- Runtime role은 permissions boundary
  `arn:aws:iam::080109295433:policy/bitcoin-trader-collector-boundary` default `v6`를 유지한다.
- IAM simulation: V2 prefix `GetObject=allowed`, `PutObject=allowed`; `ListBucket`, `DeleteObject`, canonical namespace,
  다른 temporary namespace, 다른 bucket, Secrets Manager/SSM Parameter/KMS decrypt, IAM credential/pass-role,
  STS assume-role 및 invoke/write 계열은 모두 `implicitDeny`다.
- TEMP IAM CLEANUP: **BLOCKED / NOT PERFORMED**. root 미사용, IAM mutation 없음.
- Terraform authoritative local state: lineage `5e8ff4b0-1d32-ed2e-9ed0-a9e68d3f3ccf`, serial `51`, resources `27`.
  Refresh plan 결과는 **0 to add, 0 to change, 0 to destroy**. backend migration/reconfigure/apply 없음.

## 7. 디스크 용량

현재 filesystem은 `214,668,652,544` bytes 중 `81,247,207,424` bytes 사용, `133,421,445,120` bytes 가용(38%)이다.

- Fresh45 실측 `1,131,774,860` bytes를 30H로 선형 환산: `45,270,994,400` bytes 추가,
  예상 총 사용률 약 **58.94%** — 정상 예상률 **PASS**.
- 72H 실측 `71,067,784,459` bytes를 30H로 환산: 약 `29,611,576,858` bytes 추가,
  예상 총 사용률 약 **51.64%**.
- Fresh45 환산률의 지속 2배: `90,541,988,800` bytes 추가, 예상 총 사용률 약 **80.03%**로 high 80%에 도달한다.

따라서 reasonable burst margin은 **PASS WITH ADVISORY**다. 약 2배 지속 burst에서 high 경보를 예상하고,
90% critical fail-closed 정책과 검증되지 않은 raw 자동삭제 금지를 유지한다. 인프라 resize는 승인되지 않았다.

## 8. 사후 증거 계약

`30H = 30개 완전 cohort`로 하드코딩하지 않는다. 별도 승인된 실제 실행 후
`[actual_start_time_utc, actual collector completion time)`로 touched raw cohort와 600초 grace까지 완전히 닫힌
eligible archive cohort를 계산한다. 첫/마지막 partial cohort는 이 구간과 봉인 계약에 따라 처리한다.

각 eligible cohort에는 RAW 76, COMPRESSED 76, qualifying terminal receipts 76, failed receipts 0,
restore PASS, explicit FULLSCAN references 152가 필요하다. 중복 feed, wrong date/cohort, foreign run,
epoch/run/runtime identity 불일치 및 count-only fullscan은 fail-closed blocker다.

## 9. 독립 사전 검토

- Critical: **0**
- Important: **0**
- Minor: **0**
- Advisory: **1** — 약 2배 지속 ingestion에서 80% high 디스크 임계치 접근/초과.

결론: **READY TO AUTHORIZE 30H = YES**, 단 이것은 실행 승인이 아니다. V2 branch를 병합하지 않고,
`launch_authorized=false`, `actual_start_time_utc=null` 상태에서 정지한다. 실제 30H 시작은 별도 명시적 승인 요청이 필요하다.
