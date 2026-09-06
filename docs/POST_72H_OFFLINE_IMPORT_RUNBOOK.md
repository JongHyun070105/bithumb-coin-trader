# 72시간 무인 수집 완료 후 오프라인 임포트 런북 (Post-72H Offline Import Runbook)

## 1. 개요 및 절대 원칙 (Scope & Core Principles)

본 문서는 현재 AWS 환경에서 독립적으로 실행 중인 72시간 무인 시장 데이터 수집(Soak)이 완료된 후, 수집된 원시 증거(Raw Microstructure Evidence)를 로컬 연구 환경으로 안전하게 반입하고 캐노니컬 데이터셋으로 변환·파티셔닝하기 위한 표준 절차(SOP)를 정의한다.

### 절대 원칙 (Fail-Closed Guarantees)
- **라이브 환경 간섭 금지**: AWS CLI, EC2, S3, SSM, CloudWatch, Terraform 등 라이브 인프라와 일체 상호작용하지 않는다. 수집이 자연 종료되고 정상 아카이브가 완료된 스냅샷 파일만 오프라인으로 수령하여 처리한다.
- **Fail-Closed 검증**: 빈 에포크, 누락된 매니페스트, 변조된 해시, 복원 실패, 역전 클록 등 비정상 조건 감지 시 즉시 처리를 중단하고 적격성(`DQ_FAIL`)을 거부한다.
- **구조적 감사(Structural Audit)의 DQ 대체 금지**: 단순 필드 유무를 검사하는 구조적 감사는 심층 데이터 품질(DQ) 감사를 대체할 수 없으며, 단독으로 `DQ_PASS`를 부여할 수 없다.
- **홀드아웃 격리 보장**: 데이터셋 생성 후 사전 등록 정책(`ResearchCyclePolicy`) 승인 전까지 홀드아웃 파티션은 절대 열람·탐색하지 않는다 (`HoldoutContaminationError` 강제).

---

## 2. 12단계 오프라인 임포트 표준 절차 (Step-by-Step Sequence)

### 1단계: 완료된 수집 에포크 스냅샷 확보 (Obtain Exported Snapshot)
- 72시간 수집 및 시간별 롤링 아카이브가 모두 완료된 로컬 디렉터리 경로를 지정한다.
```bash
# 로컬 반입 디렉터리 구조 예시
export EPOCH_DIR="data/exported_soak_72h"
# 구조:
# $EPOCH_DIR/raw/YYYY-MM-DD/{exchange}/{stream}/...jsonl
# $EPOCH_DIR/manifests/manifest_*.json
# $EPOCH_DIR/archive-receipts/*.archive-receipt.json
# $EPOCH_DIR/archive-receipts/full_scan_*_report.json
```

### 2단계: 소스 스냅샷 해시 및 출처 전수 검증 (Verify Source Provenance)
- 각 파티션 매니페스트 파일의 내용과 실제 원시 파일의 바이트 수, 레코드 수, SHA-256 해시를 대조한다.

### 3단계: 권위적 심층 72H DQ 감사기 실행 (Run Authoritative Deep DQ Auditor)
- `RawMicrostructureStorage` 실제 규격(`exchange_ts`, `local_recv_ts`, `local_recv_monotonic_ns`, `collector_run_id`, `payload`) 및 76개 고정 피드 유니버스를 전수 검사한다.
```bash
python scripts/audit_72h_soak.py \
    --epoch-dir "$EPOCH_DIR" \
    --out-json reports/deep_dq_audit_72h.json \
    --out-md reports/deep_dq_audit_72h.md
```

### 4단계: 동결된 72H 합격 판정 검증 (Ensure Frozen Acceptance Verdict)
- 생성된 감사 보고서의 상태가 `DQ_PASS_ELIGIBLE`인지 확인한다.
- 블로커(`blockers`)가 1건이라도 존재하거나, 필수 피드 결측, 타임스탬프 역전, 복원 실패가 있는 경우 즉시 중단한다.
```bash
# 상태 확인
jq .status reports/deep_dq_audit_72h.json
# 기댓값: "DQ_PASS_ELIGIBLE"
```

### 5단계: 에포크 증거 루트 매니페스트 구축 (Build Epoch Root Manifest)
- 76개 피드 x 전체 시간대 파티션, 아카이브 영수증, 풀스캔 리포트, 런타임 씰을 총괄 바인딩하는 단일 루트 매니페스트를 생성한다.
```bash
python scripts/build_epoch_manifest.py \
    --epoch-dir "$EPOCH_DIR" \
    --output "$EPOCH_DIR/manifests/epoch_manifest.json" \
    --strict
```

### 6단계: 암호학적 DQ 적격성 증명서 발급 (Build DQ Qualification Artifact)
- 심층 감사 보고서 바이트 해시(`audit_report_sha256`)와 에포크 증거 루트 매니페스트 해시를 암호학적으로 결속(Cryptographically Bound)한다.
```bash
python -m bithumb_coin_trader.research_cli dq-qualify \
    --audit-report reports/deep_dq_audit_72h.json \
    --source-manifest "$EPOCH_DIR/manifests/epoch_manifest.json" \
    --out evidence/research/dq_qualification_72h.json \
    --strict
```

