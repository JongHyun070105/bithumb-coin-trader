# AWS isolated 72-hour collector soak plan

## 목적과 판정 경계

AWS epoch에서 V9.1 collector의 infrastructure durability와 data integrity를 검증한다. Mac V9/V9.1 epoch와 raw, manifests, metrics, run ID를 합치지 않는다.

초기 data phase는 약 3–5일이고, uninterrupted minimum clean soak target은 72시간이다. 중간 data를 alpha mining에 사용하지 않는다. AWS는 장기 raw archive 서버가 아니라 이후 24/7 collector/trading runtime이 될 운영 기반이며, 이번 soak는 그 infrastructure만 검증한다.

- 72h process continuity PASS는 data quality PASS가 아니다.
- AWS soak PASS는 alpha-ready 또는 live-ready를 의미하지 않는다.
- Dashboard는 mock/read-only를 유지한다.
- Alpha research는 BLOCKED, live trading은 DISABLED다.

## phase 0 — immutable launch seal

launch 전에 다음을 JSON ledger와 resource tag에 고정한다.

- exact Git commit과 clean/origin SHA 확인
- canonical non-secret config SHA-256
- `collector_epoch`, `run_id`, `environment_id`
- AWS account identity는 report에 masked reference로 기록
- region, AZ, instance type, architecture, AMI ID
- raw/manifest schema version
- clock source와 launch 전 `chronyc sources -v`/`chronyc tracking`
- process start wall time와 monotonic origin
- zstd candidate/version와 benchmark result

Mac epoch 파일이나 과거 `6085218` reference를 AWS launch provenance로 사용하지 않는다.

## phase 1 — pre-launch gates

| Gate | PASS 조건 | 실패 시 |
|---|---|---|
| AWS identity/cost | caller identity, credits, price, budget review | launch 금지 |
| Terraform | fmt/init/validate/plan reviewed | apply 금지 |
| Network | SG ingress 0, SSH 22 없음, SSM 접속 | launch 금지 |
| Architecture | Python/tests/websocket/zstd 120분 smoke | x86 fallback 또는 중단 |
| Clock | Amazon Time Sync source 정상, offset/leap 정상 | collector 시작 금지 |
| Disk | 100 GiB gp3 hot-buffer model, 70/80/90% alarm과 projection | 크기/압축 pipeline 재검토 |
| Provenance | 모든 seal 값 immutable ledger에 존재 | collector 시작 금지 |

## phase 2 — compression/archive rehearsal

72h 시작 전 별도 short-run finalized copy로 수행한다.

1. zstd level 1/3/6의 CPU, wall time, RSS, ratio 측정.
2. candidate level 1 압축 후 decompression.
3. 원본/복원 byte SHA-256과 record count 동일성 확인.
4. manifest가 compressed object SHA와 uncompressed raw SHA를 구분하는지 확인.
5. S3 multipart upload 뒤 object size/checksum 확인.
6. 다른 local path로 restore하고 decompress/SHA/record count 재검증.
7. interrupted multipart upload 정리와 재시도 확인.
8. verification이 끝난 finalized uncompressed copy만 cleanup candidate로 표시.

EBS pipeline은 `active raw → finalize → manifest/SHA → zstd → decompression verification → archive/retention decision → verified cleanup` 순서를 강제한다. raw가 단순히 오래됐거나 disk가 부족하다는 이유만으로 삭제하지 않는다.

Mac의 3.49%–4.20% 표본 결과는 비교값일 뿐 AWS gate의 PASS 값이 아니다.

## phase 3 — continuous 72h run

정확한 기준은 sealed process identity의 uninterrupted process start다. raw 최초 timestamp나 재시작된 PID의 합산 uptime을 사용하지 않는다.

### continuous monitoring gates

| 영역 | 필수 관측 |
|---|---|
| process continuity | PID/start identity, single process, uptime, restart/run boundary |
| Binance symbol identity | orderbook market `UNKNOWN` 0, stream symbol preserved |
| wall receive timestamp | 존재/parse/reversal/offset-outlier count |
| monotonic receive timestamp | 존재/finite/stream reversal 0 |
| durable metrics | atomic snapshot freshness, schema/run ID 일치 |
| reconnect recovery | disconnect/reconnect/subscription recovery와 post-reconnect append |
| queue/backpressure | depth, high-water, enqueue blocked, drops |
| writer | error count, unpersisted count, fatal fail-closed propagation |
| disk pressure | free bytes, ingestion projection, write latency, inode/space failure gate |
| compression | finalized partition compression/decompression SHA와 record count |
| S3 archive | upload count/bytes/failure/retry, restored byte integrity |
| manifest | finalized raw coverage, path/size/schema/run ID/object SHA |
| raw SHA | every finalized partition SHA-256 |

disk-used policy:

- 70% warning: 원인과 time-to-critical 측정, compression/upload lag 조사.
- 80% high: optional 작업 중단, verified finalize/compress/archive 경로 우선.
- 90% critical: new nonessential operation 차단, 안전한 graceful stop/fail-closed 준비. unverified raw 삭제 금지.

