import React from 'react'
import { ShieldCheck, Lock, AlertTriangle, Key, Ban, PowerOff } from 'lucide-react'
import { ModeBanner } from '../components/ModeBanner'

export const SafetyCenter: React.FC = () => {
  return (
    <div className="page-container">
      <ModeBanner />

      <div className="page-header">
        <div>
          <h2>안전 센터 및 게이트 통제 (Safety & Security Center)</h2>
          <p className="page-subtitle">
            실거래 주문 및 자산 이동을 방지하기 위한 소프트웨어 및 네트워크 레벨의 불변 안전 가드 현황
          </p>
        </div>
      </div>

      {/* Safety Invariant Notice */}
      <section className="section-block">
        <div className="safety-hero-box">
          <Lock size={32} className="icon-locked-hero" />
          <div>
            <h3>읽기 전용 안전 화면 (Strictly Read-Only Safety Interface)</h3>
            <p>
              본 시스템은 연구 전용 격리 콘솔입니다. 시스템 설계상 실거래 실행 스위치나 안전 설정을 우회하는 제어 컨트롤을 일체 구현하지 않았습니다 (Fail-Closed 원칙).
            </p>
          </div>
        </div>
      </section>

      {/* Immutable Safety Gates Grid */}
      <section className="section-block">
        <h3 className="section-title">
          <ShieldCheck size={18} />
          <span>7대 불변 안전 게이트 (7 Immutable Safety Gates)</span>
        </h3>

        <div className="safety-gates-grid">
          <div className="gate-card gate-locked">
            <div className="gate-top">
              <Key size={18} />
              <span className="gate-title">PRIVATE API KEYS</span>
            </div>
            <div className="gate-status">DISABLED (키 입력 기능 없음)</div>
            <p className="gate-desc">빗썸/바이낸스/업비트 주문용 API Key/Secret이 코드 및 환경변수에 일체 존재하지 않습니다.</p>
          </div>

          <div className="gate-card gate-locked">
            <div className="gate-top">
              <PowerOff size={18} />
              <span className="gate-title">LIVE ORDER TRANSPORT</span>
            </div>
            <div className="gate-status">DISABLED (수송 계층 차단)</div>
            <p className="gate-desc">BithumbLiveOrderTransport 모듈은 ImportError를 발생시키며 영구 봉인되어 있습니다.</p>
          </div>

          <div className="gate-card gate-locked">
            <div className="gate-top">
              <Ban size={18} />
              <span className="gate-title">LIVE TRADING ENGINE</span>
            </div>
            <div className="gate-status">DISABLED (실거래 금지)</div>
            <p className="gate-desc">실제 자산 매수/매도 주문 파이프라인은 컴파일 타임 및 런타임에 전면 차단되어 있습니다.</p>
          </div>

          <div className="gate-card gate-neutral">
            <div className="gate-top">
              <Lock size={18} />
              <span className="gate-title">PAPER TRADING</span>
            </div>
            <div className="gate-status">NOT STARTED (미시행)</div>
            <p className="gate-desc">모의 거래는 데이터 품질 통과, 가설 검증, 거버넌스 승인 후 별도 승인 절차를 필요로 합니다.</p>
          </div>

          <div className="gate-card gate-neutral">
            <div className="gate-top">
              <AlertTriangle size={18} />
              <span className="gate-title">MICROSTRUCTURE ALPHA</span>
            </div>
            <div className="gate-status">UNPROVEN (미검증)</div>
            <p className="gate-desc">홀드아웃 데이터셋 및 과학적 통계 검정이 완료되기 전까지 알파는 존재하지 않는 것으로 취급합니다.</p>
          </div>

          <div className="gate-card gate-neutral">
            <div className="gate-top">
              <ShieldCheck size={18} />
              <span className="gate-title">REAL-DATA DATA QUALITY</span>
            </div>
            <div className="gate-status">NOT RUN (대기 중)</div>
            <p className="gate-desc">72시간 수집 데이터셋의 품질 검증 전까지 어떠한 다운스트림 연구나 모델 피팅도 허용되지 않습니다.</p>
          </div>

          <div className="gate-card gate-locked">
            <div className="gate-top">
              <Ban size={18} />
              <span className="gate-title">UNKNOWN RISK POLICY</span>
            </div>
            <div className="gate-status">FAIL-CLOSED (무조건 거부)</div>
            <p className="gate-desc">불확실하거나 모호한 조건이 발생할 경우 자산을 보호하기 위해 모든 처리를 즉시 중단합니다.</p>
          </div>
        </div>
      </section>
    </div>
  )
}
