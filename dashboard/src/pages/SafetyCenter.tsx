import { LockKeyhole, ShieldAlert } from 'lucide-react'
import { StatusBadge } from '../components/StatusBadge'
import { safetyChecks } from '../data/mockData'

export function SafetyCenter() {
  return (
    <div className="page-stack">
      <header className="page-heading"><div><span className="eyebrow">Read-only controls</span><h1>Safety Center</h1><p>Hard gates for data, research, paper, and live promotion.</p></div><StatusBadge value="LOCKED" /></header>
      <section className="safety-banner"><div className="safety-icon"><ShieldAlert size={26} /></div><div><span className="eyebrow">Global safety posture</span><h2>LIVE TRADING DISABLED</h2><p>This interface cannot enable entries, connect an account, or submit orders.</p></div><LockKeyhole size={26} /></section>
      <section className="safety-grid" aria-label="Safety checks">{safetyChecks.map((check) => <article className="safety-card" key={check.label}><div className="safety-card-top"><span>{check.label}</span><StatusBadge value={check.status} /></div><strong>{check.value}</strong><p>{check.detail}</p></article>)}</section>
      <p className="read-only-note"><LockKeyhole size={14} /> Read-only surface. No safety override controls are implemented.</p>
    </div>
  )
}
