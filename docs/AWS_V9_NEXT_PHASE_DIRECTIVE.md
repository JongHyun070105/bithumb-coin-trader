==================================================
NEXT PHASE DIRECTIVE — V9.1 STABILIZATION → AWS MIGRATION
==================================================

현재 진행 중인 Strategy V9 72시간 collector/audit 작업이 완료된 이후의
후속 작업을 지금부터 미리 등록한다.

IMPORTANT:

이 지시는 현재 실행 중인 PID 30933 collector를 조기 종료하거나
현재 72시간 epoch에 영향을 주라는 뜻이 아니다.

현재 우선순위는 기존 지시대로:

1. PID 30933 uninterrupted 72h 완료
2. graceful shutdown
3. original artifact freeze
4. FULL-SCAN final audit
5. V9/V9.1 findings 확정

이다.

아래 AWS migration 작업은
위 과정이 완료되고 V9.1 코드가 안정화된 이후에만 시작한다.

==================================================
1. 최종 목표
==================================================

현재 MacBook에서 실행 중인 collector/trading infrastructure를
장기적으로 AWS로 이전한다.

이유:

- 사용자가 학교 생활 중 노트북을 자주 닫거나 이동할 예정
- collector 및 trading bot은 24/7 availability가 필요
- 로컬 Wi-Fi/LAN 전환, sleep, 전원 상태에 의존하지 않는 환경 필요
- 장기 prospective microstructure dataset을 안정적으로 수집해야 함
- 향후 paper/live trading 역시 항상 켜져 있는 서버가 필요함

단,

AWS 이전 자체가 live trading 승인이라는 뜻은 아니다.

AWS에서 충분한 infrastructure soak과 paper validation을 통과하기 전까지:

live_trading_ready = false

를 유지한다.

==================================================
2. 현재 AWS 예산
==================================================

사용자는 현재 AWS promotional credit을 보유하고 있다.

2026-08-27 기준 대략:

총 남은 credit:
US$114.49

활성 credit 1:
AWS Free Tier
발행 US$100
남은 약 US$94.49

활성 credit 2:
Explore AWS / AWS Budgets 관련
발행 US$20
남은 US$20

두 credit 모두 대략:

2026-12-13 만료

이다.

따라서 AWS architecture는
현재 credit 범위에서 효율적으로 운영하는 것을 우선한다.

그러나 비용 절감을 위해 데이터 무결성 또는 시스템 안전성을 희생하지 않는다.

AWS 계정에서 실제 credit eligibility가 조회 가능하다면
공식 billing/credits 상태를 다시 확인하라.

비용 산정 시 최신 AWS Seoul region 실제 가격을 사용하라.
오래된 가격을 하드코딩하지 않는다.

==================================================
3. AWS REGION
==================================================

우선 region 후보:

ap-northeast-2
Seoul

을 기본으로 검토한다.

이유:

- Bithumb / Upbit가 한국 시장
- microstructure 및 cross-market 연구에서 local receive timestamp가 중요함
- target execution venue인 Bithumb과 물리적/네트워크 거리를 줄이는 것이 합리적임

하지만 단순히 “서울이니까 빠르다”고 가정하지 않는다.

AWS 이전 후 반드시:

Bithumb
Binance
Upbit

각 WebSocket/API에 대한 실제 latency/clock-offset distribution을 측정하여
Mac 환경과 비교한다.

==================================================
4. AWS 이전은 새로운 DATA EPOCH다
==================================================

매우 중요하다.

Mac 환경과 AWS 환경의 microstructure data를
아무 구분 없이 동일한 환경의 dataset으로 합치지 않는다.

AWS로 이전하면 다음이 바뀐다.

- network path
- RTT
- ISP
- receive latency
- clock/NTP environment
- operating system
- scheduler behavior
- filesystem/write latency
- reconnect characteristics

따라서 AWS deployment부터는 새로운 epoch로 정의한다.

예:

V9.2-AWS

또는 당시 프로젝트 numbering convention에 맞는
명확한 새 epoch 이름을 사용한다.

반드시 metadata에 다음을 포함한다.

