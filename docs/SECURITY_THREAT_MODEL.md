# 보안 위협 모델 분석 (Security Threat Model: STRIDE)

## 1. 개요
본 문서는 오프라인 마이크로스트럭처 연구 및 페이퍼 트레이딩 플랫폼의 자산, 경계, 잠재적 공격 벡터 및 완화 대책을 STRIDE 프레임워크에 기반하여 분석한다.

## 2. 자산 및 보안 경계 (Assets & Boundaries)
- **자산 A**: 라이브 72시간 수집기 인프라 (독립된 AWS EC2/S3 - 완벽 격리 대상)
- **자산 B**: 거래소 API 자격 증명 및 시크릿 키 (오프라인 환경 노출 절대 금지)
- **자산 C**: 과학적 연구 증거 및 원장 데이터 (위변조 불가성 요구)
- **자산 D**: 로컬 시스템 리소스 (디스크, CPU, 메모리)

## 3. STRIDE 위협 분석 및 대응 통제

| 위협 카테고리 | 잠재적 위협 시나리오 | 영향도 | 구현된 완화 통제 (Mitigation Controls) | 검증 상태 |
| :--- | :--- | :--- | :--- | :--- |
| **Spoofing (신원 도용)** | 위조된 마켓 데이터 주입을 통한 허위 알파 유도 | 높음 | - 데이터 출처 해시 검증 및 캐노니컬 모델 스키마 검증<br>- 비정상 타임스탬프, 역전 호가, 비유한수 차단 | PASS |
| **Tampering (데이터 변조)** | 백테스트 결과 및 시행 원장 사후 조작을 통한 과적합 은폐 | 치명적 | - SHA-256 기반 블록체인형 해시체인 원장 (`previous_hash` 검증)<br>- 변조 발생 시 `LedgerTamperError` 즉시 발생 | PASS |
| **Repudiation (부인 방지)** | 특정 전략 시도의 사전등록 사실 부인 | 높음 | - 시도 시작 전 원자적 예약(`reserve_trial`) 강제<br>- 매니페스트 해시가 원장에 영구 기록됨 | PASS |
| **Information Disclosure (정보 유출)** | 환경변수나 코드 내 AWS/거래소 비밀키 노출 및 로그 유출 | 치명적 | - `verify_no_live_credentials_in_offline_env` 검증<br>- 정규식 기반 `mask_secrets`로 로그 및 보고서 자동 마스킹<br>- Git 커밋 전 비밀값 스캔 자동화 | PASS |
| **Denial of Service (서비스 거부)** | 압축 해제 폭탄(Decompression Bomb) 또는 무한 루프 | 중간 | - Zstandard 스트리밍 압축 해제 시 바운디드 읽기 적용<br>- 경로 살균기(`sanitize_path`)로 널 바이트 및 디렉터리 순회 차단 | PASS |
| **Elevation of Privilege (권한 상승)** | 로컬 스크립트 실행을 통한 실거래 API 호출 시도 | 치명적 | - `DisabledLiveTransport`를 통해 실거래 전송 계층 영구 무력화<br>- pytest 글로벌 소켓 차단 가드로 외부 통신 원천 봉쇄 | PASS |

## 4. 결론
오프라인 연구 플랫폼은 6대 위협 벡터 전반에 대해 Fail-Closed 및 심층 방어(Defense in Depth) 원칙을 완벽히 적용하여, 시스템 외부로의 데이터 유출이나 라이브 시스템 간섭 위험을 100% 차단하였다.
