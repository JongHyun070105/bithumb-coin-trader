import React from 'react'
import { MetricCard } from '../components/MetricCard'
import { StatusBadge } from '../components/StatusBadge'
import { ModeBanner } from '../components/ModeBanner'
import { History, XCircle, Sparkles, Scale } from 'lucide-react'

export const ResearchLab: React.FC = () => {
  return (
    <div className="page-container">
      <ModeBanner />

      <div className="page-header">
        <div>
          <h2>정량 연구실 및 가설 거버넌스 (Research Lab)</h2>
          <p className="page-subtitle">
            사전등록된 마이크로스트럭처 알파 연구(CYCLE-01), 과거 전략 베이스라인 및 다중 검정 통계 거버넌스
          </p>
        </div>
      </div>

      {/* Section 1: Prospective Microstructure (P11.3) */}
      <section className="section-block">
        <h3 className="section-title">
          <Sparkles size={18} />
          <span>전향적 마이크로스트럭처 연구 (Prospective Microstructure CYCLE-01)</span>
        </h3>

        <div className="card-surface prospective-card">
          <div className="prospective-header">
            <div>
              <strong>사전등록 번호: <code>prereg-microstructure-20260905-v1</code></strong>
              <span className="badge-frozen">FROZEN BEFORE DATA INSPECTION</span>
            </div>
            <StatusBadge status="PENDING" label="WAITING FOR QUALIFIED DATASET" />
          </div>

          <p className="prospective-desc">
            72시간 수집 데이터셋이 DQ_PASS로 정식 적격 승인된 이후에만 Discovery 24시간 표본에서 탐색을 시작합니다.
            데이터 열람 전 가설, 목적함수, 피처 패밀리 명세가 동결되었습니다.
          </p>

          <div className="feature-families-grid">
            <div className="feature-card">
              <span className="feat-code">OFI (Order Flow Imbalance)</span>
              <span className="feat-title">주문 흐름 불균형</span>
              <p className="feat-desc">호가창 깊이별 매수/매도 주문 유입량 차이 측정</p>
              <div className="feat-status"><StatusBadge status="NOT_STARTED" label="DEFINED / NOT RUN" size="sm" /></div>
            </div>

            <div className="feature-card">
              <span className="feat-code">ATI (Aggressor Trade Imbalance)</span>
              <span className="feat-title">체결 강도 불균형</span>
              <p className="feat-desc">시장가 체결(Taker buy/sell) 거래량 비대칭성 측정</p>
              <div className="feat-status"><StatusBadge status="NOT_STARTED" label="DEFINED / NOT RUN" size="sm" /></div>
            </div>

            <div className="feature-card">
              <span className="feat-code">MPQI (Micro-Price Queue Imbalance)</span>
              <span className="feat-title">마이크로 가격 큐 불균형</span>
              <p className="feat-desc">최우선 호가 잔량 가중 마이크로 프라이스 모멘텀</p>
              <div className="feat-status"><StatusBadge status="NOT_STARTED" label="DEFINED / NOT RUN" size="sm" /></div>
            </div>
          </div>
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
