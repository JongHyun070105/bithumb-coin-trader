# AWS 30H 무인 검증 V1 사전 점검 및 봉인 확인서 (2026-09-12)

## 1. 개요 및 최종 상태

- **30H 승인 준비 완료 (READY TO AUTHORIZE 30H)**: **YES** (인간 검토자 승인 대기)
- **30H 실행 시작 (30H STARTED)**: **NO**
- **런치 승인 여부 (LAUNCH AUTHORIZED)**: **false**
- **실제 시작 시각 (ACTUAL START TIME UTC)**: **null**
- **실행 중 프로세스 / systemd 유닛**: **0개 (없음)**
- **S3 임시 프리픽스 객체 수**: **0개 (완전 무결)**
- **알파 상태**: **미검증 (UNPROVEN)**
- **페이퍼 트레이딩**: **미시작 (NOT STARTED)**
- **라이브 트레이딩**: **비활성화 (DISABLED)**
- **Private API / 거래소 API Key**: **비활성화 / 미사용 (DISABLED)**

---

## 2. 베이스라인 및 코드 프로비넌스

- **공식 머지 베이스 커밋 (authoritative main HEAD)**:
  `00464e585e50308bead0924940301d9264ee2dcf`
  (PR #3 머지 커밋: 30시간/108,000초 런처 및 교차 레이어 가드 엄격 바인딩 적용)
- **30H V1 준비 브랜치**:
  `codex/aws-30h-prep-v1-20260912-00464e5`
- **Git Tree SHA**:
  `aee25e6d81ccb36104519794ac1b735e3d41d06d`
- **결정론적 아카이브 SHA-256**:
  `72df9bb1697919aa2d9f07c83841528936a24060dd0d5c9fd273b2c8856882db`
- **이전 V0 준비 브랜치 보존 (SUPERSEDED)**:
  `codex/aws-30h-prep-20260912-ac0351c` (커밋 `4463fd83a56b8fa6461e1d0e67cf05dbd53afffe`)
  (기록용으로 온전히 보존되며, 삭제/재작성/실행에 사용되지 않음)

---

## 3. 30H 타이밍 계약 및 식별자

| 항목 | 설정값 |
| :--- | :--- |
| **컬렉터 에포크 (Collector Epoch)** | `aws-validation-30h-20260912-00464e5` |
| **컬렉터 런 ID (Run ID)** | `aws-validation-30h-run-20260912T091500Z-00464e5` |
| **수집 기간 (Collection Duration)** | `108000s` (30시간) |
| **종료 정리 타임아웃 (Finalization Timeout)** | `180s` |
| **수퍼바이저 하드 실링 (Hard Ceiling)** | `108300s` |
| **systemd 런타임 최대 제한 (RuntimeMaxSec)** | `108400s` |
| **아카이브 폴링 주기 / 그레이스** | `30.0s` / `600s` |
| **원격 아카이브 S3 버킷** | `bitcoin-trader-aws-apne2-research-ap-northeast-2-080109295433` |
| **원격 S3 프리픽스** | `market-data/temporary/aws-validation-30h-20260912-00464e5` |
| **로컬 런타임 데이터 루트** | `/var/lib/bitcoin-trader/30h-validation/aws-validation-30h-20260912-00464e5` |
| **게스트 격리 워크트리** | `/var/lib/bitcoin-trader/runtime-worktrees/aws-validation-30h-20260912-00464e5` |
| **수집 피드 구성 (76개)** | 빗썸 20개 마켓(60 파티션), 바이낸스 4개 페어(8 파티션), 업비트 4개 마켓(8 파티션) |
| **디스크 임계치** | 경고 70%, 위험 80%, 임계(종료) 90% |
| **정리(Cleanup) 설정** | `False` (사후 감사를 위한 로컬 보존) |

---

## 4. 봉인 아티팩트 및 해시 3방향 일치 검증

로컬 저장소, 봉인 파일, EC2 게스트 인스턴스에 배포된 아티팩트의 SHA-256 해시가 완전히 일치함을 검증했습니다:

| 파일명 | 로컬 저장소 (`infra/aws/seals/`) | 게스트 EC2 (`/var/lib/bitcoin-trader/launch-artifacts/...`) | 상태 |
| :--- | :--- | :--- | :--- |
| `aws-validation-30h-20260912-00464e5.runtime.json` | `37ea17bd0c5e42c512e6af751c8e192c8e54d0737a291d614e911438977de9cd` | `37ea17bd0c5e42c512e6af751c8e192c8e54d0737a291d614e911438977de9cd` | **일치 (PASS)** |
| `aws-validation-30h-20260912-00464e5.launch-command.json` | `3b7132be8d65ae24fd2a1f91f185c9b2eb3b2c6b12d7d090724a3f0ac7371589` | `3b7132be8d65ae24fd2a1f91f185c9b2eb3b2c6b12d7d090724a3f0ac7371589` | **일치 (PASS)** |
| `aws-validation-30h-20260912-00464e5.launch-provenance.json` | `6d711a10b02859919846cf6460143b21d88e47ad201115bd896b281f5f738a00` | `6d711a10b02859919846cf6460143b21d88e47ad201115bd896b281f5f738a00` | **일치 (PASS)** |
| `launch.sh` (래퍼 스크립트) | `301f5936195272fb6a25f2e89f04befb6110d4e79b93f04abe39a8eb75e87cab` | `301f5936195272fb6a25f2e89f04befb6110d4e79b93f04abe39a8eb75e87cab` | **일치 (PASS)** |

- **런타임 구성 카노니컬 핑거프린트 (Config Fingerprint)**:
  `ee229e6a96c2feef3be41ab4347f4d2edc5d36be525352caba243edf64c55c35`

---

## 5. 게스트 EC2 환경 검증

- **대상 인스턴스**: `i-008bc503c1136349f` (`ap-northeast-2a`, `t3.medium`)
- **운영체제**: Amazon Linux 2023 (`Linux 6.1.134-142.203.amzn2023.x86_64`)
- **게스트 워크트리 상태**:
  - 경로: `/var/lib/bitcoin-trader/runtime-worktrees/aws-validation-30h-20260912-00464e5`
  - 커밋: `00464e585e50308bead0924940301d9264ee2dcf` (HEAD 일치)
  - 트리: `aee25e6d81ccb36104519794ac1b735e3d41d06d` (일치)
  - 상태: clean
- **게스트 테스트 결과**:
  - 독립 venv: `/var/lib/bitcoin-trader/runtime-envs/aws-validation-30h-20260912-00464e5-test-py311`
  - 7개 핵심 테스트 스위트 (109개 항목): **109 passed, 0 failed** in 29.28s
- **렌더 전용(Dry-run) 실행 검증**:
  - 명령어: `sudo /var/lib/bitcoin-trader/launch-artifacts/aws-validation-30h-20260912-00464e5/launch.sh`
  - 결과: 정상 0 종료, 렌더링된 `systemd-run` 명세 확인
  - 렌더링 유닛명: `bitcoin-trader-30h-aws-validation-30h-run-20260912T091500Z-00464e5.service`
  - 확인 속성: `RuntimeMaxSec=108400s`, `UID=bitcoin-trader`, `--collection-duration-seconds 108000`
  - 검증 후 유닛 및 프로세스 상태: `inactive`, 잔류 프로세스 0개

---

## 6. 인프라, IAM 및 Terraform 상태 검증

- **Terraform Plan**:
  - 상태 파일: `/Users/macintosh/Documents/ChatGPT/bitcoin-trader/infra/aws/terraform.tfstate` (lineage `5e8ff4b0-1d32-ed2e-9ed0-a9e68d3f3ccf`)
  - 결과: `0 to add, 0 to change, 0 to destroy` (인프라 드리프트 없음)
- **IAM 권한 경계 (Permissions Boundary)**:
  - ARN: `arn:aws:iam::080109295433:policy/bitcoin-trader-collector-boundary`
  - 기본 버전: `v6` (정상 적용)
- **임시 IAM 버전 관리 권한 정리**:
  - 상태: **차단 / 미수행 (BLOCKED / NOT PERFORMED)**
  - 사유: 프로비저너 및 개발자 비-루트 CLI에 `iam:DeleteUserPolicy` 권한 부재, 보안 수칙에 의거 루트 계정 사용 절대 금지 원칙 준수. Fail-closed 유지.

---

## 7. 봉인 보존 및 안전 수칙

1. **승인 없는 실행 금지**: `--launch` 플래그는 인간 검토자의 명시적 지시가 있을 때만 실행합니다.
2. **증거 불변성**: Fresh45의 검증 증거는 완전히 보존되어 있으며 수정되지 않습니다.
3. **Fail-Closed 동작 보장**: 런처와 수퍼바이저의 108,000초 기간 불일치, 중복 인자, 또는 인자 누락 시 즉각 실행이 차단됩니다.
