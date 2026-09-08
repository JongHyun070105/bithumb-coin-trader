# Post-72H Flash Integration Backlog (2026-09-08)

## 1. 개요 및 엔지니어링 역할
- **역할 정의**: 구현 / 통합 / 정리 엔지니어 (Implementation / Integration / Cleanup Engineer)
- **금지 영역**: 과학적 감사관, 연구 의사결정자, 프로덕션 트레이딩 승인자 역할 절대 금지
- **기반 브랜치**: \`gemini/post72h-light-integration-20260908\` (베이스: \`2da9363c2d66258516427206018bd046007dcdbe\`)
- **현재 과학적 상태 동결**:
  - 72H: NATURAL COMPLETION EXPECTED AROUND 2026-09-08 14:40 KST / FINAL RESULT NOT YET SCIENTIFICALLY VERIFIED
  - REAL DQ: NOT RUN
  - ALPHA: UNPROVEN
  - PAPER: NOT STARTED
  - LIVE: DISABLED
  - PRIVATE API: DISABLED

---

## 2. 태스크 분류 체계

### [FLASH_SAFE] 이번 스프린트에서 안전하게 구현하는 태스크
1. **트레이딩 데이터 공급자 추상화 (\`TradingDataProvider\`)**:
   - \`NoDataProvider\`, \`DemoTradingProvider\`, \`LocalSnapshotProvider\`, \`ReadOnlyApiProvider\` 인터페이스 및 구현
   - 명시적 데이터 소스 메타데이터 상태: \`NO_DATA\`, \`DEMO\`, \`LOCAL_SNAPSHOT\`, \`READ_ONLY_API\`
2. **로컬 스냅샷 JSON 임포트 & 검증 레이어**:
   - \`trading_snapshot.json\` 로컬 파일 파싱, 엄격한 스키마 검증 (\`validateTradingSnapshot\`), 정규화 (\`normalizeTradingSnapshot\`)
   - 누락 필드를 0으로 왜곡하지 않고 명확한 에러 표출
   - 개발 및 테스트용 합성 데모 스냅샷 내보내기 기능 (\`trading_snapshot.demo.json\`, \`synthetic: true\` 명시)
3. **읽기 전용 API 계약 및 클라이언트 준비**:
   - TypeScript API 응답 계약 정의 (\`apiContract.ts\`)
   - 프론트엔드 API 클라이언트 (\`apiClient.ts\`: localhost/127.0.0.1 전용, AbortController, 타임아웃, graceful failure, credentials 없음, 기본 비활성화)
4. **상태 UX 완성**:
   - \`LOADING\`, \`NO_DATA\`, \`DEMO\`, \`LOCAL_SNAPSHOT\`, \`READ_ONLY_API\`, \`ERROR\`, \`STALE\` 상태 처리
   - 데이터 지연/stale 표시 ("데이터가 오래되었습니다" 및 마지막 업데이트 시각)
   - 수동 새로고침 버튼 준비 (과도한 폴링 방지)
5. **트레이딩 데이터 포매터 중앙화 (\`formatters.ts\`)**:
   - KRW 화폐, 백분율, PnL 부호/색상, 소요 시간, KST 시각, 자산/페어 표기 중앙화
   - 양수/음수/0/누락값/대규모 KRW 포맷팅 단위 테스트
6. **대시보드 일관성 감사 및 UI 다듬기**:
   - 대시보드 메인, 포트폴리오, 보유 포지션, 거래 내역, 성과 분석 간 수치 일치 확인 및 보정
   - 해시 라우팅 fallback 및 히스토리 내비게이션 안정화
   - 시스템/헬스 페이지 강화 (미확인 필드는 가짜 녹색불 대신 \`—\` 표시)
   - 프론트엔드 React \`ErrorBoundary\` 추가
7. **포스트 72H 읽기 전용 인벤토리 및 아스트라 인계 문서**:
   - 예상 종료 시각(14:40 KST) 경과 확인 후, 로컬 아티팩트 및 증거 후보 현황 인벤토리 (\`POST_72H_EVIDENCE_INVENTORY_ONLY.md\`)
   - 인계 문서 (\`docs/POST72H_ASTRA_HANDOFF_20260908.md\`)
8. **단위 테스트 및 게이트 검증**:
   - 신규 데이터 레이어, 스냅샷 검증, 포매터, API 클라이언트 테스트 추가 (기존 105개 테스트 100% 보존)

---

### [ASTRA_REQUIRED] 상위 모델(Astra/Codex) 전용 — 이번 스프린트 수정 금지
- 72H 소크 최종 완성 여부 검증 및 PASS/FAIL 판정
- 실제 시작 시각 증거 확정 (\`actual_start_evidence.json\` 생성 금지)
- 공식 에포크 계약 합성 (\`compose_epoch_contract.py\`)
- 공식 에포크 매니페스트 빌드 (\`build_epoch_manifest.py\`)
- 심층 데이터 품질(DQ) 감사 및 결측률 판정 (\`audit_72h_soak.py\`)
- 연구용 단일 시계열 캐노니컬 변환 및 파티셔닝 (\`research_cli.py\`)
- 전략 알파 발굴, 백테스트 결과 검증, 통계적 유의성 평가
- 코어 알고리즘 수정 (수집기, 아카이버, 런타임, 리스크 엔진)

---

### [BLOCKED_ON_REAL_DQ] 실제 DQ 통과 전까지 차단된 항목
- 실제 72H 수집 데이터 기반의 실시간 트레이딩 스냅샷 생성
- 프로덕션 파티션 데이터셋 생성 및 모델 학습 파이프라인 가동

---

### [BLOCKED_ON_PAPER] 페이퍼 인가 전까지 차단된 항목
- 페이퍼 트레이딩 엔진 프로세스 기동
- 실시간 빗썸 공개 웹소켓 피드 구독 및 오더북 매칭 시뮬레이션

---

### [BLOCKED_ON_LIVE] 라이브 인가 전까지 차단된 항목
- 빗썸 프라이빗 API 키 설정 및 발주 엔드포인트 활성화
- 실계좌 자산 조회 및 실제 주문 집행 (Fail-Closed 유지)
