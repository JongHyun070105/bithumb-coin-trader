import { Construction } from 'lucide-react'

export function PlaceholderPage({ title, description }: { title: string; description: string }) {
  return <div className="page-stack"><header className="page-heading"><div><span className="eyebrow">Planned module</span><h1>{title}</h1><p>{description}</p></div></header><section className="placeholder"><Construction size={28} /><span className="eyebrow">UI skeleton reserved</span><h2>Coming later</h2><p>This module will connect only after its backend evidence and safety gates are ready.</p></section></div>
}