collector_epoch
environment_id
region
instance_type
host_id
run_id
collector_version
git_commit
config_fingerprint
schema_version
clock_source
process_start
AWS availability zone if appropriate

Mac V9/V9.1과 AWS epoch를 구별할 수 있어야 한다.

==================================================
5. AWS 이전 시작 조건
==================================================

AWS provisioning을 시작하기 전에 다음 조건을 확인한다.

A.

현재 V9 72h final audit 완료

B.

현재 epoch의 알려진 문제와
V9.1 수정사항이 문서화됨

C.

최소한 다음 V9.1 수정이 검증됨:

- Binance orderbook symbol/market preservation
- Binance usable timestamp semantics 확보 또는 명확한 limitation
- local monotonic receive timestamp
- wall-clock receive timestamp
- durable metrics
- writer fail-closed
- queue/drop accounting
- run ID
- manifest integrity
- atomic file/manifest operations
- bounded status tooling
- correct disk calculation
- balanced exchange/stream sampling

D.

전체 unit tests PASS

E.

synthetic/adversarial infrastructure tests PASS

F.

compile/static/diff checks PASS

G.

secret scan PASS

H.

로컬 V9.1 smoke/short-soak PASS

위 조건이 충족되지 않으면
AWS부터 만들어 놓고 문제를 숨기지 않는다.

먼저 V9.1을 안정화한다.

==================================================
6. 로컬 V9.1 SHORT VALIDATION
==================================================

V9.1을 AWS로 바로 배포하지 않는다.

현재 V9 epoch가 완전히 종료된 후
로컬 환경에서 수정된 V9.1 collector를 별도의 새 run으로 짧게 검증한다.

목적:

- startup
- WebSocket subscription
- market attribution
- timestamp
- monotonic clock
- writer
- compression
- manifests
- metrics
- graceful shutdown
- deterministic replay

등의 correctness 확인.

이 검증은 alpha research를 위한 데이터 수집이 아니라
deployment readiness smoke test다.

필요 이상으로 며칠씩 로컬에서 반복하지 말되,
AWS에서 명백한 코드 버그를 발견하지 않을 수준까지 검증한다.

==================================================
7. AWS 기본 architecture
==================================================

처음부터 ECS/EKS/Kubernetes 등의 복잡한 architecture를 사용하지 않는다.

현재 규모에는 단순하고 감사 가능한 architecture를 우선한다.

우선 다음 구조를 설계하라.

AWS Seoul

EC2
- microstructure collector
- feature/signal service
- trading/paper engine
- health monitor
- systemd services

EBS gp3
- OS
- source
- local application state
- hot raw partitions
- temporary compression/upload staging

S3
- canonical long-term raw archive
- manifests
- optional derived research datasets

SSM Parameter Store 또는 Secrets Manager
- exchange credentials
- notification credentials
- other secrets

CloudWatch
- process health
- disk usage
- memory
- CPU
- collector heartbeat
- reconnect/error metrics
- alarms

AWS Budgets
- cost alerts

가능하면 public inbound SSH를 열지 않고:

AWS Systems Manager Session Manager

를 사용한다.

==================================================
8. EC2 INSTANCE SELECTION
==================================================

현재 collector workload를 실제 측정하여 instance를 선정한다.

무조건 큰 instance를 고르지 않는다.

초기 target:

2 vCPU
2~4 GiB RAM

정도를 후보로 삼되 실제 필요량을 측정한다.

후보 예:

t3.small
t3.medium
t4g.small
t4g.medium

등.

ARM64를 선택한다면:

- Python dependencies
- native libraries
- binaries
- package compatibility

를 먼저 검증한다.

호환성/운영 단순성이 중요하면 x86_64를 선택해도 된다.

선정 근거에는:

expected monthly cost
CPU headroom
RAM headroom
network behavior
architecture compatibility

를 포함한다.

==================================================
9. SPOT INSTANCE 사용 금지
==================================================

collector/trading primary node에는
기본적으로 Spot instance를 사용하지 않는다.

이 시스템은:

continuous prospective collection

이 중요하므로
예고 없는 instance termination이 research integrity를 해칠 수 있다.

기본은 On-Demand로 설계한다.

