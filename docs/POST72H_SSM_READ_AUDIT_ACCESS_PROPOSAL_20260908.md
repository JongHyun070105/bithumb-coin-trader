# Post-72H Audit: Least-Privilege SSM Read Access Proposal (2026-09-08)

## 1. 목적 및 보안 원칙 (Purpose & Security Principles)

본 문서는 현재 차단된 Post-72H 최종 과학 감사를 재개하기 위해 필요한 AWS SSM 최소 권한(Least-Privilege) 접근 설계안을 제시한다.

**절대 규칙:**
- 본 문서는 **설계안(Design Only)**이며, 현재 세션에서 IAM 권한을 변경하거나 `terraform apply` 또는 AWS API를 통해 적용하지 않는다.
- `ssm:SendCommand`는 게스트 운영체제 내에서 루트 권한 셸 명령을 실행할 수 있는 매우 강력한 운영 권한이다.
- 범용 `AWS-RunShellScript`에 대한 와일드카드(`*`) 허용은 게스트 프로세스 종료, 런타임 파일 수정, 증거 오염(Mutation) 위험을 초래하므로 **절대 권장하지 않는다.**

---

## 2. 현행 IAM / SSM 아키텍처 실사 결과 (Current Architecture Audit)

`infra/aws/identity/terraform-provisioner-permissions-policy.json.example` 및 실제 AWS IAM 정책 실사 결과:

1. **현재 허용된 SSM 권한:**
   - `ssm:StartSession`: `Project=bitcoin-trader`, `Environment=aws-apne2-research` 태그가 부착된 EC2 인스턴스 대상
   - `ssm:StartSession`: 전용 세션 매니저 문서 `arn:aws:ssm:ap-northeast-2:080109295433:document/SSM-SessionManagerRunShell` 대상
   - `ssmmessages:OpenDataChannel`: 대화형 세션용
   - `ssm:ResumeSession`, `ssm:TerminateSession`: 본인 세션 한정
   - `ssm:DescribeSessions`, `ssm:GetConnectionStatus`, `ssm:DescribeInstanceInformation`: 전체
2. **차단된 핵심 원인 (Direct Blocker):**
   - 프로비저너 역할은 **대화형 세션 매니저(`ssm:StartSession`)**만 허용되었으며, 비대화형 자동 감사 스크립트 실행에 필요한 **`ssm:SendCommand`가 의도적으로 누락**되어 있음.
   - 명령 결과 수신에 필요한 `ssm:GetCommandInvocation`, `ssm:ListCommands`, `ssm:ListCommandInvocations` 역시 누락되어 있음.
3. **S3 아카이브 접근 제어 상태:**
   - `Sid: DenyArchiveObjectDataPlane`에 의해 `s3:GetObject`가 명시적 거부(`Deny`)되어 있어 S3 상의 아카이브 데이터 플레인 읽기(`head-object` 포함)가 403으로 차단됨.
   - `s3:ListBucket`은 허용되어 메타데이터 목록 확인(`ListObjectsV2`)만 가능한 상태임.

---

## 3. 접근 설계안 비교 (Comparison of Access Options)

### 옵션 A: 제약된 `AWS-RunShellScript` + 엄격한 IAM Condition
- **개념:** AWS 관리형 기본 문서(`AWS-RunShellScript`)를 사용하되, IAM 정책에서 대상 인스턴스, 리전, 계정을 엄격히 한정.
- **장점:** 별도의 커스텀 SSM 문서를 생성/배포할 필요가 없음.
- **단점 및 위험성:** IAM 레벨에서는 전송되는 셸 스크립트 내용(Content)이 읽기 전용인지 검증할 수 없음. 운영자의 실수나 모델의 임의 명령으로 게스트 런타임 파일이 삭제(`rm`), 이동(`mv`), 변조될 위험이 상존함.

### 옵션 B (권장): 고정형 읽기 전용 SSM 커스텀 문서 (Fixed-Content SSM Document)
- **개념:** 리포지토리에 정의된 **읽기 전용 감사 명령 번들만 포함된 SSM Document**(`BitcoinTrader-Post72h-ReadOnlyAudit`)를 사전에 등록하고, IAM 정책은 **오직 이 문서에 대해서만** `ssm:SendCommand`를 허용.
- **장점 (Fail-Safe):**
  - 운영자나 AI 어시스턴트가 임의의 위험 명령(`kill`, `rm`, `truncate`, `systemctl restart`)을 전송하는 것이 구조적으로 불가능함.
  - 전송되는 모든 명령이 사전 검토된 고정 스크립트이므로 감사 재현성(Reproducibility)과 데이터 불변성(Immutability)이 완벽히 보장됨.
- **단점:** SSM 커스텀 문서를 AWS 계정에 사전 1회 생성해야 함.

---

## 4. 권장 Least-Privilege IAM 정책 설계안 (옵션 B 기반)

사용자/권한 관리자가 인가 후 추가할 수 있는 최소 권한 IAM 인라인/관리형 정책 블록 명세:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowSendReadOnlyAuditDocumentOnly",
      "Effect": "Allow",
      "Action": "ssm:SendCommand",
      "Resource": [
        "arn:aws:ssm:ap-northeast-2:080109295433:document/BitcoinTrader-Post72h-ReadOnlyAudit"
      ]
    },
    {
      "Sid": "AllowSendCommandOnTargetInstanceOnly",
      "Effect": "Allow",
      "Action": "ssm:SendCommand",
      "Resource": [
        "arn:aws:ec2:ap-northeast-2:080109295433:instance/i-008bc503c1136349f"
      ],
      "Condition": {
        "StringEquals": {
          "aws:ResourceTag/Project": "bitcoin-trader",
          "aws:ResourceTag/Environment": "aws-apne2-research"
        }
      }
    },
    {
      "Sid": "AllowGetAuditCommandInvocations",
      "Effect": "Allow",
      "Action": [
        "ssm:GetCommandInvocation",
        "ssm:ListCommandInvocations",
        "ssm:ListCommands"
      ],
      "Resource": "*"
    }
  ]
}
```

*참고: `ssm:GetCommandInvocation`, `ssm:ListCommands`는 AWS 액션 정의 상 리소스 레벨 권한 분리가 지원되지 않아 `Resource: "*"`가 요구됨.*

---

## 5. 단계별 적용 및 실행 승인 절차 (Authorization Procedure)

1. **사용자 검토 및 승인:** 인프라 소유자가 본 권한 명세와 `POST72H_GUEST_READONLY_EVIDENCE_COMMANDS_20260908.md`의 명령 목록을 승인.
2. **SSM 문서 등록:** 사용자 권한으로 `BitcoinTrader-Post72h-ReadOnlyAudit` 문서 생성 (또는 임시로 옵션 A 적용 시 엄격히 인스턴스 한정 적용).
3. **감사 재개:** Astra 세션에서 사전 승인된 감사 명령 번들만 실행.
