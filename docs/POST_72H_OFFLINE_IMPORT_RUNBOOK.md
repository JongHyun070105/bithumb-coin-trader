# 72시간 무인 수집 완료 후 오프라인 임포트 런북 (Post-72H Offline Import Runbook)

## 1. 개요 및 절대 원칙 (Scope & Core Principles)

본 문서는 현재 AWS 환경에서 독립적으로 실행 중인 72시간 무인 시장 데이터 수집(Soak)이 완료된 후, 수집된 원시 증거(Raw Microstructure Evidence)를 로컬 연구 환경으로 안전하게 반입하고 캐노니컬 데이터셋으로 변환·파티셔닝하기 위한 표준 절차(SOP)를 정의한다.

### 절대 원칙 (Fail-Closed Guarantees)
- **라이브 환경 간섭 금지**: AWS CLI, EC2, S3, SSM, CloudWatch, Terraform 등 라이브 인프라와 일체 상호작용하지 않는다. 수집이 자연 종료되고 정상 아카이브가 완료된 스냅샷 파일만 오프라인으로 수령하여 처리한다.
- **Fail-Closed 검증**: 빈 에포크, 누락된 매니페스트, 변조된 해시, 복원 실패, 역전 클록 등 비정상 조건 감지 시 즉시 처리를 중단하고 적격성(`DQ_FAIL`)을 거부한다.
- **구조적 감사(Structural Audit)의 DQ 대체 금지**: 단순 필드 유무를 검사하는 구조적 감사는 심층 데이터 품질(DQ) 감사를 대체할 수 없으며, 단독으로 `DQ_PASS`를 부여할 수 없다.
- **홀드아웃 격리 보장**: 데이터셋 생성 후 사전 등록 정책(`ResearchCyclePolicy`) 승인 전까지 홀드아웃 파티션은 절대 열람·탐색하지 않는다 (`HoldoutContaminationError` 강제).

---

## 2. 6단계 오프라인 임포트 공식 절차 (Authoritative Step-by-Step Sequence)

### 1단계: 에포크 실행 계약 합성 및 실제 시작 증거 검증 (Compose & Verify Epoch Run Contract)
- 런타임 씰(`runtime_seal.json`), 런칭 출처(`launch-provenance.json`), 그리고 **실제 시작 증거**(`actual-start-evidence`)를 검증하여 계약서를 합성한다.
- `launch_provenance`의 `created_at_utc`를 실제 시작 시각으로 간주하지 않으며, 실제 시작 증거가 없을 경우 `ACTUAL_START_EVIDENCE_MISSING` (exit 2)으로 즉각 중단(Fail-Closed)한다.
```bash
python scripts/compose_epoch_contract.py \
    --epoch-dir "$EPOCH_DIR" \
    --runtime-seal "$EPOCH_DIR/contracts/runtime_seal.json" \
    --launch-provenance "$EPOCH_DIR/contracts/launch-provenance.json" \
    --actual-start-evidence "$EPOCH_DIR/contracts/actual_start.evidence.json" \
    --out "$EPOCH_DIR/contracts/epoch_contract.json"
```

### 2단계: 에포크 증거 루트 매니페스트 구축 및 봉인 (Build Sealed Epoch Root Manifest)
- 76개 피드 x 전체 시간대 파티션, 아카이브 영수증, 풀스캔 리포트, 실행 계약서를 총괄 바인딩하는 단일 루트 매니페스트를 생성하고 셀프 해시를 봉인한다.
- 공식 모드에서 `runtime_seal_sha`, `launch_prov_sha`, `contract_data`, `actual_start_evidence`가 누락되거나 손상된 경우 즉시 거부한다.
```bash
python scripts/build_epoch_manifest.py \
    --epoch-dir "$EPOCH_DIR" \
    --contract "$EPOCH_DIR/contracts/epoch_contract.json" \
    --output "$EPOCH_DIR/manifests/epoch_manifest.json" \
    --strict
```

### 3단계: 루트 연동 권위적 심층 72H DQ 감사기 실행 (Run Authoritative Deep DQ Auditor Against Root)
- 에포크 루트 매니페스트(`--epoch-manifest`)의 셀프 해시를 대조 검증한 후, `RawMicrostructureStorage` 실제 규격 및 76개 고정 피드 유니버스를 전수 검사한다.
- 타임스탬프 파싱 실패 시 예외 묵살 없이 `CORRUPT_RAW_RECORD` / `MONOTONIC_CLOCK_REVERSAL` 블로커를 즉시 방출한다.
```bash
python scripts/audit_72h_soak.py \
    --epoch-dir "$EPOCH_DIR" \
    --epoch-manifest "$EPOCH_DIR/manifests/epoch_manifest.json" \
    --contract "$EPOCH_DIR/contracts/epoch_contract.json" \
    --out-json reports/deep_dq_audit_72h.json \
    --out-md reports/deep_dq_audit_72h.md
```

