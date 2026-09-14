# AWS 30H 독립 실패 포렌식 (2026-09-14)

## 결론

공식 30H 식별자는 정확히 한 번 실행됐고 108,000초 수집 구간을 채웠지만, 수집기 최종 manifest flush가
300초 hard ceiling 안에 끝나지 않았다. 슈퍼바이저는 수집기를 SIGTERM으로 종료했고
`result.json`은 `collector_exit_code=-15`, `forced_timeout=true`,
`final_manifest_flush_observed=false`, `overall_status=FAIL`을 기록했다.

또한 봉인 계약이 요구하는 qualifying UTC cohort 30개 중 3개 cohort에서 Bithumb 8개 feed partition이
생성되지 않았다. 따라서 RAW, compressed archive, terminal receipt, restore, fullscan의 exact-coverage
계약도 충족하지 못했다.

최종 판정은 **30H OVERALL = FAIL**이다. 성공 증거 PR/병합, 인프라 검증 종료 선언, 24/7 public
microstructure research 시작을 모두 중단한다. 이 식별자는 이미 소비됐으므로 다시 실행하지 않는다.

## 범위와 비변경 원칙

감사는 Git/GitHub, 게스트, systemd journal, 게스트의 작은 결과물, receipt/fullscan 집계, S3의 공식
prefix inventory를 읽기 전용으로 교차검증했다. 다음 작업은 하지 않았다.

- 공식 RAW, manifest, receipt, fullscan 또는 S3 객체의 수정·보완·재생성
- 같은 epoch/run ID 재실행
- IAM/Terraform 변경
- archive 수동 재실행 또는 probe 업로드
- trading, paper/live mode, private exchange API 사용
- research service 준비 또는 시작

게스트 원본 `result.json`, `collector-lifecycle.json`, `collector_metrics.json`은 바이트 그대로
`evidence/aws-validation-30h-20260912-6576f63/post-run/`에 복사했고 게스트 SHA-256과 일치시켰다.
대용량 RAW와 270 KB supervisor log는 Git에 복사하지 않고 크기와 SHA-256만 요약 증거에 기록했다.

## 1. Git 및 GitHub 기준점

- authoritative `origin/main`:
  `6576f632b3f44eb68645bad4304665c0ea87512d`
- PR #4: GitHub에서 regular merge로 확인, 변경 파일은
  `tests/test_bounded_supervisor.py` 하나
- evidence branch:
  `codex/aws-30h-prep-v2-20260912-6576f63`
- post-run 증거 추가 전 branch HEAD:
  `776c67d63b1794aa1f9b9163865e62ae472d1d2f`
- parent chain:
  `776c67d` → `27d9a0811236341835d8d8e9cfce4dc7c91f3f4e` →
  `0aa7ae073b3e14dfdbc53db3e98c5215ef8fbf4b` →
  `6576f632b3f44eb68645bad4304665c0ea87512d`

원격 fetch 후 main과 evidence branch가 예상값과 일치했고, 기존 준비·승인·actual-start 커밋은 수정하지
않았다. 기본 main worktree에 원래 존재하던 untracked `test-results/`도 건드리지 않았다.

## 2. 공식 식별자와 provenance

| 항목 | 값 |
|---|---|
| runtime commit | `6576f632b3f44eb68645bad4304665c0ea87512d` |
| runtime Git tree | `5a936a1b3a48f66e938cbe234a853ec7d8c6b027` |
| epoch | `aws-validation-30h-20260912-6576f63` |
| run ID | `aws-validation-30h-run-20260912T103507Z-6576f63` |
| config fingerprint | `d330923d6c5d57a1399317b315fcbcabcd512624db6c1dd1f4a5420a6089fecf` |
| actual start | `2026-09-12T11:24:51Z` |
| supervisor end | `2026-09-13T17:29:52.284325+00:00` |

게스트 detached runtime worktree는 위 commit에 있고 clean 상태였다. 봉인된 runtime, launch command,
launch provenance, launch wrapper의 게스트 해시는 저장소의 원래 해시와 일치했다. 실행 종료 후 같은
epoch를 포함하는 collector/supervisor/archive/publisher 프로세스는 0개였다.

## 3. exactly-once와 systemd

