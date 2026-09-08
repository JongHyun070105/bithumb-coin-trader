# 퀀트 트레이딩 콘솔 & 오프라인 연구 대시보드 v0.3

한국어 우선(Korean-First)의 미니멀 트레이딩 콘솔과 포스트소크 감사/증거 사슬(Evidence Chain) 검증을 위한 오프라인 증거 콘솔이 통합된 로컬 대시보드입니다.

## Dashboard v0.3 핵심 기능

- **한국어 중심 트레이딩 인터페이스**: 대시보드, 포트폴리오, 보유 포지션, 거래 내역, 성과 분석, 봇 상태, 시스템 등 핵심 화면이 한국어로 제공됩니다.
- **NO DATA 기본 상태 (기본값)**: 초기 실행 시 실제 트레이딩 데이터가 없는 상태가 기본이며, 임의의 수치를 0이나 성공으로 날조하지 않고 명확한 빈 상태를 표시합니다.
- **결정론적 DEMO DATA 모드**: 상단 '데모 미리보기' 버튼을 명시적으로 클릭할 때만 활성화되는 합성 데이터 픽스처입니다.
  - 모든 데모 수치는 완전한 합성 데이터(Synthetic)이며, 상시 `데모 데이터` 배지가 노출됩니다.
  - 산술적 불변식 보장: 오늘 실현 손익(+₩91,000) + 미실현 손익(+₩52,700) - 수수료(₩6,200) = 오늘 PnL(+₩137,500).
  - 3개 포지션(BTC, ETH, SOL), 5개 오늘 체결 거래, 30일 결정론적 자본 곡선.
  - '데모 종료' 클릭 시 즉시 초기 NO DATA 상태로 안전하게 복귀합니다.
- **데이터 프로바이더 아키텍처 (`TradingDataProvider`)**:
  - `NO_DATA`: 기본 빈 상태.
  - `DEMO`: 상단 '데모 미리보기'로 활성화되는 결정론적 합성 데이터.
  - `LOCAL_SNAPSHOT`: 상단 '스냅샷 가져오기'를 통해 로컬 `trading_snapshot.json`을 직접 브라우저로 로드하여 검증 및 조회.
  - `READ_ONLY_API`: 미래 백엔드 연동을 위한 읽기 전용 REST 계약 (`GET /api/trading/snapshot` 등) 준비 완료 (기본 비활성화, localhost 전용).
- **데모 스냅샷 내보내기**: 데모 모드에서 '데모 내보내기'를 클릭하여 테스트용 `trading_snapshot.demo.json`을 저장할 수 있습니다. (`synthetic: true` 명시)
- **실거래 통로 완전 차단**: 실거래/페이퍼 주문 발주 컨트롤(BUY/SELL) 및 API 키 입력란이 일체 존재하지 않으며, '페이퍼 OFF' 및 '라이브 비활성화' 안전 상태가 유지됩니다.
- **고급 기능 (Phase 6.2/6.3 오프라인 증거 콘솔 보존)**: 사이드바 '고급 기능'을 통해 72시간 무인 수집, 증거 사슬, 데이터 품질, 연구실 등 기존 연구/검증 기능을 그대로 열람할 수 있습니다.

---

## 퀀트 운영 대시보드 v0.2 (Offline Evidence & Research Console)

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
