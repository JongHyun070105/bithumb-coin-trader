import { useEffect, useState } from 'react'
import { Lock, CircleDot, RefreshCw } from 'lucide-react'
import { MetricCard } from '../components/MetricCard'
import { ModeBanner } from '../components/ModeBanner'

const PAPER_RUNTIME_URL = 'http://127.0.0.1:8787/api/paper/runtime'

type PaperRuntimePayload = {
  status: 'AVAILABLE' | 'NOT_CONFIGURED' | 'NOT_AVAILABLE' | 'UNAVAILABLE' | 'EMPTY' | 'CORRUPT'
  event_id?: string
  timestamp_ms?: number
  journal_name?: string
  metrics_integrity?: string
  metrics?: Record<string, unknown> | null
}

const paperMetricLabels: Record<string, string> = {
  market_data_age_ms: 'Market data age (ms)',
  strategy_state: 'Strategy state',
  signal_count: 'Signals',
  order_count: 'Orders',
  fill_count: 'Fills',
  rejections: 'Rejections',
  positions: 'Positions',
  cash_krw: 'Cash (KRW)',
  reserved_cash_krw: 'Reserved cash (KRW)',
  equity_krw: 'Equity (KRW)',
  realized_pnl_krw: 'Realized PnL (KRW)',
  unrealized_pnl_krw: 'Unrealized PnL (KRW)',
  fees_krw: 'Fees (KRW)',
  slippage_cost_krw: 'Slippage (KRW)',
  turnover_krw: 'Turnover (KRW)',
  drawdown_fraction: 'Drawdown',
  risk_state: 'Risk state',
  risk_verdict: 'Risk verdict',
  risk_reason_codes: 'Risk reasons',
  halt_reason: 'Halt reason',
  journal_state: 'Journal',
  restart_count: 'Restarts'
}

function parsePaperRuntime(value: unknown): PaperRuntimePayload {
  if (typeof value !== 'object' || value === null || !('status' in value)) {
    throw new Error('The local API returned an invalid PAPER snapshot.')
  }
  const payload = value as Record<string, unknown>
  const allowedStatuses: PaperRuntimePayload['status'][] = [
    'AVAILABLE', 'NOT_CONFIGURED', 'NOT_AVAILABLE', 'UNAVAILABLE', 'EMPTY', 'CORRUPT'
  ]
  if (typeof payload.status !== 'string' || !allowedStatuses.includes(payload.status as PaperRuntimePayload['status'])) {
    throw new Error('The local API returned an unknown PAPER snapshot status.')
  }
  const metrics = payload.metrics
  if (metrics !== undefined && metrics !== null && (typeof metrics !== 'object' || Array.isArray(metrics))) {
    throw new Error('The local API returned invalid PAPER metrics.')
  }
  return payload as PaperRuntimePayload
}

function displayMetric(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export const Trading: React.FC = () => {
  const [connected, setConnected] = useState(false)
  const [snapshot, setSnapshot] = useState<PaperRuntimePayload | null>(null)
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    if (!connected) return
    let active = true
    let timer: number | undefined
    const controller = new AbortController()

    const poll = async () => {
      try {
        const response = await fetch(PAPER_RUNTIME_URL, {
          method: 'GET',
          headers: { Accept: 'application/json' },
          signal: controller.signal
        })
        if (!response.ok) throw new Error(`Local API returned HTTP ${response.status}.`)
        const nextSnapshot = parsePaperRuntime(await response.json())
        if (active) {
          setSnapshot(nextSnapshot)
          setLoadError('')
        }
      } catch (error) {
        if (!active || (error instanceof DOMException && error.name === 'AbortError')) return
        setLoadError(error instanceof Error ? error.message : 'Could not read the local PAPER snapshot.')
      }
      if (active) timer = window.setTimeout(poll, 5_000)
    }

    void poll()
    return () => {
      active = false
      controller.abort()
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [connected])

  const metrics = snapshot?.status === 'AVAILABLE' ? snapshot.metrics : null

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

      <section className="section-block paper-runtime-section" aria-labelledby="paper-runtime-heading">
        <div className="section-header-row">
          <div>
            <h3 id="paper-runtime-heading" className="section-title">Local PAPER observability</h3>
            <p className="muted-text">Opt in to read the latest journal snapshot from the loopback API. This does not start PAPER or enable orders.</p>
          </div>
          {connected ? (
            <button className="btn-action btn-clear" onClick={() => setConnected(false)}>
              Disconnect local journal
            </button>
          ) : (
            <button
              className="btn-action btn-clear"
              onClick={() => {
                setLoadError('')
                setSnapshot(null)
                setConnected(true)
              }}
            >
              Connect local journal
            </button>
          )}
        </div>

        {!connected && <p className="muted-text" role="status">Local journal is disconnected; no request has been made.</p>}
        {connected && loadError && <p className="danger-text" role="alert">{loadError}</p>}
        {connected && !snapshot && !loadError && <p className="muted-text" role="status">Reading local journal…</p>}
        {connected && snapshot && (
          <div className="paper-runtime-content" aria-live="polite">
            <div className="paper-runtime-summary card-surface">
              <strong>Journal status: {snapshot.status}</strong>
              {snapshot.journal_name && <span>Journal: {snapshot.journal_name}</span>}
              {snapshot.event_id && <span>Latest event: <code>{snapshot.event_id}</code></span>}
              {snapshot.timestamp_ms !== undefined && <span>Event time (ms): {snapshot.timestamp_ms}</span>}
              {snapshot.metrics_integrity && (
                <span className="warning-text">Metrics integrity: {snapshot.metrics_integrity}</span>
              )}
            </div>
            {metrics && (
              <div className="status-grid">
                {Object.entries(paperMetricLabels).map(([key, label]) => (
                  <MetricCard key={key} title={label} value={displayMetric(metrics[key])} />
                ))}
                {metrics.fill_model !== undefined && (
                  <MetricCard title="Fill model" value={displayMetric(metrics.fill_model)} />
                )}
              </div>
            )}
            {snapshot.status !== 'AVAILABLE' && <p className="muted-text">No PAPER event snapshot is available.</p>}
          </div>
        )}
        {connected && (
          <small className="muted-text"><RefreshCw size={12} aria-hidden="true" /> Refreshes every 5 seconds while connected. Read-only; LIVE remains disabled.</small>
        )}
      </section>
    </div>
  )
}