transient unit은 `--collect` 때문에 현재 unit registry에서 garbage-collected됐지만 journal에는 정확히
다음 lifecycle event 5개가 남아 있다.

- Starting: 1
- Started: 1, `2026-09-12T11:24:51.951302+00:00`
- Main process exited: 1, `status=1/FAILURE`,
  `2026-09-13T17:29:52.301536+00:00`
- Failed with result `exit-code`: 1
- CPU consumption summary: 1

봉인 설정은 `Restart=no`이고 journal restart event는 0개다. Git의 actual-start evidence도 하나,
동일 식별자의 게스트 run root도 하나다. 따라서 공식 실행 횟수는 1, restart는 0으로 판정한다.
unit이 사라진 사실 자체는 실패 근거로 사용하지 않았다.

## 4. 프로세스 계약

게스트 원본 `result.json`:

| 필드 | 관측값 | 판정 |
|---|---:|---|
| `collection_duration_seconds` | 108000.0 | 계약 일치 |
| `elapsed_seconds` | 108300.277294 | full duration 충족 |
| `collector_exit_code` | -15 | FAIL |
| `received_signal` | null | 정보 |
| `forced_timeout` | true | FAIL |
| `full_duration_satisfied` | true | PASS |
| `final_metrics_valid` | true | PASS |
| `final_manifest_flush_observed` | false | FAIL |
| `overall_status` | FAIL | FAIL |

`publisher_exit_code=0`, `archive_scheduler_exit_code=0`이다.
`publisher_stopped_after_collector=false`는 프롬프트와 생산 계약에 따라 실패 조건으로 사용하지 않았다.

`collector-lifecycle.json`은 `phase=FINALIZING`,
`final_manifest_flush_observed=false`, `manifest_count=0`으로 멈췄다. 따라서 30H PROCESS는
명백한 FAIL이다.

## 5. 최종 수집 지표

최종 metrics 자체는 유효하다.

| 지표 | 값 |
|---|---:|
| configured feeds | 76 = Bithumb 60 + Binance 8 + Upbit 8 |
| total messages | 14,545,571 |
| writer errors | 0 |
| queue dropped events | 0 |
| queue backpressure events | 0 |
| unpersisted events | 0 |
| fatal writer error | null |
| malformed/quarantined | 0 |
| disconnects / reconnects | 0 / 0 |
| active partition files at final metrics | 0 |

교환소별 message 수는 Binance 6,856,301, Bithumb 5,419,227, Upbit 2,270,043이다. 이 지표는
수집 프로세스가 108,000초 동안 대량 데이터를 수신·기록했음을 보여주지만, hour/feed exact coverage를
대체하지 않는다.

## 6. cohort 파생과 exact coverage

실제 시간으로 파생한 touched cohort는 31개:
`2026-09-12_11`부터 `2026-09-13_17`까지다.

600초 grace를 포함한 봉인 contract가 archive/fullscan 대상으로 인정하는 qualifying cohort는 30개:
`2026-09-12_11`부터 `2026-09-13_16`까지다. 시작 partial hour인 `_11`은 76개가 모두
존재하고 grace 전에 닫혀 qualifying이다. 종료 partial hour `2026-09-13_17`은 qualifying에서
제외한다.

qualifying cohort 중 exact 76개를 만족한 cohort는 27개뿐이다.

| cohort | 관측/기대 | 누락 |
|---|---:|---|
| `2026-09-12_17` | 74 / 76 | Bithumb KRW-MANA ticker, trade |
| `2026-09-12_18` | 72 / 76 | Bithumb KRW-AXS ticker, trade; KRW-MANA ticker, trade |
| `2026-09-12_19` | 74 / 76 | Bithumb KRW-MANA ticker, trade |

종료 partial `2026-09-13_17`에도 KRW-MANA ticker/trade 2개가 없지만, 이는 qualifying 실패 수
8개에는 포함하지 않았다. touched 31시간의 theoretical 2,356개 중 RAW 파일은 2,346개다.