향후 batch backtest/research worker에는
Spot을 별도로 고려할 수 있다.

==================================================
10. EBS POLICY
==================================================

현재 raw data ingestion은 대략:

23 GiB/day

수준이었다.

uncompressed 상태로 EBS에 장기간 누적하지 않는다.

EBS는:

HOT BUFFER

용으로 사용한다.

예:

최근 1~3일 raw
+
compression/upload staging

정도만 유지하는 architecture를 우선한다.

EBS 사용량이 threshold를 넘으면
CloudWatch alarm을 발생시킨다.

예:

70%
80%
90%

또는 실제 capacity에 맞는 reasonable threshold.

==================================================
11. S3 CANONICAL ARCHIVE
==================================================

finalized raw partition은
검증 후 S3에 장기 보존한다.

권장 logical structure 예:

s3://<private-bucket>/
  microstructure/
    v9.2-aws/
      bithumb/
        orderbook/
        trade/
        ticker/
      binance/
        orderbook/
        trade/
      upbit/
        orderbook/
        trade/

실제 naming은 기존 partitioning scheme과
research replay 효율을 보고 결정한다.

bucket은 public access를 완전히 차단한다.

가능하면:

- Block Public Access
- server-side encryption
- versioning 여부 검토
- least privilege IAM

를 적용한다.

==================================================
12. COMPRESSION PIPELINE
==================================================

AWS 장기 수집 전
V9.1에서 zstd compression을 benchmark하고 적용한다.

대표 partition 대상으로:

zstd level 1
zstd level 3
zstd level 6

등을 비교한다.

측정:

compression ratio
compression throughput
decompression throughput
CPU
replay performance

적절한 level을 선택한다.

canonical flow:

raw active JSONL
↓
partition finalized
↓
original SHA-256
↓
record count
↓
zstd temp file
↓
fsync
↓
atomic rename
↓
compressed SHA-256
↓
decompression verification
↓
byte-for-byte original hash verification
↓
S3 upload
↓
S3 upload verification
↓
local retention policy

원본을 검증 없이 삭제하지 않는다.

==================================================
13. S3 UPLOAD INTEGRITY
==================================================

S3에 업로드됐다는 API success만으로
archive 성공을 선언하지 않는다.

각 artifact에 대해 가능한 범위에서:

- local compressed size
- compressed SHA
- original logical SHA
- record count
- manifest
- S3 object metadata
- upload completion

를 확인한다.

S3 object key에는
secret 또는 개인 정보를 포함하지 않는다.

==================================================
14. RAW와 DERIVED DATASET 분리
==================================================

S3 canonical raw는:

AUDIT / REPLAY SOURCE OF TRUTH

이다.

연구 속도를 위해 별도의:

Parquet
Arrow

등 derived dataset을 만들 수 있다.

그러나 derived dataset을
canonical raw를 대체하는 것으로 취급하지 않는다.

lineage:

raw hash
→ parser version
→ feature code
→ derived dataset hash

를 추적할 수 있게 한다.

==================================================
15. IAM / SECRET MANAGEMENT
==================================================

AWS 서버에 장기 credential 파일을 무분별하게 저장하지 않는다.

AWS resource access에는
EC2 IAM Role을 사용한다.

AWS access key를 `.env`에 저장하지 않는다.

Bithumb 등 외부 exchange secret은:

SSM Parameter Store SecureString

또는

AWS Secrets Manager

를 사용한다.

어느 것이 비용/기능 측면에서 적절한지 비교하라.

secret 값은:

- terminal output
- logs
- Git
- CloudWatch
- reports

에 출력하지 않는다.

==================================================
16. NETWORK SECURITY
==================================================

가능하면 EC2는 inbound SSH 22를 public internet에 노출하지 않는다.

관리:

SSM Session Manager

우선.

Security Group은 최소 권한.

collector가 public market WebSocket을 이용하므로
필요한 outbound connectivity는 허용한다.

trading API 역시 필요한 outbound만 사용한다.

==================================================
17. SYSTEMD SERVICES
==================================================

collector와 향후 trading engine은
터미널 세션에 종속되지 않게 한다.

systemd unit으로 관리한다.

