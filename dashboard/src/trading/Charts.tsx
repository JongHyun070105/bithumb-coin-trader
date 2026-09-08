import { useId, useState } from 'react'
import { ChartNoAxesCombined } from 'lucide-react'
import type { TradingDataState } from './model'
import { available, dateLabel, money, percent } from './format'

export function EmptyState({ data, title, compact = false }: { data: TradingDataState; title: string; compact?: boolean }) {
  const loading = data.status === 'LOADING', error = data.status === 'ERROR'
  return <div className={`trade-empty${compact ? ' compact' : ''}${loading ? ' is-loading' : ''}`} role={error ? 'alert' : 'status'}>
    <span className="empty-icon"><ChartNoAxesCombined size={20} /></span>
    <strong>{loading ? '트레이딩 데이터 로딩 중…' : error ? '트레이딩 데이터 불가' : title}</strong>
    <small>{loading ? '공급된 스냅샷 대기 중' : error ? data.error || '공급된 데이터를 표시할 수 없습니다.' : data.status === 'NO_DATA' ? '페이퍼 트레이딩이 시작되지 않았습니다.' : '해당 기간에 기록이 없습니다.'}</small>
  </div>
}

export function Segmented({ label, options, value, onChange }: { label: string; options: readonly string[]; value: string; onChange: (value: string) => void }) {
  return <div className="segmented" role="group" aria-label={label}>{options.map(option => <button key={option} aria-pressed={value === option} onClick={() => onChange(option)}>{option}</button>)}</div>
}

const ranges: Record<string, number> = { '1D': 1, '7D': 7, '30D': 30, '3M': 90 }
const axisMoney = (value: number) => Math.abs(value) >= 1000000 ? `₩${(value / 1000000).toFixed(2)}M` : money(value)

export function EquityChart({ data, drawdown = false }: { data: TradingDataState; drawdown?: boolean }) {
  const [period, setPeriod] = useState('30D')
  const [display, setDisplay] = useState('금액')
  const [selected, setSelected] = useState<number | null>(null)
  const gradient = useId().replace(/:/g, '')
  const snapshot = data.snapshot
  const all = snapshot?.equityCurve ?? []
  const endTime = snapshot ? Date.parse(snapshot.timestamp) : NaN
  const points = all.filter(point => period === 'ALL' || Date.parse(point.timestamp) >= endTime - ranges[period] * 86400000)
  const field = drawdown ? 'drawdownPct' : display === '금액' ? 'equity' : 'returnPct'
  const values = points.map(point => point[field]).filter(available)
  const ready = values.length >= 2
  const rawMin = ready ? Math.min(...values, ...(drawdown ? [0] : [])) : 0
  const rawMax = ready ? Math.max(...values, ...(drawdown ? [0] : [])) : 1
  const padding = Math.max((rawMax - rawMin) * .18, Math.abs(rawMax) * .0002, .001)
  const min = drawdown ? rawMin - padding : rawMin - padding, max = drawdown ? 0 : rawMax + padding
  const y = (value: number) => 12 + (max - value) / (max - min) * 186
  const t0 = points.length ? Date.parse(points[0].timestamp) : 0
  const t1 = points.length ? Date.parse(points[points.length - 1].timestamp) : 1
  const x = (timestamp: string) => 8 + (Date.parse(timestamp) - t0) / Math.max(t1 - t0, 1) * 620
  const path = points.map((point, i) => {
    const value = point[field]
    if (!available(value)) return ''
    return `${i > 0 && available(points[i - 1][field]) ? 'L' : 'M'}${x(point.timestamp).toFixed(2)},${y(value).toFixed(2)}`
  }).join(' ')
  const continuous = ready && values.length === points.length
  const index = selected === null ? points.length - 1 : Math.min(selected, points.length - 1)
  const active = points[index]
  const format = (value: number | null | undefined) => field === 'equity' ? money(value) : percent(value)
  const description = ready ? `${drawdown ? '낙폭' : '포트폴리오 히스토리'}, ${period}. ${dateLabel(points[0].timestamp)} ~ ${dateLabel(points.at(-1)?.timestamp)}. ${format(points[0][field])} → ${format(points.at(-1)?.[field])}.` : '아직 트레이딩 내역이 없습니다'

  return <section className={`trade-panel equity-panel${drawdown ? ' drawdown-panel' : ''}`} aria-label={drawdown ? '낙폭 차트' : '포트폴리오 가치 차트'}>
    <div className="panel-heading"><div><h2>{drawdown ? '낙폭' : '포트폴리오 가치'}</h2><span className="panel-caption">{drawdown ? '고점 대비 낙폭' : '시간별 자본 변화'}</span></div>{!drawdown && <Segmented label="자본 표시" options={['금액', '수익률 %']} value={display} onChange={value => { setDisplay(value); setSelected(null) }} />}</div>
    <div className="chart-toolbar"><div className="chart-readout"><strong className={drawdown ? 'negative' : ''}>{ready ? format(active?.[field]) : '—'}</strong><small>{ready ? dateLabel(active?.timestamp, period === '1D') : '트레이딩 내역을 기다리는 중'}</small></div><Segmented label={drawdown ? '낙폭 기간' : '자본 기간'} options={['1D', '7D', '30D', '3M', 'ALL']} value={period} onChange={value => { setPeriod(value); setSelected(null) }} /></div>
    <div className="chart-stage">
      {!ready ? <><div className="empty-grid" aria-hidden="true" /><EmptyState data={data} title="아직 트레이딩 내역이 없습니다" /></> : <>
        <svg className="equity-svg" viewBox="0 0 700 220" role="img" aria-label={description} preserveAspectRatio="none" onMouseLeave={() => setSelected(null)} onMouseMove={event => {
          const rect = event.currentTarget.getBoundingClientRect()
          const time = t0 + Math.max(0, Math.min(1, ((event.clientX - rect.left) / rect.width * 700 - 8) / 620)) * (t1 - t0)
          let nearest = 0
          for (let i = 1; i < points.length; i++) if (Math.abs(Date.parse(points[i].timestamp) - time) < Math.abs(Date.parse(points[nearest].timestamp) - time)) nearest = i
          setSelected(nearest)
        }}>
          <title>{description}</title>
          <defs><linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={drawdown ? 'var(--trade-red)' : 'var(--trade-blue)'} stopOpacity=".18" /><stop offset="100%" stopColor={drawdown ? 'var(--trade-red)' : 'var(--trade-blue)'} stopOpacity="0" /></linearGradient></defs>
          {[12, 74, 136, 198].map(line => <line key={line} x1="8" x2="636" y1={line} y2={line} className="chart-grid-line" />)}
          {continuous && <path d={`${path} L628,${drawdown ? 12 : 198} L8,${drawdown ? 12 : 198} Z`} fill={`url(#${gradient})`} />}
          <path d={path} fill="none" stroke={drawdown ? 'var(--trade-red)' : 'var(--trade-blue)'} strokeWidth="2.3" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
          {active && available(active[field]) && <><line x1={x(active.timestamp)} x2={x(active.timestamp)} y1="12" y2="198" className="chart-cursor" /><circle cx={x(active.timestamp)} cy={y(active[field])} r="4" fill={drawdown ? 'var(--trade-red)' : 'var(--trade-blue)'} stroke="var(--trade-panel)" strokeWidth="2" /></>}
          {[0, 1, 2, 3].map(step => <text key={step} x="649" y={16 + step * 62} className="chart-axis">{field === 'equity' ? axisMoney(max - (max - min) * step / 3) : percent(max - (max - min) * step / 3, false)}</text>)}
        </svg>
        <input className="chart-keyboard" type="range" min="0" max={points.length - 1} value={Math.max(index, 0)} onChange={event => setSelected(Number(event.target.value))} aria-label={drawdown ? '낙폭 지점 검사' : '자본 지점 검사'} aria-valuetext={`${dateLabel(active?.timestamp, true)} ${format(active?.[field])}`} />
      </>}
    </div>
    <div className="chart-bottom"><span>{ready ? dateLabel(points[0].timestamp, period === '1D') : '첫 번째 스냅샷부터 내역이 시작됩니다'}</span><span>{ready ? dateLabel(points[Math.floor(points.length / 2)]?.timestamp, period === '1D') : ''}</span><span>{ready ? dateLabel(points.at(-1)?.timestamp, period === '1D') : 'KST'}</span></div>
  </section>
}