누락은 모두 특정 Bithumb 저활동 종목의 ticker/trade다. 최종 metrics에는 writer, queue,
connection 오류가 없다. 따라서 현재 증거는 writer 유실보다 해당 hour에 실제 메시지가 없어 파일이
생성되지 않은 경우와 더 일치하지만, upstream 무발행을 독립적으로 증명하는 event ledger는 없다.
어느 경우든 기존 계약은 `RAW partitions = 76 exact`를 요구하므로 결과는 FAIL이다.

## 7. archive, receipt, restore와 S3

| 항목 | 관측 | 요구 | 판정 |
|---|---:|---:|---|
| terminal receipts | 2,272 | 2,280 | FAIL |
| local compressed files | 2,272 | 2,280 | FAIL |
| remote compressed objects | 2,272 | 2,280 | FAIL |
| remote zero-byte objects | 0 | 0 | PASS |
| failed receipts among present receipts | 0 | 0 | PASS |
| restore-verified present receipts | 2,272 | 2,272 | PASS |

존재하는 receipt 2,272개는 모두 `CLEANUP_ELIGIBLE`, failure reason null, restore timestamp
non-null이고 epoch/run ID가 정확했다. S3 key도 모두 공식 prefix 안의 고유한
`.jsonl.zst` 객체이고 duplicate/foreign/unexpected-extension/zero-byte 객체는 발견되지 않았다.
그러나 8개 qualifying feed가 처음부터 없어서 receipt와 remote object도 8개 부족하다.

따라서 “존재하는 receipt의 restore 검증”은 PASS지만 “요구되는 2,280개 전체 restore coverage”는
FAIL이다. 전체 30H ARCHIVE 판정도 FAIL이다.

## 8. fullscan과 데이터 품질

autonomous fullscan report는 qualifying cohort마다 하나씩 30개다. present input에 대한 집계:

| 지표 | 값 |
|---|---:|
| RAW + compressed input references | 4,544 / expected 4,560 |
| records (RAW + compressed 양쪽 합계) | 28,591,468 |
| valid records | 28,591,468 |
| invalid JSON | 0 |
| schema mismatch | 0 |
| missing required fields | 0 |
| non-finite numeric | 0 |
| malformed timestamp | 0 |
| unknown market/feed | 0 |
| scan failures | 0 |
| quarantine files | 0 |

각 report의 present-input integrity는 PASS지만, `_17`, `_18`, `_19`의 input reference가
각각 148, 144, 148로 152 exact 계약을 어긴다. present data의 parse/schema 품질을 전체 계약
PASS로 승격할 수 없다. 30H DATA QUALITY는 exact feed coverage 누락 때문에 FAIL이다.

## 9. 최종 manifest flush의 직접 원인

수집 종료 시 `scripts/run_cross_market_collector.py`는
`collector.generate_all_manifests()`를 호출한다. 해당 메서드는 run 동안 touched된 모든 partition
파일을 순회하고, `RawMicrostructureStorage.generate_partition_manifest()`는 각 파일 전체를 다시
읽으며 JSON parse, hash, DQ 통계를 계산한다.

이번 run root는 23,176,503,335 bytes이고 RAW만 22,605,908,385 bytes, 2,346개 파일이다.
archive scheduler가 닫힌 hour manifest를 이미 생성했는데도 finalizer는 touched file 전부를 다시
처리했다. finalization 구간에 1,675개 manifest가 다시 기록됐지만 300초 hard ceiling에 도달했다.
종료 partial의 RAW 24개는 manifest 없이 남았다.

`manifests = collector.generate_all_manifests()` 대입이 완전히 반환된 뒤에만 이뤄지므로, 중간에
상당수를 처리했어도 lifecycle의 `manifest_count`는 0으로 남는다. supervisor log에는 Python
traceback이나 writer failure가 없고 마지막에 archive scheduler가 signal 15를 받은 기록이 있다.

따라서 주 프로세스 실패의 직접 원인은 **대용량 전체 재스캔형 final manifest flush가 봉인된 hard
ceiling 안에 완료되지 못한 runtime/evidence-finalization 설계**다. 이는 auditor false negative가
아니다.

## 10. strict auditor와 epoch manifest

authoritative run contract를 만들기 위해 strict composer를 실제 봉인 파일과 actual-start evidence에
실행하면 exit 2와 다음 오류가 재현된다.

