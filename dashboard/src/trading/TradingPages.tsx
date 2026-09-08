import { useState } from 'react'
import { ArrowRight, ArrowUpRight, CirclePause, LockKeyhole, ShieldCheck } from 'lucide-react'
import type { ReactNode } from 'react'
import type { TradingDataState, TradingSnapshot } from './model'
import { tradingDayInSeoul } from './model'
import {
  available,
  dateLabel,
  duration,
  money,
  number,
  percent,
  prepareTradingState,
  tone,
} from './format'
import { DailyChart, EmptyState, EquityChart, Segmented } from './Charts'

export function FinancialMetric({
  label,
  value,
  subtext,
  change,
}: {
  label: string
  value: string
  subtext?: string
  change?: number | null
}) {
  return (
    <article className="financial-metric" aria-label={label}>
      <span>{label}</span>
      <strong className={tone(change)}>{value}</strong>
      {subtext && <small>{subtext}</small>}
    </article>
  )
}

function Metrics({ children, count }: { children: ReactNode; count?: number }) {
  return <div className={`financial-metrics${count ? ` metrics-${count}` : ''}`}>{children}</div>
}

function PanelHeading({
  title,
  detail,
  action,
  onAction,
}: {
  title: string
  detail?: string
  action?: string
  onAction?: () => void
}) {
  return (
    <div className="panel-heading">
      <div>
        <h2>
          {title}
          {detail && <span className="count-badge">{detail}</span>}
        </h2>
      </div>
      {action && (
        <button className="text-action" onClick={onAction}>
          {action}
          <ArrowUpRight size={14} />
        </button>
      )}
    </div>
  )
}

function Asset({ asset, name }: { asset: string; name?: string }) {
  return (
    <span className="asset-cell">
      <span className={`asset-icon asset-${asset.toLowerCase()}`}>
        {asset === 'BTC' ? '₿' : asset === 'ETH' ? 'Ξ' : asset.slice(0, 1)}
      </span>
      <span>
        <strong>{asset}</strong>
        {name && <small>{name}</small>}
      </span>
    </span>
  )
}

