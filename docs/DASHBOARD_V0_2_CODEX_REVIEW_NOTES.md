# DASHBOARD v0.2 CODEX REVIEW NOTES

본 문서는 Gemini의 대시보드 v0.2 UI/UX 구현 중 관찰된 스키마 불확실성, 가정사항, 그리고 향후 Codex가 최종 암호학적 머지 검토 시 확인해야 할 핵심 점검 항목을 기술합니다.

## 1. 아티팩트 자동 분류 휴리스틱 (Artifact Classifier Heuristics)
- **위치**: `dashboard/src/evidence/artifactClassifier.ts`
- **내용**: 업로드된 JSON 객체의 필드 시그니처와 파일명 휴리스틱을 조합하여 11종의 아티팩트 타입을 자동 식별합니다.
- **Codex 확인 필요사항**:
  - `actual_start_evidence.json`의 `actual_start_time_utc` 필드 외에 추가적인 systemd 로그 다이제스트 검증 필드가 필요한지 여부.
  - `epoch_manifest.json`과 `epoch_contract.json`의 구분 기준 필드(`contract_type` vs `epoch_manifest_sha256`)의 고유성.

## 2. 7단계 증거 사슬 평가 상태 전이 (Chain Evaluator State Machine)
- **위치**: `dashboard/src/evidence/chainEvaluator.ts`
- **내용**:
  - `node-provenance`에서 `actual_start_evidence.json`이 누락된 경우 즉시 `INVALID`(Fail-Closed) 처리하고 체인 전체를 `INVALID`로 판정합니다.
  - 업스트림 해시 불일치 발생 시 즉시 `MISMATCH`를 부여합니다.
- **Codex 확인 필요사항**:
  - `epoch_contract`에 기재된 `runtime_seal_sha256`과 `runtime_seal.json`의 계산 해시 대조 시 정규화(Canonical JSON) 직렬화 적용 필요 여부.

## 3. 에피스테믹 불변식 (Zero-Fabrication Invariant)
- **위치**: 모든 페이지 및 컴포넌트
- **내용**:
  - `NO_EVIDENCE` 모드에서 72H 수집 시간, 코호트 건수, 데이터 품질 측정 수치가 `0`이나 `0.0%`으로 표시되지 않도록 `—` 또는 `NOT AVAILABLE`로 엄격히 마스킹되었습니다.
  - `72h-soak` 페이지에서 72H 수집 완료 판정과 데이터 품질 판정을 2개의 독립 박스로 분리(P6.1)하여 상호 간섭을 차단했습니다.
- **Codex 확인 필요사항**:
  - 향후 실제 포스트소크 감사 시 `scripts/audit_72h_soak_deep.py`의 JSON 결과 포맷과 `DataQuality.tsx`의 필드 매핑 일관성 확인.

## 4. 보안 및 격리 (Security & Isolation)
- **위치**: 전체 프론트엔드 코드
- **내용**:
  - `fetch`, `axios`, `WebSocket`, `EventSource`, `dangerouslySetInnerHTML` 0건.
  - 파일 크기 가드 (10MB 초과 경고).
- **Codex 확인 필요사항**:
  - 향후 S3에서 오프라인으로 번들을 다운로드받아 로컬 대시보드에 일괄 드롭할 때의 최적 번들 패키징 스크립트 작성 검토.
