import { Lock, CircleDot } from 'lucide-react'
import { ModeBanner } from '../components/ModeBanner'

export const Trading: React.FC = () => {
  return (
    <div className="page-container">
      <ModeBanner />

      <div className="page-header">
        <div>
          <h2>페이퍼 및 실거래 통제 (Trading & Execution Console)</h2>
          <p className="page-subtitle">
            모의 거래(Paper Trading) 및 실거래 주문 파이프라인의 엄격한 잠금 및 진입 조건
          </p>
        </div>
      </div>

      <section className="section-block">
        <div className="trading-locked-hero card-surface">
          <div className="locked-icon-wrapper">
            <Lock size={48} className="icon-locked-large" />
          </div>

          <h3>트레이딩 실행 계층 완전 잠금 (Execution Layer Strictly Locked)</h3>
          <p className="locked-lead">
            본 콘솔은 데이터 품질 통과 및 가설 검증이 완료되기 전까지 주문 발주 기능(BUY / SELL), 계정 연결, 실거래 언락 컨트롤을 제공하지 않습니다.
          </p>

          <div className="trading-status-pills">
            <span className="status-pill pill-paper">PAPER TRADING: NOT STARTED</span>
            <span className="status-pill pill-live">LIVE TRADING: DISABLED</span>
          </div>

          <div className="prereq-container">
            <h4>실행 계층 진입을 위한 5대 선행 필수 조건 (Prerequisites)</h4>
            <div className="prereq-list">
              <div className="prereq-item pending">
                <CircleDot size={16} className="icon-pending" />
                <div>
                  <strong>1. 공식 72H 데이터 품질 감사 통과 (Real DQ Pass)</strong>
                  <p className="muted-text">하드 블로커 0건, 단조 시계 역전 0건, 76개 피드 완전성 입증 (현재: 미실행)</p>
                </div>
              </div>

              <div className="prereq-item pending">
                <CircleDot size={16} className="icon-pending" />
                <div>
                  <strong>2. 적격 연구 데이터셋 생성 (Qualified Dataset Creation)</strong>
                  <p className="muted-text">캐노니컬 나노초 정렬 및 10종 출처 메타데이터 암호학적 봉인</p>
                </div>
              </div>

              <div className="prereq-item pending">
                <CircleDot size={16} className="icon-pending" />
                <div>
                  <strong>3. 사전등록된 연구 가설 검증 (Research Validation)</strong>
                  <p className="muted-text">Discovery 24h 탐색 및 Validation 24h에서 DSR/PBO/WRC 다중 가설 패널티 통과</p>
                </div>
              </div>

              <div className="prereq-item pending">
                <CircleDot size={16} className="icon-pending" />
                <div>
                  <strong>4. 독립적인 홀드아웃 개봉 의사결정 (Holdout Decision)</strong>
                  <p className="muted-text">단 1회의 사전등록 검정(One-shot test)으로만 봉인 해제 허용</p>
                </div>
              </div>

              <div className="prereq-item pending">
                <CircleDot size={16} className="icon-pending" />
                <div>
                  <strong>5. 별도 모의 거래 정식 승인 (Paper Trading Authorization)</strong>
                  <p className="muted-text">오프라인 리스크 엔진 및 슬리피지 모델 오라클 승인</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}
