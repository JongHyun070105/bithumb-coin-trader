# 공식 AWS Fresh 6h 신뢰성 검증 사전 준비 보고서 (6H_PREPARATION_REPORT)

- **보고서 작성 시각**: `2026-09-18 18:07:00 KST` (`2026-09-18T09:07:00Z UTC`)
- **런 식별자 (Run ID)**: `aws-validation-observability-6h-run-20260918T095000Z-v1`
- **에포크 (Epoch)**: `aws-validation-observability-6h-20260918-20260918T095000Z-v1`
- **런타임 소프트웨어 커밋**: `e752c3da084d4e27ec7b81b1518c54bc112dba54` (origin/develop 최신)
- **런타임 소프트웨어 트리**: `f91bc6abcb4b6efae652481405e4ce2893c6238a`
- **아티팩트 봉인 커밋**: `1920d91` (`chore(reliability): seal fresh 6h validation launch artifacts (aws-6h-v1)`)
- **수집 듀레이션**: **21,600초 (6시간 = 360분)**
- **계획 수집 시작 (Planned Start)**: **2026-09-18 18:50:00 KST** (`2026-09-18T09:50:00Z UTC`)
- **적격 수집 시작 (Qual Start)**: **2026-09-18 19:00:00 KST** (`2026-09-18T10:00:00Z UTC`)
- **목표 적격 풀 아워**: **5시간 (`target_full_hours = 5`)**
- **적격 코호트 목록**:
  1. `2026-09-18_10` (19:00 ~ 20:00 KST) -> 20:10 KST 아카이브
  2. `2026-09-18_11` (20:00 ~ 21:00 KST) -> 21:10 KST 아카이브
  3. `2026-09-18_12` (21:00 ~ 22:00 KST) -> 22:10 KST 아카이브
  4. `2026-09-18_13` (22:00 ~ 23:00 KST) -> 23:10 KST 아카이브
  5. `2026-09-18_14` (23:00 ~ 00:00 KST) -> 00:10 KST 아카이브
- **워밍업 코호트**: `2026-09-18_09` (18:50 ~ 19:00 KST, 10분)
- **잔여 수집 코호트**: `2026-09-18_15` (00:00 ~ 00:50 KST, 50분)
- **자연 완수 예정 시각**: **2026-09-19 00:50:00 KST** (`2026-09-18T15:50:00Z UTC`)
- **하드 데드라인**: **2026-09-19 02:00:00 KST** (1시간 10분의 넉넉한 안전 마진 확보)

---

## 1. 사전 오염 부재 및 게이트 검증 (PASS)

| 점검 항목 | 판정 | 상세 실증 내용 |
| :--- | :---: | :--- |
| **S3_PREFIX_EMPTY** | **PASS** | `s3://.../market-data/temporary/aws-validation-observability-6h-...-v1/` `KeyCount = 0` (완전 공백 실사) |
| **LOCAL_DATA_ROOT_ABSENT** | **PASS** | `/var/lib/bitcoin-trader/6h-validation/...-v1` 사전 미존재 확인 |
| **LOCAL_ARTIFACTS_DIR_ABSENT** | **PASS** | 배포 전 `/var/lib/bitcoin-trader/launch-artifacts/...-v1` 사전 미존재 확인 |
| **NO_ACTIVE_PROCESSES** | **PASS** | EC2 상에 이전 3h 프로세스 전원 정상 종료 확인 (활성 프로세스 0개) |
| **DISK_SPACE** | **PASS** | 가용 디스크 91.28 GB (기준 5 GB 대비 18배 이상) |
| **BYTE_IDENTICAL_DEPLOYMENT** | **PASS** | 로컬 봉인 6대 파일과 EC2 배포 6대 파일 간 SHA256 100% 일치 실증 |

---

## 2. 6대 아티팩트 SHA256 체크섬 대조표

| 파일명 | 로컬 봉인 해시 (`1920d91`) | EC2 배포 해시 | 판정 |
| :--- | :--- | :--- | :---: |
| `aws-validation-observability-6h-...runtime.json` | `905de347b68b101eaeb348899d8d3d2cba80f33d0d1a09082e02a616cff95d1d` | `905de347b68b101eaeb348899d8d3d2cba80f33d0d1a09082e02a616cff95d1d` | **MATCH** |
| `identity.json` | `f1e4535c46a66fca9fdf8529270cf5388292f3f7471e3b49432b322c40143c1f` | `f1e4535c46a66fca9fdf8529270cf5388292f3f7471e3b49432b322c40143c1f` | **MATCH** |
| `launch-command.json` | `f82e4cc3a564e2d16150457029d6ff91208cabf92f6fcbed315cfcdf2b258bac` | `f82e4cc3a564e2d16150457029d6ff91208cabf92f6fcbed315cfcdf2b258bac` | **MATCH** |
| `launch-ec2.sh` | `cc2823511378533a1dcdd27f91e9ef191f2ad02b602fdf609b75d81af53b1c60` | `cc2823511378533a1dcdd27f91e9ef191f2ad02b602fdf609b75d81af53b1c60` | **MATCH** |
| `launch.sh` | `035411d5620bf63c081ac5265ec21faadb819c71118a1b37681e1e636916b7c0` | `035411d5620bf63c081ac5265ec21faadb819c71118a1b37681e1e636916b7c0` | **MATCH** |
| `sealed-manifest.json` | `6c9b37276ff118a8cdd03a686e861ee5320015cd4ee39af6e3d93817334c22e3` | `6c9b37276ff118a8cdd03a686e861ee5320015cd4ee39af6e3d93817334c22e3` | **MATCH** |

---

## 3. 글로벌 런칭 회계

- **상한**: `NEW GLOBAL AWS LAUNCH CAP = 6`
- **소진 내역**: V8 (#1) + V10 (#2) + V12 (#3) + V13 (#4) + 3h-v1 (#5) = **5 / 6 소진**
- **이번 런칭**: **글로벌 런칭 #6 (마지막 6/6 소진)**
- **단 1회(EXACTLY-ONCE) 기동 엄수**: 중복 런칭 시도 절대 금지
