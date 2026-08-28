import { Construction } from 'lucide-react'

export function PlaceholderPage({ title, description }: { title: string; description: string }) {
  return <div className="page-stack"><header className="page-heading"><div><span className="eyebrow">예정 모듈</span><h1>{title}</h1><p>{description}</p></div></header><section className="placeholder"><Construction size={28} /><span className="eyebrow">UI 골격 준비됨</span><h2>추후 제공</h2><p>Backend 근거와 안전 게이트가 준비된 뒤에만 이 모듈을 연결합니다.</p></section></div>
}