예:

v9-collector.service

paper-trader.service

archive-uploader.service

health-monitor.service

그러나 잘못된 자동 restart loop는 피한다.

collector crash의 종류를 구분한다.

예:

transient network disconnect
→ application-level reconnect

writer corruption/disk failure
→ FAIL CLOSED

invalid config
→ FAIL CLOSED

unknown account state
→ trading FAIL CLOSED

==================================================
18. GRACEFUL SHUTDOWN
==================================================

EC2 reboot/stop/deploy 시:

SIGTERM

을 처리하고:

queue drain
partition close
manifest
metrics flush

후 종료하도록 한다.

termination/reboot test도 수행한다.

kill -9에 의존하지 않는다.

==================================================
19. NTP / CLOCK
==================================================

V9 cross-market 연구에서 매우 중요하다.

AWS host clock 상태를 명시적으로 기록한다.

확인:

chrony / systemd-timesyncd / Amazon Time Sync Service

등 실제 환경.

가능하면 clock offset diagnostics를 지속적으로 기록한다.

raw에는 계속:

exchange timestamp
local wall receive timestamp
local monotonic receive timestamp

를 보존한다.

monotonic timestamp는
process/run boundary를 넘어 직접 비교하지 않는다.

run_id를 함께 사용한다.

==================================================
20. CLOUDWATCH
==================================================

최소 다음 상태를 관측 가능하게 한다.

EC2:
- CPU
- memory
- disk
- inode if relevant

collector:
- process alive
- last append age
- events received
- bytes written
- queue depth
- queue dropped
- reconnects
- writer failures
- malformed/quarantine
- exchange/stream heartbeat

archive:
- finalized partitions pending upload
- successful uploads
- failed uploads
- age of oldest pending artifact

CloudWatch에 secret/raw payload를 보내지 않는다.

==================================================
21. ALERTS
==================================================

다음과 같은 condition에 알림을 고려한다.

collector not running
append stale
disk high
queue drop > 0
writer failure
S3 upload backlog
repeated reconnect storm
paper/live reconciliation failure

기존 Discord notification infrastructure가 있다면
secret-safe하게 활용할 수 있다.

==================================================
22. AWS BUDGET
==================================================

현재 credit은 2026-12-13 만료 예정이므로
비용 모니터링을 반드시 설정한다.

AWS Budget / Cost Anomaly Detection을 검토한다.

예:

monthly actual/forecast:
$10
$20
$30
$40

등의 단계별 alert.

정확한 threshold는
예상 architecture 월 비용을 계산한 뒤 결정한다.

현재 남은 credit이 약 $114라는 이유로
무제한 자원을 만들지 않는다.

==================================================
23. INFRASTRUCTURE AS CODE
==================================================

가능하면 AWS resource는
수동 콘솔 클릭만으로 만들지 않는다.

Terraform
또는
프로젝트에서 더 적합한 IaC

를 사용한다.

최소:

EC2
IAM role/policy
Security Group
S3
EBS configuration
CloudWatch
Budget-related config where feasible

를 재현 가능하게 관리한다.

단 secrets 자체는 IaC state에 평문으로 넣지 않는다.

==================================================
24. AWS DEPLOYMENT REPOSITORY SAFETY
==================================================

다음은 Git에 넣지 않는다.

.env
.env.local
AWS secret
Bithumb secret
raw dataset
private state
account balances
personal holdings

IaC state 역시 secret이 포함될 가능성이 있으므로
local plaintext state를 공개 repository에 commit하지 않는다.

`.gitignore`를 확인한다.

==================================================
25. AWS MIGRATION VALIDATION
==================================================

AWS deployment 직후
장기 수집으로 바로 넘어가지 않는다.

먼저:

AWS V9.2 smoke test

를 수행한다.

확인:

3 exchanges connected
all expected markets subscribed
Binance market attribution correct
timestamps present
monotonic clock present
run ID present
writer healthy
manifest healthy
compression healthy
S3 upload healthy
graceful shutdown healthy
systemd restart behavior
CloudWatch metrics

PASS 후에만 다음 단계.

==================================================
26. AWS NEW 72-HOUR SOAK
==================================================