### 4단계: 암호학적 DQ 적격성 증명서 발급 (Build Cryptographic DQ Qualification Evidence)
- 문자열 전용 해시를 불허하며, 실제 검증된 `--epoch-manifest` 파일과 심층 감사 보고서를 바인딩한다.
- `degraded_count > 0`인 경우 `DQ_DEGRADED`로 엄격 분류되며, `DQ_PASS`가 부여되지 않는다.
```bash
python -m bithumb_coin_trader.research_cli dq-qualify \
    --audit-report reports/deep_dq_audit_72h.json \
    --epoch-manifest "$EPOCH_DIR/manifests/epoch_manifest.json" \
    --out evidence/research/dq_qualification_72h.json \
    --strict
```

### 5단계: 스트림 인식 캐노니컬 변환 및 미봉인 주입 방어 (Canonicalize Streams & Root Binding)
- 파일시스템 임의 파일이 아닌 `epoch_manifest["partitions"]`에 봉인된 파티션만을 엄격 검증하여 변환한다. 미봉인 파일 발견 시 `UNSEALED_SOURCE_PARTITION` (exit 2)으로 즉시 거부한다.
- 변환 결과 `canonical_manifest.json`에 `source_epoch_manifest_sha256` 및 `dq_qualification_sha256`을 결속한다.
```bash
mkdir -p data/canonical_72h

# 호가창 스트림 변환
python -m bithumb_coin_trader.research_cli transform-canonical \
    --input-dir "$EPOCH_DIR/raw" \
    --output-dir data/canonical_72h \
    --exchange bithumb \
    --stream orderbook \
    --schema-version 2.1.0 \
    --epoch-manifest "$EPOCH_DIR/manifests/epoch_manifest.json" \
    --dq-qualification evidence/research/dq_qualification_72h.json

# 체결 스트림 변환
python -m bithumb_coin_trader.research_cli transform-canonical \
    --input-dir "$EPOCH_DIR/raw" \
    --output-dir data/canonical_72h \
    --exchange bithumb \
    --stream trade \
    --schema-version 2.1.0 \
    --epoch-manifest "$EPOCH_DIR/manifests/epoch_manifest.json" \
    --dq-qualification evidence/research/dq_qualification_72h.json
```

### 6단계: 증거 사슬 전수 검증 및 데이터셋 분할 (Verify Evidence Chain & Partition Dataset)
- `canonical.source_epoch_manifest_sha256 == DQ.epoch_manifest_sha256 == actual_epoch_sha` 증거 사슬 삼자 일치를 검증한다 (`EVIDENCE_CHAIN_MISMATCH` 방지).
- 레거시 우회 정책(`strict_phase4`, `1.0.0`)을 전면 거부(`LEGACY_QUALIFICATION_REJECTED`)하며, `--deep-audit-report`를 필수로 검증한다.
- 에포크 루트로부터 소스 에포크/런/커밋/핑거프린트 10종 메타데이터를 자동 도출하여 기록한다.
```bash
python -m bithumb_coin_trader.research_cli partition-dataset \
    --canonical-manifest data/canonical_72h/canonical_manifest.json \
    --exchange bithumb \
    --market KRW-BTC \
    --stream orderbook \
    --output-dir data/datasets/krw_btc_72h_v1 \
    --dq-report evidence/research/dq_qualification_72h.json \
    --epoch-manifest "$EPOCH_DIR/manifests/epoch_manifest.json" \
    --deep-audit-report reports/deep_dq_audit_72h.json \
    --train-frac 0.60 \
    --val-frac 0.20 \
    --purge-window-ms 900000 \
    --clock receive_wall_clock
```

## 3. 비정상 대응 가이드 (Troubleshooting & Emergency Matrix)

| 증상 | 원인 | 조치 절차 |
| :--- | :--- | :--- |
| `NO_RAW_EVIDENCE` / `NO_MANIFEST_EVIDENCE` | 빈 에포크 또는 잘못된 경로 | 스냅샷 경로 확인, 빈 데이터셋 적격성 판정 즉시 거부 (`FAIL`) |
| `STRUCTURAL_ONLY_NOT_QUALIFIABLE` | 구조적 감사 보고서를 qualify에 입력 | 3단계 `audit_72h_soak.py` 심층 감사 재수행 후 입력 |
| `HASH_MISMATCH` / `RECORD_COUNT_MISMATCH` | 원시 파티션 파일 손상 또는 변조 | 아카이브 파일 무결성 재확인, 손상 파티션 격리 |
| `TEMPORAL_KEY_MISSING` | 타임스탬프 키 누락 | 파티셔닝 기준 시계(`--clock`) 점검 및 원시 인벨로프 수신시각 확인 |
| `CYCLE_BUDGET_EXCEEDED` / `DISALLOWED_FAMILY` | 거버넌스 사이클 예산 초과 또는 비인가 패밀리 | 사이클 정책 승인 검토 또는 새로운 연구 사이클 등록 |