export function DailyChart({ data, returns = false }: { data: TradingDataState; returns?: boolean }) {
  const [period, setPeriod] = useState('14D')
  const [display, setDisplay] = useState('일별')
  const daily = data.snapshot?.dailyPerformance ?? []
  let points = daily.slice(-Number(period.slice(0, -1))).map(row => ({ date: row.date, value: returns ? row.returnPct : row.pnl }))
  if (returns && display === '월별') {
    const groups = new Map<string, Array<number | null>>()
    for (const row of daily) groups.set(row.date.slice(0, 7), [...(groups.get(row.date.slice(0, 7)) ?? []), row.returnPct])
    points = Array.from(groups, ([date, values]) => ({ date: `${date}-01`, value: values.every(available) ? (values.reduce<number>((total, value) => total * (1 + (value as number) / 100), 1) - 1) * 100 : null }))
  }
  const ready = points.some(point => available(point.value))
  const max = Math.max(...points.map(point => available(point.value) ? Math.abs(point.value) : 0), .001)
  const summary = ready ? points.map(point => `${point.date}: ${returns ? percent(point.value) : money(point.value, true)}`).join('; ') : '아직 일별 내역이 없습니다'
  return <section className="trade-panel daily-panel" aria-label={returns ? '기간별 수익률' : '일별 PnL'}>
    <div className="panel-heading"><h2>{returns ? '기간별 수익률' : '일별 PnL'}</h2>{returns ? <Segmented label="수익률 집계" options={['일별', '월별']} value={display} onChange={setDisplay} /> : <Segmented label="일별 PnL 기간" options={['7D', '14D', '30D']} value={period} onChange={setPeriod} />}</div>
    <p className="panel-caption">{returns ? (display === '월별' ? '복리 수익률 · 유효한 날만' : '일별 자본 변동 · 최근 14일') : '수수료 차감 후 순이익·손실'}</p>
    <div className="daily-stage">{!ready ? <EmptyState data={data} title="아직 일별 내역이 없습니다" compact /> : <svg viewBox="0 0 360 164" role="img" aria-label={summary} preserveAspectRatio="none"><title>{summary}</title><line x1="0" x2="360" y1="80" y2="80" className="chart-grid-line" />{points.map((point, i) => {
      if (!available(point.value)) return null
      const height = Math.abs(point.value) / max * 62
      return <rect key={point.date} x={i * 354 / points.length + 3} y={point.value >= 0 ? 80 - height : 80} width={Math.min(30, 354 / points.length - 7)} height={Math.max(height, 1)} rx="2" fill={point.value < 0 ? 'var(--trade-red)' : 'var(--trade-green)'} opacity=".85"><title>{point.date}: {returns ? percent(point.value) : money(point.value, true)}</title></rect>
    })}</svg>}</div>
    <div className="chart-bottom"><span>{ready ? dateLabel(points[0].date) : '내역 대기 중'}</span><span>{ready ? dateLabel(points.at(-1)?.date) : '—'}</span></div>
  </section>
}