### 7단계: 스트림 인식 캐노니컬 변환 (Canonicalize Stream-Aware Data)
- 대용량 데이터셋 메모리 초과 방지를 위해 행 단위 스트리밍(O(1) RAM) 방식으로 변환한다.
- 호가창(`orderbook`) 및 체결(`trade`) 스트림을 각각 독립적으로 변환하며, 전체 파티션 메타데이터를 아우르는 `canonical_manifest.json`을 방출한다.
```bash
mkdir -p data/canonical_72h

# 호가창 스트림 변환
python -m bithumb_coin_trader.research_cli transform-canonical \
    --input-dir "$EPOCH_DIR/raw" \
    --output-dir data/canonical_72h \
    --exchange bithumb \
    --stream orderbook \
    --schema-version 2.1.0

# 체결 스트림 변환
python -m bithumb_coin_trader.research_cli transform-canonical \
    --input-dir "$EPOCH_DIR/raw" \
    --output-dir data/canonical_72h \
    --exchange bithumb \
    --stream trade \
    --schema-version 2.1.0
```

### 8단계: 레코드 보존 법칙 및 캐노니컬 매니페스트 검증 (Verify Count Conservation & Canonical Manifest)
- 각 (거래소, 마켓, 스트림) 단위로 소스 원시 유효 레코드 수와 변환 결과 레코드 수가 완전히 보존되는지 대조한다:
  $$\text{source\_nonblank} = \text{parse\_failures} + \text{skipped\_exchange} + \text{skipped\_stream} + \text{skipped\_market} + \text{eligible\_records}$$
  $$\text{eligible\_records} = \text{canonicalized} + \text{rejected}$$
- 생성된 `data/canonical_72h/canonical_manifest.json`의 전체 파티션 파일 해시 및 메타데이터를 검증한다.

### 9단계: 트랜잭션 스테이징 기반 전 시간대 데이터셋 분할 (Partition Full Multi-Hour Series)
- 단일 파일이 아닌 `canonical_manifest.json`을 통해 72시간 전체에 걸친 대상 마켓 시계열 전체를 2-Pass Bounded-Memory 스트리밍으로 병합·분할한다.
- 실제 심층 감사 리포트 해시(`--deep-audit-report`)와 적격성 증명서의 무결성을 상호 교차 검증한다.
- 원자적 디렉터리 스테이징(`<output_dir>.building.<uuid>/`)을 거쳐 안전하게 데이터셋을 생성한다.
- 트레인(60%), 엠바고 퍼지(15분), 밸리데이션(20%), 엠바고 퍼지(15분), 홀드아웃(20%) 분할을 적용한다.
```bash
python -m bithumb_coin_trader.research_cli partition-dataset \
    --canonical-manifest data/canonical_72h/canonical_manifest.json \
    --exchange bithumb \
    --market KRW-BTC \
    --stream orderbook \
    --output-dir data/datasets/krw_btc_72h_v1 \
    --dq-report evidence/research/dq_qualification_72h.json \
    --source-manifest "$EPOCH_DIR/manifests/epoch_manifest.json" \
    --deep-audit-report reports/deep_dq_audit_72h.json \
    --train-frac 0.60 \
    --val-frac 0.20 \
    --purge-window-ms 900000 \
    --clock receive_wall_clock \
    --source-epoch-id "epoch_72h_soak_official" \
    --source-run-id "run_72h_aws_production"
```

### 10단계: 데이터셋 식별자 봉인 (Seal Dataset Identity)
- 생성된 `manifest.json` 내 64자리 SHA-256 `dataset_id` 및 출처 커밋(`deep_dq_auditor_commit`, `canonicalizer_commit`, `dataset_builder_commit`)이 기록되었는지 확인한다.
- 봉인된 디렉터리는 덮어쓰기가 원천 금지된다 (`FileExistsError`).

### 11단계: 홀드아웃 격리 유지 (Do NOT Open Holdout)
- `holdout.ndjson.zst` 파티션은 사전 등록 정책 승인 및 탐색 가설 검증이 완료될 때까지 접근이 차단된다.

### 12단계: 사전 등록 연구 거버넌스 승인 후 연구 개시 (Preregistered Discovery)
- `ResearchCyclePolicy`에 사이클 총 예산 및 특성 패밀리별 최대 트라이얼 수가 등록된 상태에서만 `reserve_trial()`을 통해 트레이딩 연구를 개시한다.

---

## 3. 비정상 대응 가이드 (Troubleshooting & Emergency Matrix)

| 증상 | 원인 | 조치 절차 |
| :--- | :--- | :--- |
| `NO_RAW_EVIDENCE` / `NO_MANIFEST_EVIDENCE` | 빈 에포크 또는 잘못된 경로 | 스냅샷 경로 확인, 빈 데이터셋 적격성 판정 즉시 거부 (`FAIL`) |
| `STRUCTURAL_ONLY_NOT_QUALIFIABLE` | 구조적 감사 보고서를 qualify에 입력 | 3단계 `audit_72h_soak.py` 심층 감사 재수행 후 입력 |
| `HASH_MISMATCH` / `RECORD_COUNT_MISMATCH` | 원시 파티션 파일 손상 또는 변조 | 아카이브 파일 무결성 재확인, 손상 파티션 격리 |
| `TEMPORAL_KEY_MISSING` | 타임스탬프 키 누락 | 파티셔닝 기준 시계(`--clock`) 점검 및 원시 인벨로프 수신시각 확인 |
| `CYCLE_BUDGET_EXCEEDED` / `DISALLOWED_FAMILY` | 거버넌스 사이클 예산 초과 또는 비인가 패밀리 | 사이클 정책 승인 검토 또는 새로운 연구 사이클 등록 |
