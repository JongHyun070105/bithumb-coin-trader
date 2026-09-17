import React from 'react'
import { MetricCard } from '../components/MetricCard'
import { StatusBadge } from '../components/StatusBadge'
import { ModeBanner } from '../components/ModeBanner'
import { History, XCircle, Sparkles, Scale, TrendingDown } from 'lucide-react'
import { V2_RESEARCH_STATUS, V4_VALIDATION_STATUS } from '../fixtures/syntheticDemoData'

export const ResearchLab: React.FC = () => {
  return (
    <div className="page-container">
      <ModeBanner />

      <div className="page-header">
        <div>
          <h2>정량 연구실 및 가설 거버넌스 (Research Lab)</h2>
          <p className="page-subtitle">
            V2 30시간 마이크로스트럭처 알파 연구 결과 및 V4 검증 상태
          </p>
        </div>
      </div>

      {/* V2 Authoritative Research Results */}
      <section className="section-block">
        <h3 className="section-title">
          <TrendingDown size={18} />
          <span>V2 권위적 30시간 연구 결과 (Authoritative V2 30H Study)</span>
        </h3>

        <div className="card-surface prospective-card">
          <div className="prospective-header">
            <div>
              <strong>데이터셋: <code>{V2_RESEARCH_STATUS.dataset}</code></strong>
              <span className="badge-frozen">DEVELOPMENT / EXPLORATORY</span>
            </div>
            <StatusBadge
              status={V2_RESEARCH_STATUS.status === 'COMPLETE' ? 'COMPLETE' : 'PENDING'}
              label={V2_RESEARCH_STATUS.classification}
            />
          </div>

          <p className="prospective-desc">
            2,272 DATA_PRESENT 슬롯, 8 UNKNOWN_MISSING. 풀해상도 BTC/ETH/XRP 실행 완료.
            실시간 퓨처북 실행. 48가지 시나리오 모두 음수.
          </p>

          <div className="status-grid">
            <MetricCard
              title="SOURCE OBJECTS"
              value={V2_RESEARCH_STATUS.sourceObjects.toLocaleString()}
              subtext={`${(V2_RESEARCH_STATUS.sourceBytes / 1024 / 1024).toFixed(1)} MB compressed`}
              evidenceSource="MEASURED"
            />
            <MetricCard
              title="H1-H3 FULL-RES"
              value={V2_RESEARCH_STATUS.h1h3FullResolution}
              subtext="BTC, ETH, XRP — real future-book execution"
              status="success"
              evidenceSource="MEASURED"
            />
            <MetricCard
              title="BEST TAKER"
              value={`${V2_RESEARCH_STATUS.bestTakerBps} bps`}
              subtext={`${V2_RESEARCH_STATUS.bestTakerMarket} ${V2_RESEARCH_STATUS.bestTakerLatency} ${V2_RESEARCH_STATUS.bestTakerFee}`}
              status="warning"
              evidenceSource="MEASURED"
            />
            <MetricCard
              title="VALIDATION"
              value={V2_RESEARCH_STATUS.validationEntered ? 'ENTERED' : 'NOT ENTERED'}
              subtext={V2_RESEARCH_STATUS.validationEntered ? 'Results available' : 'No DEV candidate survived execution'}
              status={V2_RESEARCH_STATUS.validationEntered ? 'success' : 'default'}
              evidenceSource="DECLARED"
            />
          </div>

          <div className="feature-families-grid">
            <div className="feature-card">
              <span className="feat-code">H1: Orderbook Imbalance</span>
              <span className="feat-title">호가창 불균형</span>
              <p className="feat-desc">
                XRP IC=0.33, ETH IC=0.29, BTC IC=0.17 @ 30s. 실행: COST_KILLED (-1.9 ~ -5.9 bps)
              </p>
              <div className="feat-status">
                <StatusBadge status="COMPLETE" label="COST_KILLED" size="sm" />
              </div>
            </div>

            <div className="feature-card">
              <span className="feat-code">H2: ATI / Trade Flow</span>
              <span className="feat-title">체결 강도 불균형</span>
              <p className="feat-desc">
                IC 0.09-0.24. 가장 강한 예측 신호. 실행 미완성 (무역 이벤트 기반).
              </p>
              <div className="feat-status">
                <StatusBadge status="COMPLETE" label="PREDICTIVE_ONLY" size="sm" />
              </div>
            </div>

            <div className="feature-card">
              <span className="feat-code">H3: Microprice</span>
              <span className="feat-title">마이크로 가격</span>
              <p className="feat-desc">
                XRP IC=0.27, ETH IC=0.28 @ 30s. 실행: COST_KILLED (-3.9 ~ -8.8 bps)
              </p>
              <div className="feat-status">
                <StatusBadge status="COMPLETE" label="COST_KILLED" size="sm" />
              </div>
            </div>

            <div className="feature-card">
              <span className="feat-code">H4/H5: Cross-Exchange</span>
              <span className="feat-title">크로스 거래소</span>
              <p className="feat-desc">
                Upbit 기준선 완료. Binance 오더북 미완성 (null exchange_ts).
              </p>
              <div className="feat-status">
                <StatusBadge status="PARTIAL" label="INCOMPLETE" size="sm" />
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* V4 Status */}
      <section className="section-block">
        <h3 className="section-title">
          <Sparkles size={18} />
          <span>V4 검증 상태 (V4 Validation Status)</span>
        </h3>

        <div className="status-grid">
          <MetricCard
            title="V4 PROCESS"
            value="NOT_VERIFIABLE"
            subtext="SSM 미인가로 직접 프로세스 미검증 (S3 전송 중단 확인)"
            status="warning"
            evidenceSource="NOT AVAILABLE"
          />
          <MetricCard
            title="V4 S3 COVERAGE"
            value={`${V4_VALIDATION_STATUS.s3CoverageHoursVisible} hour visible`}
            subtext={`Hour: ${V4_VALIDATION_STATUS.s3CoverageHour}. Raw data: ${V4_VALIDATION_STATUS.s3RawDataObjects} objects`}
            status="warning"
            evidenceSource="MEASURED"
          />
          <MetricCard
            title="V4 FINAL VERDICT"
            value={V4_VALIDATION_STATUS.finalVerdict}
            subtext={V4_VALIDATION_STATUS.note}
            status="danger"
            evidenceSource="MEASURED"
          />
        </div>
      </section>

      {/* Original preregistration (historical) */}
      <section className="section-block">
        <h3 className="section-title">
          <History size={18} />
          <span>사전등록 이력 (Historical Preregistration)</span>
        </h3>
        <div className="card-surface prospective-card">
          <div className="prospective-header">
            <div>
              <strong>사전등록 번호: <code>prereg-microstructure-20260905-v1</code></strong>
              <span className="badge-frozen">FROZEN BEFORE DATA INSPECTION</span>
            </div>
            <StatusBadge status="COMPLETE" label="SUPERSEDED BY V2" />
          </div>
          <p className="prospective-desc">
            72시간 수집 기반 사전등록. V2 30시간 독립 데이터셋으로 실행 완료.
          </p>
        </div>
      </section>

      {/* Section 2: Statistical Governance (P11.4) */}
      <section className="section-block">
        <h3 className="section-title">
          <Scale size={18} />
          <span>통계적 거버넌스 및 다중 검정 패널티 (Statistical Governance)</span>
        </h3>

        <div className="status-grid">
          <MetricCard
            title="DEFLATED SHARPE (DSR)"
            value="RESOLVED (HELPER)"
            subtext="테스트 계산 결함 수리 완료 (원계열 재현: INCONCLUSIVE)"
            evidenceSource="DERIVED"
          />
          <MetricCard
            title="WHITE REALITY CHECK (WRC)"
            value="SLOT READY"
            subtext="데이터 스누핑 패널티 부트스트랩 프레임워크"
            evidenceSource="DECLARED"
          />
          <MetricCard
            title="PROB. OVERFITTING (PBO)"
            value="SLOT READY"
            subtext="CSCV 기반 백테스트 과적합 확률 측정"
            evidenceSource="DECLARED"
          />
          <MetricCard
            title="TRIAL LEDGER"
            value="FROZEN LEDGER"
            subtext="모든 탐색 시험은 SHA-256 원장에 영구 기록"
            evidenceSource="SEALED"
            status="success"
          />
        </div>

        <div className="card-surface dsr-clarification">
          <strong>※ DSR 및 백테스트 재현성에 관한 과학적 명세:</strong>
          <p className="muted-text">
            DSR 도우미 함수의 단위/연율화 불일치는 수리되었으나, 과거 61.47% 보고 수치는 입력 원시 시계열 부재로 인해 <code>INCONCLUSIVE_INPUT_EVIDENCE</code> 상태로 분류됩니다.
            따라서 검증되지 않은 <code>DSR VERIFIED</code> 표기는 엄격히 금지됩니다.
          </p>
        </div>
      </section>

      {/* Section 3: Historical Baselines (P11.1) */}
      <section className="section-block">
        <h3 className="section-title">
          <History size={18} />
          <span>과거 연구 베이스라인 (Historical Baselines — V4 / V6)</span>
        </h3>

        <div className="card-surface">
          <div className="baseline-warning-badge">
            <span>HISTORICAL RESEARCH · ALPHA UNPROVEN · NOT LIVE VALIDATION</span>
          </div>

          <div className="baseline-table-wrapper">
            <table className="prov-table">
              <thead>
                <tr>
                  <th>베이스라인 모델</th>
                  <th>연구 표본 기간</th>
                  <th>샤프 지수 (SR)</th>
                  <th>최대 낙폭 (MDD)</th>
                  <th>과학적 판정</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td><strong>Strategy V4</strong></td>
                  <td>2024년 과거 샘플</td>
                  <td>1.42 (연율)</td>
                  <td>-8.4%</td>
                  <td><span className="badge-neutral">HISTORICAL ONLY (ALPHA UNPROVEN)</span></td>
                </tr>
                <tr>
                  <td><strong>Strategy V6</strong></td>
                  <td>2024년 과거 샘플</td>
                  <td>1.85 (연율)</td>
                  <td>-6.1%</td>
                  <td><span className="badge-neutral">HISTORICAL ONLY (ALPHA UNPROVEN)</span></td>
                </tr>
                <tr>
                  <td><strong>V4 / V6 70:30 Ensemble</strong></td>
                  <td>과거 결합 기준선</td>
                  <td>1.91 (연율)</td>
                  <td>-5.2%</td>
                  <td><span className="badge-neutral">HISTORICAL REFERENCE</span></td>
                </tr>
              </tbody>
            </table>
          </div>
          <small className="muted-text">
            * 위 수치는 과거 오프라인 연구 기록이며 미래 수익을 보장하지 않습니다. 실데이터 소크 완료 전까지 알파는 증명되지 않았습니다.
          </small>
        </div>
      </section>

      {/* Section 4: Rejected Experiments (P11.2) */}
      <section className="section-block">
        <h3 className="section-title danger-text">
          <XCircle size={18} />
          <span>거절된 실험 원장 (Rejected Experiments — V8 / V8.1)</span>
        </h3>

        <div className="rejected-grid">
          <div className="card-surface rejected-card">
            <div className="rej-header">
              <strong>Strategy V8</strong>
              <span className="badge-danger">REJECTED (거절됨)</span>
            </div>
            <p className="rej-reason">
              <strong>거절 사유:</strong> 멀티버스 파라미터 과최적화 및 슬리피지/수수료 모델 누락으로 인한 가짜 초과수익 편향 확인.
            </p>
          </div>

          <div className="card-surface rejected-card">
            <div className="rej-header">
              <strong>Strategy V8.1</strong>
              <span className="badge-danger">REJECTED (거절됨)</span>
            </div>
            <p className="rej-reason">
              <strong>거절 사유:</strong> 강건성 검정(Robustness Audit) 중 체결 지연(Latency) 노이즈 주입 시 샤프 지수 급격한 붕괴 확인.
            </p>
          </div>
        </div>
      </section>
    </div>
  )
}