AWS environment는 새 epoch이므로
다시 clean 48~72h infrastructure soak을 한다.

가능하면:

72h

을 기준으로 한다.

이 기간에는:

alpha mining 금지
live trading 금지

AWS soak에서는 V9에서 발견된 문제가
재발하지 않는지를 특히 확인한다.

- UNKNOWN market
- timestamp missing
- local timestamp reversal interpretation
- reconnect visibility
- queue/drop visibility
- writer failure
- manifest stale
- disk calculation
- sampler imbalance

==================================================
27. MAC VS AWS 비교
==================================================

AWS soak 종료 후
Mac epoch와 AWS epoch를 비교한다.

단 raw alpha performance 비교가 아니라
infrastructure characteristics를 비교한다.

예:

exchange × stream:

receive clock difference distribution
reconnect frequency
append latency
writer latency
CPU
memory
disk throughput
network consistency

특히:

Bithumb
Binance
Upbit

별로 비교한다.

AWS가 반드시 더 빠를 것이라고 가정하지 않는다.

측정값으로 결정한다.

==================================================
28. LONG-TERM PROSPECTIVE COLLECTION
==================================================

AWS 72h soak PASS 후:

V9.x prospective dataset을
AWS에서 계속 수집한다.

연구 목표:

최소 2~4주
가능하면 8~12주

의 regime-diverse microstructure dataset.

MacBook을 닫아도 AWS collector는 계속 실행되어야 한다.

==================================================
29. PAPER TRADING
==================================================

AWS에서 collector가 안정화되면
paper trading engine을 24/7 구동할 수 있다.

단:

paper trading과 alpha discovery를 혼동하지 않는다.

전략 후보가 사전등록/검증을 통과했을 때만
paper engine에 연결한다.

paper 단계에서 다음을 측정한다.

signal count
decision latency
simulated fill
expected price
real executable book
slippage
fees
missed opportunities
reconciliation
uptime

==================================================
30. LIVE TRADING 조건
==================================================

AWS에 올라갔다고 live trading을 켜지 않는다.

다음 조건 전부를 통과하기 전:

live_trading_ready=false

유지.

예:

- infrastructure audit PASS
- prospective data 충분
- preregistered strategy PASS
- sealed holdout PASS
- cost stress PASS
- paper trading stable
- reconciliation stable
- risk controls PASS
- manual review

live activation은 별도의 명시적 사용자 승인 없이는 수행하지 않는다.

==================================================
31. AWS CREDITS와 RESEARCH COMPUTE
==================================================

평상시 24/7 node는 작은 instance를 사용한다.

대규모:

FULL-SCAN
Parquet generation
feature materialization
backtest
bootstrap
WRC/PBO/DSR
stress testing

등에는 필요 시 일시적으로 더 큰 EC2 instance를 사용할 수 있다.

작업이 끝나면 즉시 종료한다.

이 방식으로 남은 AWS credit을
실제 연구 계산에 효율적으로 활용한다.

==================================================
32. S3 LIFECYCLE
==================================================

장기 storage 비용을 줄이기 위해
S3 Lifecycle을 검토한다.

그러나 활발하게 연구 중인 dataset을
너무 빨리 Glacier 등 retrieval latency가 큰 storage class로 보내지 않는다.

연구 단계에 맞게:

hot
warm
archive

정책을 설계한다.

모든 정책은 예상 비용과 retrieval requirements를 함께 제시한다.

==================================================
33. DISASTER RECOVERY
==================================================

EC2 instance 자체가 source of truth가 되면 안 된다.

다음은 외부에 보존돼야 한다.

source:
Git

raw:
S3

secrets:
SSM / Secrets Manager

infrastructure:
IaC

audit/research metadata:
appropriate durable storage

따라서 EC2가 완전히 삭제돼도
새 instance를 만들어 복구 가능해야 한다.

==================================================
34. DEPLOYMENT DOCUMENTATION
==================================================

최종적으로 다음과 같은 문서를 작성한다.

docs/AWS_V9_MIGRATION_PLAN.md

docs/AWS_V9_DEPLOYMENT_RUNBOOK.md

