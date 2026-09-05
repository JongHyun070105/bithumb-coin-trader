# 72시간 무인 수집 완료 후 오프라인 임포트 런북 (Post-72H Offline Import Runbook)

## 1. 개요
본 문서는 현재 독립적으로 실행 중인 AWS 72시간 시장 데이터 무인 수집(Soak)이 완전히 종료된 후, 수집된 데이터를 로컬 연구 환경으로 안전하게 가져와 표준 캐노니컬 데이터셋으로 정제 및 파티셔닝하는 표준 절차(SOP)를 정의한다.

## 2. 작업 전제 조건 (Prerequisites)
- AWS 72시간 수집 기간(72시간 + 그레이스 기간)이 공식적으로 만료되었음을 확인.
- 실거래 트레이딩 및 라이브 수집기에 어떠한 간섭도 하지 않는 읽기 전용(Read-only) 절차 준수.

## 3. 단계별 실행 절차 (Step-by-Step Procedure)


> **FORENSIC HARDENING NOTICE (Phase 2.5 — BUG-5/BUG-6 FIX)**:
> - `archive_hour_001.tar.zst` ~ `archive_hour_072.tar.zst` 는 **실제 존재하지 않는 파일명**입니다.
>   실제 아카이브 파일명은 수집 에포크와 타임스탬프에 따라 결정됩니다. 아래에서 `<EXPORTED_EPOCH_ROOT>`로 표기합니다.
> - 단계 3, 4, 5의 CLI 명령어(`audit-quality`, `transform-canonical`, `partition-dataset`)는
>   Phase 2.5에서 추가되었습니다. Phase 2 기준 코드에서는 이 명령어가 존재하지 않습니다.

### 단계 1: 수집 완료 상태 및 아카이브 무결성 확인 (Verification)
1. S3 버킷 내 72개 시간별 아카이브 파일(`<EXPORTED_EPOCH_ROOT>/archive_hour_NNN.tar.zst`, N=001~072) 존재 여부 확인.
   - 실제 파일명은 수집 에포크 및 시스템 설정에 따라 다릅니다. S3 버킷 내용을 직접 확인하여 실제 파일명을 사용하세요.
2. 각 시간대 매니페스트(`manifest.json`)의 SHA-256 해시 대조.

### 단계 2: 로컬 데이터 다운로드 (Read-Only Download)
```bash
# 로컬 전용 저장 경로 생성
mkdir -p data/raw_soak_72h

# S3에서 로컬로 안전 다운로드 (Read-only)
aws s3 sync s3://bitcoin-trader-archive-bucket/aws-72h-soak-20260905/ data/raw_soak_72h/ --dryrun
aws s3 sync s3://bitcoin-trader-archive-bucket/aws-72h-soak-20260905/ data/raw_soak_72h/
```

### 단계 3: 데이터 품질 사전 적격성 검사 (Data Quality Audit)
```bash
# 데이터 품질 플래그 스캐너 실행
python -m bithumb_coin_trader.research_cli audit-quality \
    --input-dir data/raw_soak_72h \
    --report-out reports/soak_72h_data_quality_report.json
```
- 확인 기준:
  - 타임스탬프 역전율 < 0.001%
  - 최대 수신 지연 갭 > 5,000ms 발생 횟수 < 10회
  - 역전 호가(Crossed book) 발생 0건

### 단계 4: 캐노니컬 마켓 데이터 변환 (Canonical Transformation)
```bash
python -m bithumb_coin_trader.research_cli transform-canonical \
    --input-dir data/raw_soak_72h \
    --output-dir data/canonical_72h \
    --schema-version 2.0.0
```
- 빗썸/바이낸스/업비트 데이터를 `CanonicalOrderBook`, `CanonicalTrade`, `CanonicalTicker` 포맷으로 변환하고 Zstandard(레벨 3) 압축 적용.

### 단계 5: 시계열 엠바고 파티셔닝 (Temporal Partitioning)
```bash
python -m bithumb_coin_trader.research_cli partition-dataset \
    --input-file data/canonical_72h/bithumb_krw_btc_orderbooks.ndjson.zst \
    --output-dir data/datasets/krw_btc_72h_v1 \
    --train-frac 0.60 \
    --val-frac 0.20 \
    --purge-window-ms 900000
```
- Train (60%) $	o$ 15분 Purge $	o$ Validation (20%) $	o$ 15분 Purge $	o$ Holdout (20%) 분할 생성.
- `manifest.json` 내 SHA-256 체크섬 영구 기록.

### 단계 6: 연구 거버넌스 원장에 데이터셋 등록
- 생성된 데이터셋을 `evidence/research/governed_experiment_ledger.json`에 `ROLE = QUALIFICATION_DATASET`으로 공식 등록.
- 홀드아웃 파티션은 최종 검증 이전까지 접근 차단(`HoldoutContaminationError` 보장).

## 4. 비상 조치 및 롤백
- 데이터 품질 검사에서 역전 호가 또는 10분 이상의 연속 결측 발생 시, 해당 구간을 즉시 결함 구간으로 마킹하고 적격성 평가 보고서에 기록함.