`CORRUPT_ACTUAL_START_EVIDENCE: ACTUAL_START_RUNTIME_COMMIT_MISMATCH`

값 자체의 commit/fingerprint는 일치한다. 문제는 composer가
`runtime_commit`, `runtime_fingerprint`, `start_evidence_type`, `source`,
`captured_at_utc`를 요구하는 반면, 실제 start evidence는
`runtime_code_commit`, `runtime_config_fingerprint`, `evidence_kind`와 systemd timestamp
필드를 사용한다는 deterministic schema incompatibility다.

contract 없이 strict epoch manifest builder를 실제 run root에 실행하면
`NO_RUN_CONTRACT: Run contract required for epoch manifest build`로 fail closed한다. 하위 process,
coverage gate도 이미 실패했으므로 `SEALED_COMPLETE` manifest는 만들지 않았다.

이 composer 결함은 **tooling defect**로 별도 기록한다. 그러나 이를 고쳐도 immutable
`result.json`의 process FAIL과 qualifying feed 누락은 바뀌지 않으므로 이번 run을 PASS로 구제할 수
없다. strict auditor와 epoch manifest의 최종 판정은 모두 FAIL이다.

## 11. 수동 오염, IAM, Terraform

공식 S3 prefix의 2,272개 객체는 receipt와 일치하며 중복·foreign·unexpected 객체가 없다.
LastModified 범위는 `2026-09-12T12:11:50Z`부터 `2026-09-13T17:13:41Z`까지로, 자동
archive scheduler의 run 시간 안이다. 종료 뒤 객체 또는 probe/repair 객체는 발견되지 않았다.
이 감사도 S3 write를 수행하지 않았다. 가용 증거 범위에서 **MANUAL CONTAMINATION = NONE**이다.

CloudTrail `LookupEvents`는 현재 least-privilege provisioner role에 허용되지 않아 account-wide
IAM event count를 독립 조회할 수 없었다. 따라서 다음처럼 범위를 구분한다.

- 이 감사가 수행한 IAM mutation: **0**
- 현재 runtime/provisioner 경계의 임의 확대: **0**
- run 기간 account-wide CloudTrail IAM mutation count: **NOT VERIFIABLE (AccessDenied)**

Terraform은 apply/plan/backend 작업을 실행하지 않았다. authoritative local state는 run 전인
`2026-09-09T17:03:19+0900`에 마지막 수정됐고, prelaunch와 동일한 lineage, serial 51, resource
27개를 유지한다. SHA-256은
`e18132996254ba66f493fac9a0348016ed204bdfedf09f98a78fc916a8715a8a`다.
이 감사의 Terraform mutation count는 **0**이다.

## 12. 독립 포렌식 finding

### Critical 1 — finalization hard-ceiling failure

수집기 종료가 자연 완료가 아니고 `-15`이며, forced timeout과 incomplete manifest flush가 공식
결과에 직접 기록됐다. 분류: **runtime + evidence**.

### Critical 2 — qualifying feed exact coverage 누락

qualifying 30 cohort 중 3 cohort, 8 feed partition이 없어 RAW/archive/receipt/restore/fullscan의
root exact coverage가 연쇄 실패했다. 분류: **data-provider/availability + evidence contract**.

### Important 1 — start evidence/composer schema 불일치

공식 actual-start evidence와 strict composer 사이의 deterministic field schema 불일치 때문에
정상 값도 contract로 조립되지 않는다. 분류: **tooling**.

Minor와 Advisory는 없다. IAM CloudTrail 조회 제한은 이미 FAIL인 run의 판정을 바꾸지 않으며,
권한 확대 없이 명시적 NOT VERIFIABLE로 남겼다.

## 13. 최소 remediation과 재검증 조건

이번 branch에는 source fix를 섞지 않았다. 다음 remediation은 별도 branch와 새 authoritative main
기준으로 수행해야 한다.

1. **finalization을 증분·bounded로 변경**
   - archive scheduler가 이미 생성한 closed-cohort manifest를 다시 전체 스캔하지 않는다.
   - dirty/active ending partition만 처리하거나, 기존 manifest와 source size/hash binding을 검증해
     unchanged file은 건너뛴다.
   - 파일별 진행 수와 완료 상태를 원자적으로 기록해 중간 종료 시 `manifest_count=0` 착시를 없앤다.
   - 다시간·대파일과 느린 manifest generation을 재현하는 테스트로 hard ceiling 내 완료를 검증한다.