docs/AWS_V9_SECURITY_MODEL.md

docs/AWS_V9_COST_MODEL.md

docs/AWS_V9_72H_AUDIT.md

필요하면 이름은 현재 repository naming convention에 맞춘다.

==================================================
35. MACHINE-READABLE DEPLOYMENT STATE
==================================================

secret을 제외한 deployment metadata를
machine-readable 형태로 남긴다.

예:

deployment_epoch
region
instance type
AMI
git commit
collector hash
schema
S3 bucket alias/non-sensitive identifier
process start
clock configuration
AWS soak boundary

개인 계정 ID/secret이 public artifact에
불필요하게 노출되지 않도록 한다.

==================================================
36. EXECUTION ORDER
==================================================

현재 작업이 끝난 뒤 다음 순서를 반드시 따른다.

PHASE A
현재 V9 PID 30933 72h 완주

PHASE B
graceful shutdown + artifact freeze

PHASE C
V9 FULL-SCAN final audit

PHASE D
V9.1 bug fixes 확정

PHASE E
V9.1 전체 tests / infra audit

PHASE F
local V9.1 smoke/short soak

PHASE G
AWS architecture/cost/security plan

PHASE H
Terraform/IaC 구현

PHASE I
AWS infrastructure provisioning

PHASE J
V9.x AWS deployment

PHASE K
AWS smoke test

PHASE L
AWS clean 72h infrastructure soak

PHASE M
AWS final infrastructure audit

PHASE N
long-term prospective collection

PHASE O
validated strategy paper trading

PHASE P
별도 명시적 승인 후에만 live trading 검토

순서를 생략하지 않는다.

==================================================
37. CURRENT TASK PRIORITY
==================================================

지금 이 지시를 읽은 시점에
PID 30933이 아직 72시간 미만이라면:

AWS migration을 지금 시작하지 마라.

현재 collector도 건드리지 마라.

대신 이 AWS directive를
NEXT PHASE PLAN으로 보존한다.

현재 72h final audit과 V9.1 stabilization이 완료되면
이 문서를 다시 읽고 PHASE G 이전 prerequisite를 검증한 후 진행한다.

==================================================
38. AUTONOMOUS CONTINUATION
==================================================

현재 V9 72h + V9.1 stabilization이 끝났고
명백한 blocker가 없다면
사용자에게 매 작은 단계마다 허락을 요구하지 말고
위 순서대로 안전한 범위에서 계속 진행한다.

다만 다음은 반드시 사용자 확인을 요구한다.

- live trading 활성화
- 실제 거래 권한 확대
- irreversible destructive operation
- 예상보다 큰 AWS 비용 발생 가능성이 있는 architecture
- credential rotation이 필요한 상황

AWS에서 소액 과금 resource를 생성하는 단계는
현재 사용자가 AWS migration을 명시적으로 요청한 것으로 간주할 수 있으나,
실제 예상 월 비용을 provisioning 직전에 먼저 계산·보고한다.

==================================================
39. 가장 중요한 원칙
==================================================

AWS migration의 목적은
단순히 “노트북 대신 서버에서 프로그램을 실행하는 것”이 아니다.

목표는:

- 24/7 availability
- reproducible infrastructure
- stable prospective collection
- causal timestamps
- durable raw archive
- disaster recovery
- cost observability
- safe paper/live execution

을 갖춘 연구/거래 infrastructure를 만드는 것이다.

복잡한 architecture 자체가 목표가 아니다.

현재 workload에 충분한
가장 단순하고 안전하고 재현 가능한 구조를 선택한다.

==================================================
40. FINAL DIRECTIVE
==================================================

현재 V9 72h 작업을 그대로 계속 수행하라.

현재 collector에 영향을 주지 않는다.

72h 종료 후:

final audit
→ V9.1 안정화
→ local validation

까지 완료한다.

그 이후 이 AWS migration directive를 실행하여:

AWS Seoul 기반의 새로운 V9.x epoch를 구축하고,
다시 clean infrastructure soak을 수행한 뒤,
장기 prospective collection 및 paper trading 단계로 진행하라.

어떤 단계에서도 검증보다 빠른 live trading을 허용하지 않는다.