function PositionsTable({
  data,
  full = false,
  holdings = false,
  navigate,
}: {
  data: TradingDataState
  full?: boolean
  holdings?: boolean
  navigate?: (page: string) => void
}) {
  const positions = data.snapshot?.positions
  const title = holdings ? '보유 자산' : '오픈 포지션'
  return (
    <section className="trade-panel positions-panel" aria-label={title}>
      <PanelHeading
        title={title}
        detail={positions ? String(positions.length) : undefined}
        action={!full && !holdings ? '포지션 보기' : undefined}
        onAction={() => navigate?.('positions')}
      />
      {!positions?.length ? (
        <EmptyState data={data} title={holdings ? '보유 자산 없음' : '오픈 포지션 없음'} compact />
      ) : (
        <div className="trade-table-scroll" tabIndex={0} role="region" aria-label={`${title} 표, 가로 스크롤`}>
          <table className="trade-table">
            <thead>
              <tr>
                <th>자산</th>
                {!holdings && <th>방향</th>}
                <th>{holdings ? '평균 진입가' : '진입가'}</th>
                <th>{holdings ? '현재가' : '현재가'}</th>
                <th>{holdings ? '수량' : '수량'}</th>
                {(full || holdings) && <th>{holdings ? '평가금액' : '노출'}</th>}
                <th>PnL</th>
                <th>PnL %</th>
                {!holdings && <th>보유 기간</th>}
                {full && !holdings && <th>전략</th>}
              </tr>
            </thead>
            <tbody>
              {positions.map((position) => (
                <tr key={position.id}>
                  <td>
                    <Asset asset={position.asset} name={holdings ? position.name : 'KRW'} />
                  </td>
                  {!holdings && (
                    <td>
                      <span className="side-long">{position.side === 'LONG' ? '롱' : '숏'}</span>
                    </td>
                  )}
                  <td>{money(position.entry)}</td>
                  <td>{money(position.current)}</td>
                  <td>{number(position.quantity, 6)}</td>
                  {(full || holdings) && <td>{money(position.exposure)}</td>}
                  <td className={tone(position.pnl)}>{money(position.pnl, true)}</td>
                  <td className={tone(position.pnlPct)}>{percent(position.pnlPct)}</td>
                  {!holdings && <td className="muted">{duration(position.openedAt, data.snapshot?.timestamp)}</td>}
                  {full && !holdings && <td>{position.strategy ?? '—'}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {positions?.length ? <div className="table-footnote">PnL에 진입 수수료 포함. 청산 수수료 미포함.</div> : null}
    </section>
  )
}

function TradesTable({
  data,
  full = false,
  navigate,
}: {
  data: TradingDataState
  full?: boolean
  navigate?: (page: string) => void
}) {
  const [outcome, setOutcome] = useState('전체')
  const [period, setPeriod] = useState('전체')
  const snapshot = data.snapshot
  const trades = [...(snapshot?.recentTrades ?? [])]
    .sort((a, b) => Date.parse(b.closedAt) - Date.parse(a.closedAt))
    .filter((trade) => {
      if (outcome === '수익' && !(available(trade.pnl) && trade.pnl > 0)) return false
      if (outcome === '손실' && !(available(trade.pnl) && trade.pnl < 0)) return false
      if (!snapshot || period === '전체') return true
      if (period === '오늘') return tradingDayInSeoul(trade.closedAt) === tradingDayInSeoul(snapshot.timestamp)
      return Date.parse(trade.closedAt) >= Date.parse(snapshot.timestamp) - (period === '7일' ? 7 : 30) * 86400000
    })
  const shown = full ? trades : trades.slice(0, 5)
  return (
    <section className="trade-panel trades-panel" aria-label={full ? '거래 내역' : '최근 거래'}>
      <PanelHeading
        title={full ? '거래 내역' : '최근 거래'}
        action={full ? undefined : '전체 거래'}
        onAction={() => navigate?.('trades')}
      />
      {full && (
        <div className="trade-filters">
          <Segmented label="거래 결과" options={['전체', '수익', '손실']} value={outcome} onChange={setOutcome} />
          <Segmented label="거래 기간" options={['오늘', '7일', '30일', '전체']} value={period} onChange={setPeriod} />
        </div>
      )}
      {!shown.length ? (
        <EmptyState
          data={data}
          title={snapshot && snapshot.recentTrades.length ? '필터 조건에 맞는 거래 없음' : '거래 내역이 없습니다'}
          compact
        />
      ) : (
        <div className="trade-table-scroll" tabIndex={0} role="region" aria-label="거래 내역 표, 가로 스크롤">
          <table className="trade-table">
            <thead>
              <tr>
                <th>시간 (KST)</th>
                <th>페어</th>
                <th>방향</th>
                <th>진입가</th>
                <th>청산가</th>
                <th>PnL</th>
                <th>PnL %</th>
                <th>수수료</th>
                <th>보유 기간</th>
                {full && <th>청산 사유</th>}
              </tr>
            </thead>
            <tbody>
              {shown.map((trade) => (
                <tr key={trade.id}>
                  <td className="muted">{dateLabel(trade.closedAt, true)}</td>
                  <td>
                    <strong>{trade.pair}</strong>
                  </td>
                  <td>
                    <span className="side-long">{trade.side === 'LONG' ? '롱' : '숏'}</span>
                  </td>
                  <td>{money(trade.entry)}</td>
                  <td>{money(trade.exit)}</td>
                  <td className={tone(trade.pnl)}>{money(trade.pnl, true)}</td>
                  <td className={tone(trade.pnlPct)}>{percent(trade.pnlPct)}</td>
                  <td>{money(trade.fee)}</td>
                  <td className="muted">{duration(trade.openedAt, trade.closedAt)}</td>
                  {full && <td>{trade.exitReason ?? '—'}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {snapshot && (
        <div className="table-footnote">
          {full ? `${shown.length}건 표시` : `최신 ${shown.length}건 / 총 ${snapshot.recentTrades.length}건`} · 순이익에
          진입·청산 수수료 포함
        </div>
      )}
    </section>
  )
}

function TodaySummary({ snapshot }: { snapshot?: TradingSnapshot }) {
  const today = snapshot?.today
  const rows = [
    ['오늘 실현 PnL', money(today?.realizedPnl, true), today?.realizedPnl],
    ['오늘 미실현 변동', money(today?.unrealizedPnl, true), today?.unrealizedPnl],
    ['오늘 수수료', money(today?.fees)],
    ['오늘 거래', number(today?.trades)],
    ['수익 / 손실', `${number(today?.wins)} / ${number(today?.losses)}`],
    ['노출 비율', percent(today?.exposurePct, false)],
  ] as const
  return (
    <section className="trade-panel today-panel" aria-label="오늘의 요약">
      <PanelHeading title="오늘의 요약" detail="KST" />
      <dl className="summary-list">
        {rows.map(([label, value, change]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd className={tone(change)}>{value}</dd>
          </div>
        ))}
      </dl>
      <div className="summary-note">
        <span className="connection-dot" />
        {snapshot ? '순이익 · 수수료 포함' : '첫 번째 트레이딩 세션을 기다리는 중'}
      </div>
    </section>
  )
}

function PerformanceSnapshot({
  snapshot,
  navigate,
}: {
  snapshot?: TradingSnapshot
  navigate: (page: string) => void
}) {
  const performance = snapshot?.performance
  const rows = [
    ['7일 수익률', percent(performance?.return7d), performance?.return7d],
    ['30일 수익률', percent(performance?.return30d), performance?.return30d],
    ['총 수익률', percent(performance?.totalReturn), performance?.totalReturn],
    ['최대 낙폭', percent(performance?.maxDrawdown), performance?.maxDrawdown],
    ['승률', percent(performance?.winRate, false)],
    ['손익비', number(performance?.profitFactor, 2)],
  ] as const
  return (
    <section className="trade-panel performance-snapshot">
      <PanelHeading title="성과 요약" action="자세히" onAction={() => navigate('performance')} />
      <div className="performance-mini-grid">
        {rows.map(([label, value, change]) => (
          <div key={label}>
            <span>{label}</span>
            <strong className={tone(change)}>{value}</strong>
          </div>
        ))}
      </div>
    </section>
  )
}

function Allocation({ data }: { data: TradingDataState }) {
  const portfolio = data.snapshot?.portfolio
  const positions = data.snapshot?.positions
  const equity = portfolio?.equity
  const valid = available(equity) && equity > 0 && available(portfolio?.cash) && positions?.every((pos) => available(pos.exposure))
  const allocations =
    valid && positions
      ? [
          ...positions.map((pos) => ({ asset: pos.asset, value: pos.exposure as number })),
          { asset: '현금', value: portfolio.cash as number },
        ]
      : []
  return (
    <section className="trade-panel allocation-panel">
      <PanelHeading title="자산 배분" />
      {!valid ? (
        <EmptyState data={data} title="배분 정보 없음" compact />
      ) : (
        <>
          <div
            className="allocation-bar"
            role="img"
            aria-label={allocations.map((row) => `${row.asset} ${percent((row.value / equity) * 100, false)}`).join(', ')}
          >
            {allocations.map((row, i) => (
              <span
                key={row.asset}
                className={`allocation-color color-${i}`}
                style={{ width: `${(row.value / equity) * 100}%` }}
              />
            ))}
          </div>
          <dl className="summary-list">
            {allocations.map((row, i) => (
              <div key={row.asset}>
                <dt>
                  <i className={`allocation-dot color-${i}`} />
                  {row.asset}
                </dt>
                <dd>
                  {percent((row.value / equity) * 100, false)}
                  <small>{money(row.value)}</small>
                </dd>
              </div>
            ))}
          </dl>
        </>
      )}
    </section>
  )
}

function OperationalPage({
  system,
  data,
  navigate,
}: {
  system: boolean
  data: TradingDataState
  navigate: (page: string) => void
}) {
  const bot = data.snapshot?.botStatus
  const sourceLabel =
    data.status === 'SYNTHETIC_DEMO'
      ? '데모 데이터 (합성)'
      : data.status === 'LOCAL_SNAPSHOT'
      ? '로컬 스냅샷'
      : data.status === 'READ_ONLY_API'
      ? '읽기 전용 API'
      : data.status === 'REAL_DATA'
      ? (data.snapshot?.source.label ?? '실제 데이터')
      : '트레이딩 데이터 없음'

  const apiStatus = data.status === 'READ_ONLY_API' ? '연결됨 (읽기 전용)' : '미연결 (오프라인)'
  const lastUpdated = dateLabel(data.snapshot?.timestamp, true)

  const rows = system
    ? [
        ['앱 버전', '0.3.0'],
        ['데이터 소스', sourceLabel],
        ['API 상태', apiStatus],
        ['런타임 모드', '로컬 UI 전용'],
        ['시장 데이터 상태', '대기 중'],
        ['백엔드 연결', '미연결'],
        ['마지막 업데이트', lastUpdated],
        ['데이터 지연', '—'],
        ['거래 모드', data.snapshot?.mode ?? 'OFF'],
        ['오류', data.status === 'ERROR' ? (data.error ?? '오류 발생') : '—'],
        ['경고', '—'],
      ]
    : [
        ['모드', 'OFF'],
        ['전략', '미배포'],
        ['시장 데이터', '대기 중'],
        ['주문 실행', '비활성화'],
        ['리스크 가드', '잠김'],
        ['마지막 활동', dateLabel(bot?.lastActivity ?? undefined, true)],
        ['가동 시간', available(bot?.uptimeSeconds) ? `${number(bot.uptimeSeconds)} 초` : '—'],
        ['오늘 거래', number(bot?.todayTrades)],
        ['오류', number(bot?.errors)],
      ]

  return (
    <div className="operations-layout">
      <section className="trade-panel operations-card">
        <PanelHeading title={system ? '워크스페이스 정보' : '봇 개요'} />
        <div className="bot-state-hero">
          <span className="bot-state-icon">{system ? <ShieldCheck size={25} /> : <CirclePause size={25} />}</span>
          <div>
            <h2>{system ? '로컬 워크스페이스' : '봇 비활성화'}</h2>
            <p>
              {system
                ? '계정 및 실행 서비스가 연결되지 않았습니다.'
                : '페이퍼 트레이딩이 시작되지 않았습니다. 전략이 배포되지 않았습니다.'}
            </p>
          </div>
          <span className="quiet-badge">읽기 전용</span>
        </div>
        <dl className="summary-list operational-list">
          {rows.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </section>
      <section className="trade-panel operations-aside">
        <LockKeyhole size={24} />
        <h2>실행은 비활성화됩니다</h2>
        <p>
          이 워크스페이스는 트레이딩 정보를 표시합니다. 실거래는 비활성화되어 있으며, 전략의 알파는 아직 검증되지
          않았습니다.
        </p>
        <div className="operation-state-row">
          <span>페이퍼</span>
          <strong>미시작</strong>
        </div>
        <div className="operation-state-row">
          <span>라이브</span>
          <strong>비활성화</strong>
        </div>
        <button className="text-action" onClick={() => navigate('advanced/overview')}>
          연구 &amp; 증거
          <ArrowRight size={16} />
        </button>
      </section>
    </div>
  )
}

const names: Record<string, { title: string; description: string }> = {
  dashboard: { title: '대시보드', description: '포트폴리오 현황. 한눈에 확인하세요.' },
  portfolio: { title: '포트폴리오', description: '자본, 보유 자산, 배분 현황.' },
  positions: { title: '보유 포지션', description: '오픈 포지션을 자세히 확인하세요.' },
  trades: { title: '거래 내역', description: '체결된 모든 거래와 전체 내역.' },
  performance: { title: '성과 분석', description: '전략의 시간별 성과를 확인하세요.' },
  'bot-status': { title: '봇 상태', description: '실행, 전략, 운영 상태.' },
  system: { title: '시스템', description: '연결 및 워크스페이스 정보.' },
}

export function TradingPage({
  page,
  data: suppliedData,
  navigate,
}: {
  page: string
  data: TradingDataState
  navigate: (page: string) => void
}) {
  const data = prepareTradingState(suppliedData)
  const snapshot = data.snapshot,
    portfolio = snapshot?.portfolio,
    performance = snapshot?.performance
  const { title, description } = names[page] ?? names.dashboard

  return (
    <section className="trading-page" aria-label={`${title} 페이지`} aria-busy={data.status === 'LOADING'}>
      <div className="trade-page-heading">
        <div>
          <h1>{title}</h1>
          <p>{description}</p>
        </div>
        <div className="page-asof">
          <span>
            {snapshot
              ? `${data.status === 'SYNTHETIC_DEMO' ? '합성 데이터 미리보기' : snapshot.source.label}`
              : '트레이딩 데이터 없음'}
          </span>
          <small>{snapshot ? `${dateLabel(snapshot.timestamp, true)} KST` : '페이퍼 트레이딩이 시작되지 않았습니다.'}</small>
        </div>
      </div>

      {data.isStale && snapshot && (
        <div className="trade-state-notice" style={{ borderColor: 'rgba(234, 179, 8, 0.4)', color: '#facc15' }} role="status">
          ⚠️ 데이터가 오래되었습니다. (마지막 업데이트: {dateLabel(snapshot.timestamp, true)} KST)
        </div>
      )}

      {data.status === 'ERROR' && (
        <div className="trade-state-notice" role="alert">
          {data.error || '트레이딩 데이터를 불러올 수 없습니다.'}
        </div>
      )}
      {data.status === 'LOADING' && (
        <div className="trade-state-notice" role="status">
          트레이딩 데이터 로딩 중…
        </div>
      )}

      {page === 'dashboard' && (
        <>
          <Metrics>
            <FinancialMetric
              label="포트폴리오 가치"
              value={money(portfolio?.equity)}
              subtext="총 자본 · KRW"
            />
            <FinancialMetric
              label="오늘 PnL"
              value={money(portfolio?.todayPnl, true)}
              change={portfolio?.todayPnl}
              subtext="00:00 KST 이후 순변동"
            />
            <FinancialMetric
              label="오늘 수익률"
              value={percent(portfolio?.todayReturnPct)}
              change={portfolio?.todayReturnPct}
              subtext="일 시가 기준"
            />
            <FinancialMetric
              label="총 PnL"
              value={money(portfolio?.totalPnl, true)}
              change={portfolio?.totalPnl}
              subtext={available(portfolio?.totalReturnPct) ? `${percent(portfolio.totalReturnPct)} 누적` : '첫 번째 트레이딩 세션 이후'}
            />
          </Metrics>
          <div className="dashboard-chart-grid">
            <EquityChart data={data} />
            <TodaySummary snapshot={snapshot} />
          </div>
          <PositionsTable data={data} navigate={navigate} />
          <div className="dashboard-lower-grid">
            <TradesTable data={data} navigate={navigate} />
            <DailyChart data={data} />
          </div>
          <PerformanceSnapshot snapshot={snapshot} navigate={navigate} />
        </>
      )}

      {page === 'portfolio' && (
        <>
          <Metrics count={5}>
            <FinancialMetric label="총 자본" value={money(portfolio?.equity)} />
            <FinancialMetric label="가용 현금" value={money(portfolio?.cash)} />
            <FinancialMetric label="투자 / 노출" value={money(portfolio?.exposure)} />
            <FinancialMetric label="실현 PnL" value={money(portfolio?.realizedPnl, true)} change={portfolio?.realizedPnl} />
            <FinancialMetric label="미실현 PnL" value={money(portfolio?.unrealizedPnl, true)} change={portfolio?.unrealizedPnl} />
          </Metrics>
          <div className="dashboard-chart-grid">
            <EquityChart data={data} />
            <Allocation data={data} />
          </div>
          <PositionsTable data={data} holdings />
        </>
      )}

      {page === 'positions' && (
        <>
          <Metrics count={3}>
            <FinancialMetric
              label="오픈 포지션"
              value={snapshot ? number(snapshot.positions.length) : '—'}
              subtext="활성 현물 포지션"
            />
            <FinancialMetric label="총 노출" value={money(portfolio?.exposure)} />
            <FinancialMetric label="미실현 PnL" value={money(portfolio?.unrealizedPnl, true)} change={portfolio?.unrealizedPnl} />
          </Metrics>
          <PositionsTable data={data} full />
        </>
      )}

      {page === 'trades' && (
        <>
          <Metrics count={3}>
            <FinancialMetric
              label="체결 거래"
              value={snapshot ? number(snapshot.recentTrades.length) : '—'}
              subtext="제공된 내역"
            />
            <FinancialMetric label="실현 PnL" value={money(portfolio?.realizedPnl, true)} change={portfolio?.realizedPnl} />
            <FinancialMetric label="승률" value={percent(performance?.winRate, false)} subtext="수수료 차감 후" />
          </Metrics>
          <TradesTable data={data} full />
        </>
      )}

      {page === 'performance' && (
        <>
          <Metrics count={6}>
            <FinancialMetric label="총 수익률" value={percent(performance?.totalReturn)} change={performance?.totalReturn} />
            <FinancialMetric label="순 PnL" value={money(portfolio?.totalPnl, true)} change={portfolio?.totalPnl} />
            <FinancialMetric label="최대 낙폭" value={percent(performance?.maxDrawdown)} change={performance?.maxDrawdown} />
            <FinancialMetric label="승률" value={percent(performance?.winRate, false)} />
            <FinancialMetric label="손익비" value={number(performance?.profitFactor, 2)} />
            <FinancialMetric label="평균 거래" value={money(performance?.averageTrade, true)} change={performance?.averageTrade} />
          </Metrics>
          <EquityChart data={data} />
          <div className="performance-charts-grid">
            <DailyChart data={data} returns />
            <EquityChart data={data} drawdown />
          </div>
        </>
      )}

      {(page === 'bot-status' || page === 'system') && (
        <OperationalPage system={page === 'system'} data={data} navigate={navigate} />
      )}
    </section>
  )
}