100 GiB는 5일 uncompressed raw를 보관하는 설계가 아니다. compression/verification이 정상적으로 순환하지 않으면 soak를 PASS로 계속 진행하지 않고 disk gate에서 중단 또는 EBS 확장 review로 전환한다.

Queue drop, writer error, unpersisted event, missing monotonic timestamp, Binance `UNKNOWN`, manifest/raw SHA mismatch은 모두 허용치 0이다. reconnect count 자체는 실패가 아니며 subscription/data recovery가 증명되지 않으면 실패다.

운영 status sampling은 bounded 진단일 뿐 FULL-SCAN을 대체하지 않는다. CloudWatch metric 누락은 `NOT VERIFIABLE`이 아니라 alarm breaching으로 취급한다.

## phase 4 — 72h boundary와 graceful shutdown

1. sealed process start 기준 elapsed monotonic duration이 `>=72h`인지 재검증.
2. PID identity와 append freshness가 같은 run인지 확인.
3. 72h 미충족이면 종료하지 않는다.
4. 충족 후 collector의 정상 shutdown 경로만 사용한다.
5. producers 중단, queue drain, writer flush/close 순서를 확인한다.
6. shutdown 뒤 새 append가 없고 final metrics가 종료 상태인지 확인한다.
7. 마지막 active partition을 finalize하고 manifest/SHA를 만든다.
8. soak evidence와 canonical 후보는 S3에 업로드하고 restore rehearsal을 완료한다. 모든 temporary raw의 영구 보존을 요구하지 않는다.

SIGKILL, host terminate, Terraform destroy로 soak를 끝내지 않는다.

## phase 5 — final freeze와 FULL-SCAN

전체 raw/manifest/S3 index를 대상으로 다음을 수행한다.

- partition/manifest coverage 100%
- 모든 raw SHA와 path/size/schema/run ID
- Binance orderbook `UNKNOWN` count
- wall/monotonic missing, invalid, reversal
- duplicate trade ID는 stream semantics와 함께 계수
- offset outlier 분포와 clock evidence
- durable reconnect/drop/backpressure/writer/unpersisted totals
- malformed record/quarantine
- compressed/decompressed SHA 및 record count
- S3 uploaded/restored SHA
- canonical/temporary classification과 retention-decision ledger
- deterministic replay: 동일 sealed code/config로 두 번 replay한 normalized/features hash 비교
- process continuity와 graceful shutdown ledger

각 결과는 `MEASURED FULL-SCAN`, `MEASURED SAMPLE`, `ESTIMATED`, `NOT VERIFIABLE`, `NOT DIRECTLY VERIFIABLE`을 구분한다. 샘플을 full-scan으로 표현하지 않는다.

## 종료 runbook — retained root EBS

초기 soak에서는 root EBS의 `delete_on_termination=false`를 수용한다. volume 보존은 final evidence가 instance termination과 함께 사라지는 것을 막지만 orphan storage 비용을 만들 수 있으므로 다음 순서를 강제한다.

1. graceful shutdown과 final partition flush를 완료한다.
2. final freeze와 FULL-SCAN 결과를 봉인한다.
3. canonical soak evidence를 S3에 업로드하고 다른 경로로 restore한다.
4. local/S3/restored artifact의 manifest, byte size, SHA-256을 비교해 모두 PASS인지 확인한다.
5. instance 종료 또는 교체 뒤 unattached/orphan EBS volume을 ID, tag, epoch, size, 생성시각 기준으로 명시적으로 inventory한다.
6. 필요한 recovery/reproduction copy이면 보존 사유와 비용 owner를 ledger에 남긴다.
7. 필요 없는 것으로 승인된 volume만 별도 destructive-action 승인 후 명시적 ID로 삭제한다.

S3 verification 이전 자동 삭제, tag/glob만 사용한 volume 삭제, orphan 여부를 확인하지 않은 일괄 삭제는 금지한다.

## 최종 판정

독립적으로 기록한다.

- `AWS 72H PROCESS SOAK`: PASS/FAIL
- `AWS DATA QUALITY`: PASS/FAIL
- `ARCHIVE/RESTORE INTEGRITY`: PASS/FAIL
- `DETERMINISTIC REPLAY`: PASS/FAIL/NOT VERIFIABLE
- `ALPHA RESEARCH READY`: 항상 FALSE — 별도 승인 범위
- `LIVE TRADING READY`: 항상 FALSE — 별도 승인 범위

실패 epoch도 삭제하거나 재분류하지 않고 immutable failure evidence로 보존한다.

## soak 이후 경계

infrastructure soak가 PASS하면 필요한 기간 clean prospective data를 추가 수집한다. 실제 연구에 사용된 dataset, sealed holdout, final audit와 reproduction artifact만 canonical로 보존한다. 재현에 불필요한 temporary raw는 검증된 retention policy의 대상이 될 수 있다.

그 후 연구 순서는 feature generation → development → robustness → validation → sealed holdout → paper다. `AWS infrastructure PASS ≠ Alpha PASS ≠ Paper PASS ≠ Live approval`이며 Terraform 단계에서는 alpha code, paper engine, live control을 변경하지 않는다.
