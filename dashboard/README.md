# 퀀트 운영 대시보드 v0.2 (Offline Evidence & Research Console)

72시간 무인 수집(Unattended Soak) 완료 후 포스트소크 감사, 데이터 품질(DQ) 평가, 사전등록 연구 가설 거버넌스 및 증거 사슬(Evidence Chain) 검증을 위한 **완전 격리 오프라인 증거 콘솔**입니다.

## 핵심 아키텍처 및 안전 경계 (Safety Boundaries)

- **Air-Gap 완전 오프라인 격리**: 외부 네트워크 호출(`fetch`, `axios`, `WebSocket`, `EventSource`, CDN 등) 0건. 브라우저 로컬 메모리에서만 동작합니다.
- **클라이언트 사이드 로컬 처리**: 증거 JSON 파일(런타임 씰, 출처, 계약서, 매니페스트, 심층 DQ 감사 등)을 브라우저에 로컬 드래그 앤 드롭하여 Web Crypto API(SHA-256)로 무결성을 검증하며, 서버나 외부로 업로드되지 않습니다.
- **3대 명시적 모드 분리**:
  - `NO EVIDENCE` (기본값): 로컬 증거가 없는 초기 상태. 미확인 수치는 절대 0으로 날조되지 않고 `PENDING EVIDENCE`, `NOT RUN`, `UNKNOWN`으로 표기됩니다.
  - `SYNTHETIC DEMO`: 파이프라인 시연용 합성 픽스처. 워터마크 및 `SYNTHETIC DATA` 배지가 상시 노출됩니다.
  - `IMPORTED EVIDENCE`: 실제 수집 증거 아티팩트가 로컬 메모리에 로드된 분석 모드.
- **에피스테믹 안전 불변식 (Epistemic Rules)**:
  - 수집 완료 판정과 데이터 품질 판정을 엄격히 분리 (72H Soak Execution vs Data Quality Qualification).
  - 결측치/미측정치를 임의로 0이나 0.0%로 표시하지 않고 `NOT AVAILABLE`, `—` 등으로 명시.
  - 연구 데이터셋 홀드아웃(Holdout 22h) 구간은 `SEALED`로 잠금 표시되며, 열람/해제 UI 컨트롤이 일체 존재하지 않습니다.
  - 트레이딩(실거래/페이퍼) 화면은 완전 잠금 상태이며 주문 발주(BUY/SELL) 기능 및 스위치가 없습니다.
- **7단계 권위적 증거 사슬 (Authoritative Evidence Chain)**:
  - 런타임 씰 & 런칭 출처 & 실제 시작 증거 → 에포크 계약 → 에포크 매니페스트 루트 → 심층 DQ 보고서 → DQ 자격 승인서 → 캐노니컬 루트 → 연구 데이터셋 매니페스트 간 상하위 암호학적 해시 일관성을 시각화합니다.

## 화면 구성 (Navigation)

1. **개요 (Overview)**: 글로벌 8대 상태 매트릭스, 11단계 프로젝트 라이프사이클 스트립, 증거 사슬 빠른 진단, 증거 드롭존.
2. **72H 무인 수집 (72H Soak)**: 72H 수집 완료 판정 vs DQ 판정 분리, 76개 피드 유니버스, 코호트/영수증/풀스캔/에러 지표.
3. **증거 사슬 (Evidence Chain)**: 7단계 시각적 해시 체인 플로우, 삼자 불변식(Three-Way Invariants) 검증, 아티팩트 관리 그리드.
4. **데이터 품질 (Data Quality)**: 최종 판정, 하드 블로커, 품질저하, 전수 스트리밍 무결성, 감사 스코프 배지(FULL/SAMPLED/MANIFEST-DERIVED).
5. **연구 데이터셋 (Dataset)**: 72시간 시계열 표본 분할(Train 24h / Embargo 2h / Validation 24h / Embargo 2h / Holdout 22h SEALED) 및 10종 출처 메타데이터.
6. **연구실 (Research Lab)**: 사전등록 마이크로스트럭처(OFI/ATI/MPQI) 상태, 통계적 거버넌스(DSR/WRC/PBO), 역사적 베이스라인(V4/V6), 거절된 실험(V8/V8.1) 원장.
7. **안전 센터 (Safety Center)**: 7대 불변 안전 게이트 (읽기 전용, 언락 제어 없음).
8. **이벤트 타임라인 (Events)**: 로컬 증거 기반 파생 이벤트 타임라인 (`UI DERIVED` 명시).
9. **인프라 토폴로지 (Infrastructure)**: 정적 5단계 아키텍처 및 동결 사양 (`SEALED CONFIGURATION`).
10. **트레이딩 통제 (Trading)**: 실행 계층 완전 잠금 및 5대 선행 필수 조건.

## 실행 및 검증 명령어

```sh
# 의존성 설치
npm install

# 개발 서버 실행 (Vite 로컬 호스트)
npm run dev

# 타입스크립트 정적 분석
npm run typecheck

# 린터 검증 (oxlint)
npm run lint

# 단위/통합 테스트 (Vitest 12개 검증 시나리오)
npm test

# 프로덕션 번들 빌드
npm run build
```

## Phase 6.3 verification contract

The authoritative [Phase 6.3 report](../docs/PHASE6_3_EVIDENCE_CONTRACT_REPORT.md)
supersedes the v0.2 handoff's parser/hash assumptions. Nine Python-produced core
metadata fixtures are tested byte-for-byte. Archive receipts and fullscan reports
are recognized only as ancillary metadata. Filenames never confer trust.

The strongest verdict is **STRUCTURALLY COMPLETE**, not full cryptographic evidence
verification: RAW, canonical partitions, dataset content, holdout and external producer
authenticity are not checked by this metadata viewer. Official real-DQ/soak/alpha/trading
status does not advance on import. Duplicate authoritative candidates are ambiguous.

Imports accept bounded plain UTF-8 JSON only (10 MiB, nesting <=64, no BOM or duplicate
keys). Noncanonical float spellings outside the documented Python subset fail closed.
Synthetic demo loads the Python-generated metadata and is cleared by the first import.
