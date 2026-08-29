# AWS cost estimate — Seoul, 2026-08-29

## 증거와 한계

이 문서는 2026-08-29 공식 AWS public price evidence와 V9 measured ingestion을 사용한 **세전 정가 추정**이다. AWS 인증은 `InvalidClientTokenId`이므로 현재 credit 잔액, 만료일, eligible service, MTD 사용액은 **NOT VERIFIED**다. credit 적용 후 비용을 0 또는 과거 잔액으로 가정하지 않는다.

공식 근거:

- EC2 Seoul Price List: <https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/20260829014319/ap-northeast-2/index.json>
- EBS gp3: <https://docs.aws.amazon.com/ebs/latest/userguide/general-purpose.html>
- S3: <https://aws.amazon.com/s3/pricing/>
- CloudWatch: <https://aws.amazon.com/cloudwatch/pricing/>
- Public IPv4: <https://aws.amazon.com/vpc/pricing/>
- Secrets Manager: <https://aws.amazon.com/secrets-manager/pricing/>
- AWS Budgets: <https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/>

## 입력 가정

- V9 measured raw: 73.95 GiB / 약 72시간, 계획값 24 GiB/day.
- 30일 raw: 720 GiB = 약 773.1 decimal GB.
- 보수적 계획 압축률: 10:1, 즉 모든 finalized raw를 보관할 경우 77.31 GB/month 신규 compressed data.
- Mac read-only 표본 관측: compressed/original 3.49%–4.20%, 즉 약 23.8:1–28.7:1.
- 표본 관측은 AWS/Linux 성능이나 미래 전체 데이터 분포를 보장하지 않는다.
- S3 object PUT은 V9 partition 증가율을 월 환산하고 data+manifest를 각각 object로 계산해 약 107,800회/month로 둔다.
- S3는 selective canonical/research archive이며 모든 raw의 영구 data lake가 아니다.
- canonical transition과 temporary expiration은 모두 기본 비활성이다.
- internet egress, tax, snapshot, cross-AZ, restore/retrieval은 포함하지 않는다.

## compute와 EBS

| 항목 | 단가 | 월 정가 |
|---|---:|---:|
| t3.small | US$0.0260/hour | US$18.98 |
| t3.medium | US$0.0520/hour | US$37.96 |
| t4g.small | US$0.0208/hour | US$15.18 |
| t4g.medium | US$0.0416/hour | US$30.37 |
| gp3 80 GiB minimum | US$0.0912/GB-month | US$7.30 |
| gp3 100 GiB recommended | US$0.0912/GB-month | US$9.12 |
| gp3 120 GiB headroom | US$0.0912/GB-month | US$10.94 |
| gp3 150 GiB comparison | US$0.0912/GB-month | US$13.68 |

gp3 기본 3,000 IOPS/125 MiB/s 이상은 benchmark 없이 구매하지 않는다.

100 GiB는 5일치 120 GiB uncompressed raw 보관 용량이 아니다. OS/application, active partition, compressed artifact, decompression verification staging을 포함한 hot buffer이며 finalized raw는 검증된 compression/retention pipeline으로 순환한다.

## S3 retention 시나리오

### A — 연구 dataset을 계속 보존

모든 월 compressed data를 연구용으로 계속 승격·보존하는 상한에 가까운 모델이다. lifecycle이 없으면 선형 누적된다.

| 시점 | 보관량 | 해당 시점 Standard 월 run-rate | 기간 내 storage charge 근사 |
|---|---:|---:|---:|
| 1개월 평균 | 38.65 GB | 해당 없음 | US$0.97 |
| 1개월 말 | 77.31 GB | US$1.93/month | 첫 달 storage charge는 위 평균값 |
| 3개월 말 | 231.93 GB | US$5.80/month | 3개월 누적 storage charge 약 US$8.70 |

여기에 PUT 약 US$0.49/month가 추가된다. 첫 달 총 S3 추정은 storage 평균 US$0.97 + PUT US$0.49 = 약 US$1.46이다. 3개월 동안 매월 같은 양이 누적되고 lifecycle이 없다면 storage charge는 대략 US$0.97 + US$2.90 + US$4.83 = US$8.70이며 PUT 3개월 약 US$1.46이 별도다.

### B — temporary raw를 정리하고 핵심 dataset만 보존

정확한 정책은 alpha protocol 때 고정해야 한다. 비교를 위해 다음 **illustrative assumption**을 사용한다.