2. **76 exact feed 계약과 무거래 hour를 일치시킴**
   - 기존 계약을 유지하려면 실제로 매 hour real record가 발생하는 feed universe를 새로 선정하고
     봉인해야 한다.
   - zero-event hour를 과학적으로 인정하려면 upstream 구독·availability·무발행을 입증하는 별도
     immutable evidence schema와 auditor 규칙이 필요하다.
   - 빈/fake record를 만들어 76을 맞추거나 과거 data/receipt를 보완해서는 안 된다.
   - 이는 material research/validation protocol 변경이므로 인간 검토가 필요하다.

3. **composer schema 호환 수정**
   - 실제 actual-start schema의 canonical field를 명시적으로 지원하고 exact identity를 계속
     fail-closed 검증한다.
   - 이번 실제 evidence 파일을 regression fixture로 사용해 commit/fingerprint, evidence kind,
     timezone timestamp의 성공/불일치 양쪽 테스트를 추가한다.

source 변경은 이번 실패 보존 작업의 필수 조건이 아니고, feed contract 선택은 인간 검토가 필요하므로
현재는 문서·원본 small artifact 보존만 했다. remediation 후에는 **새 epoch, 새 run ID, 새 봉인
identity가 필수**다. 같은 30H identity는 절대 재실행하지 않는다. 새 검증 실행은 인간의 별도 승인
전에는 준비하거나 시작하지 않는다.

## 14. 상태 결정

30H 인프라 검증은 닫히지 않았다. 24/7 public research로 넘어갈 선행 조건이 충족되지 않았으므로
research collection은 시작하지 않았다. 과학·거래 안전 상태는 그대로 유지한다.

CONTEXT RECOVERY:
PASS

AUTHORITATIVE MAIN:
6576f632b3f44eb68645bad4304665c0ea87512d

30H EVIDENCE BRANCH:
codex/aws-30h-prep-v2-20260912-6576f63

30H EVIDENCE HEAD BEFORE POST-RUN:
776c67d63b1794aa1f9b9163865e62ae472d1d2f

30H EPOCH:
aws-validation-30h-20260912-6576f63

30H RUN ID:
aws-validation-30h-run-20260912T103507Z-6576f63

ACTUAL START UTC:
2026-09-12T11:24:51Z

ACTUAL END UTC:
2026-09-13T17:29:52.284325+00:00

SYSTEMD EXECUTIONS:
1

SYSTEMD RESTARTS:
0

PROCESS NATURAL COMPLETION:
NO

COLLECTOR EXIT:
-15

RECEIVED SIGNAL:
null

FORCED TIMEOUT:
true

FULL DURATION:
PASS

FINAL METRICS:
PASS

FINAL MANIFEST FLUSH:
FAIL

LIFECYCLE:
FINALIZING

TOUCHED COHORTS:
31

QUALIFYING COHORTS:
30

RAW COVERAGE:
FAIL

COMPRESSED COVERAGE:
FAIL

TERMINAL RECEIPTS:
FAIL

RESTORE:
FAIL

FULLSCAN:
FAIL

STRICT AUDITOR:
FAIL

DATA QUALITY:
FAIL
invalid_json=0, schema_mismatch=0, missing_fields=0, non_finite=0, timestamp_errors=0, unknown_feeds=0, scan_failures=0, quarantine=0, missing_qualifying_feeds=8

EPOCH MANIFEST:
FAIL

MANUAL CONTAMINATION:
NONE

30H PROCESS:
FAIL

30H ARCHIVE:
FAIL

30H DATA QUALITY:
FAIL

30H EVIDENCE CONTRACT:
FAIL

INDEPENDENT REVIEW:
Critical 2
Important 1
Minor 0
Advisory 0

30H OVERALL:
FAIL

INFRA VALIDATION CLOSED:
NO

READY FOR 24/7 RESEARCH:
NO

ALPHA:
UNPROVEN

PAPER:
NOT STARTED

LIVE:
DISABLED

PRIVATE API:
DISABLED
