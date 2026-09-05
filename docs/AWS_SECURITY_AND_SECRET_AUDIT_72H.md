# AWS Security and Secret Audit for 72H Soak

## 1. 감사 개요 및 범위 (Audit Overview & Scope)

본 감사는 72시간 장기 수집(Soak)을 앞두고 AWS 인프라 및 코드베이스 전반의 공격 표면(Attack Surface), 시크릿 노출 여부, IAM 최소 권한 원칙 준수 상태를 정밀 점검한 결과이다.
본 감사는 순수 읽기 전용(Read-Only) 상태에서 수행되었으며, 실행 중인 라이브 IAM 정책이나 네트워크 설정을 임의 변경하지 않았다.

---

## 2. 9대 보안 핵심 감사 결과 (9-Point Security Audit Results)

### 2.1 Public Ingress (퍼블릭 수신 포트 완전 차단)
- **점검 대상**: EC2 보안 그룹 `sg-051ff65d70f3815ae` (`bitcoin-trader-collector-sg`).
- **실측 상태**:
  - 인바운드 규칙 수: 정확히 **0개** (`IpPermissions: []`).
  - 외부에서의 직접적인 TCP/UDP 접속(SSH, HTTP, RPC 등)이 100% 원천 차단됨.
- **판정**: **PASS (완전 격리)**

### 2.2 SSH Access (SSH 접근 차단 및 무력화)
- **점검 대상**: EC2 인스턴스 `i-008bc503c1136349f`.
- **실측 상태**:
  - 인바운드 22번 포트 차단.
  - 관리자 접근은 오직 AWS Systems Manager (SSM) Session Manager의 암호화된 채널을 통해서만 이루어짐.
  - `~/.ssh/authorized_keys` 내 임의 공개키 주입 없음.
- **판정**: **PASS**

### 2.3 Private API Flags (비공개 거래소 API 호출 차단)
- **점검 대상**: 수집기 설정 및 웹소켓 커넥터 코드.
- **실측 상태**:
  - `TRADING_ENABLED=false`, `ORDER_ROUTING=false`.
  - 호출 엔드포인트는 거래소의 공공 시장 데이터 웹소켓(Bithumb public ws, Binance public fstream/ws, Upbit public ws)으로 한정됨.
  - API Key/Secret 서명이 요구되는 Private REST 엔드포인트 호출 코드 경로 0개.
- **판정**: **PASS**

### 2.4 Live Flags (라이브 트레이딩 비활성화)
- **점검 대상**: 런타임 설정 `config.json` 및 봉인 명세 `runtime.json`.
- **실측 상태**:
  - `LIVE_TRADING: DISABLED`.
  - 주문 집행 루프, 브로커 연결 객체, 체결 신호 생성 로직이 런타임 실행 경로에서 물리적으로 배제됨.
- **판정**: **PASS**

### 2.5 S3 Delete Paths (원천 데이터 보존 및 삭제 API 차단)
- **점검 대상**: 소스 코드 및 S3 아카이버 모듈 (`scripts/orchestrate_closed_hour_archive.py`).
- **실측 상태**:
  - 소스 코드 내 `s3_client.delete_object` 호출 코드 0건 확인.
  - 모든 아카이브 영수증의 `cleanup_completed_at`은 `null` 유지.
  - Fail-Closed 정책에 따라 보존 주기 완료 전 일체의 삭제 금지.
- **판정**: **PASS**

### 2.6 IAM Overbreadth (IAM 권한 경계 및 최소 권한)
- **점검 대상**: 인스턴스 프로파일 역할 `bitcoin-trader-collector-role` 및 권한 경계.
- **실측 상태**:
  - S3 접근 권한은 `PutObject`, `GetObject`, `ListBucket`으로만 한정됨.
  - Permissions Boundary 정책(`v4`)이 적용되어 있으며, 와일드카드 S3 전체 삭제 또는 타 서비스 변경 권한이 원천 차단됨.
- **판정**: **PASS (엄격 최소 권한)**

### 2.7 Static Keys (코드베이스 내 정적 키 부재)
- **점검 대상**: Git 전체 히스토리 및 워킹 디렉토리.
- **실측 상태**:
  - AWS Access Key ID (`AKIA...`), 시크릿 키, 거래소 API Key/Secret이 하드코딩된 파일 없음.
  - 정규식 기반 스캔(`AKIA[0-9A-Z]{16}`, `aws_secret_access_key`) 결과: **0건**.
- **판정**: **PASS**

### 2.8 Credential Files (인스턴스 내 자격 증명 파일 부재)
- **점검 대상**: 게스트 EC2 인스턴스 파일시스템.
- **실측 상태**:
  - `/root/.aws/credentials`, `/home/ec2-user/.aws/credentials`, `/var/lib/bitcoin-trader/.aws/credentials` 파일 존재하지 않음.
  - 인증은 인스턴스 메타데이터 서비스(IMDSv2)를 통한 임시 세션 토큰으로만 처리됨.
- **판정**: **PASS**

### 2.9 Secret Leaks in Logs/Repo (로그 및 저장소 유출 점검)
- **점검 대상**: 수집기 표준 출력 로그 포맷터 및 커밋 로그.
- **실측 상태**:
  - 헤더, 쿼리 스트링, 에러 메시지에서 민감 정보(토큰, 서명) 자동 마스킹 필터 검증 완료.
  - Git 커밋 히스토리에 기밀 파일 포함 내역 없음.
- **판정**: **PASS**

---

## 3. 종합 보안 판정 (Final Security Classification)

- **보안 상태**: **PASS (SECURE)**
- **특이사항**: 라이브 환경에서 IAM 및 시크릿 관리 원칙이 엄격히 준수되고 있으며, 외부 공격 침투 경로는 0개로 유지되고 있음.