- compressed temporary data는 최근 7일 rolling window만 유지
- 월 compressed data의 20%를 canonical research/reproduction evidence로 승격
- 보수적 10:1 compression
- deletion automation은 아직 비활성; 이 값은 검토할 target model이지 현재 실행 정책이 아님

| 시점 | canonical 누적 | temporary rolling | 총 보관량 | Standard storage 비용 |
|---|---:|---:|---:|---:|
| 1개월 평균 | 약 7.73 GB | 약 15.94 GB | 약 23.67 GB | 약 US$0.59 |
| 1개월 말 | 15.46 GB | 18.04 GB | 33.50 GB | run-rate US$0.84/month |
| 3개월 말 | 46.39 GB | 18.04 GB | 64.43 GB | run-rate US$1.61/month |

이 모델의 3개월 storage charge 근사는 약 US$3.04이고 PUT은 별도 약 US$1.46이다. canonical selection 비율과 temporary retention 일수가 달라지면 다시 계산한다.

### Mac 표본 압축률 범위

| compressed/original | 월 신규 보관량 | 1개월 평균 charge | 1개월 말 run-rate | 3개월 말 보관량/run-rate |
|---:|---:|---:|---:|---:|
| 4.20% | 32.47 GB | US$0.41 | US$0.81 | 97.40 GB / US$2.44 |
| 3.49% | 26.98 GB | US$0.34 | US$0.67 | 80.95 GB / US$2.02 |

이 범위는 local 표본 결과이며 예산 기준은 더 보수적인 10:1을 유지한다. 초기 candidate는 level 1이다. 3.72%와 높은 throughput이 CPU 부담 대비 적절했지만 Amazon Linux에서 CPU/RSS/throughput/decompression SHA를 다시 측정한다. lifecycle을 활성화하면 transition, 최소 보관 기간, retrieval 비용을 포함해 다시 계산해야 한다.

## 운영 비용 가정

| 항목 | 가정 | 월 정가 |
|---|---|---:|
| Public IPv4 | 1 × 730h × US$0.005 | US$3.65 |
| CloudWatch | 12 metric, 10 alarm, logs ingest/storage 각 1 GB, free tier 미적용 | US$5.39 |
| Secrets Manager | public collector에는 생성 안 함 | US$0.00 |
| optional secret | private credential 필요 시 1개 + 소량 API | 약 US$0.40 |
| Budgets | monitoring 또는 최초 action-enabled budget 2개 범위 | US$0.00 |

CloudWatch 추정은 raw event logging을 포함하지 않는다. raw logging을 켜면 월 수백 GB ingest 비용이 발생할 수 있으므로 금지한다.

## 월 시나리오 — 첫 달 실제 누적 기준

아래 S3 값은 첫 달 평균 storage US$0.97 + PUT US$0.49를 사용한다. optional secret은 제외한다.

| 구성 | EC2 | EBS | IPv4 | CloudWatch | 첫 달 S3 | 합계 |
|---|---:|---:|---:|---:|---:|---:|
| minimum after smoke: t4g.small + 80 GiB | 15.18 | 7.30 | 3.65 | 5.39 | 1.46 | **US$32.98** |
| ARM candidate: t4g.medium + 100 GiB | 30.37 | 9.12 | 3.65 | 5.39 | 1.46 | **US$49.99** |
| **recommended safe default: t3.medium + 100 GiB** | 37.96 | 9.12 | 3.65 | 5.39 | 1.46 | **US$57.58** |
| x86 headroom: t3.medium + 120 GiB | 37.96 | 10.94 | 3.65 | 5.39 | 1.46 | **US$59.40** |

표는 scenario A의 첫 달 평균 storage를 사용한 정가 추정이며 credit/tax/egress는 제외한다. scenario B의 first-month S3는 storage US$0.59 + PUT US$0.49 = 약 US$1.08이므로 recommended 합계는 약 **US$57.20**다.

scenario A에서는 둘째 달부터 S3 누적분 때문에 합계가 계속 증가한다. 1개월 말 run-rate로 표현할 때 표의 합계에 약 US$0.97을 더하고, 3개월 말에는 약 US$4.83을 더한다. scenario B에서는 3개월 말 storage run-rate가 약 US$1.61로 제한되는 비교 모델이지만, retention automation은 아직 꺼져 있다.

## provisioning 직전 필수 재검증

- Billing Credits page의 remaining/estimated remaining/expiration/applicable products
- current-month actual, forecast, tax
- 서울 EC2/EBS/S3/CloudWatch/IP 최신 정가
- architecture 최종 선택
- S3 lifecycle/restore 요구사항
- budget notification destination과 threshold
