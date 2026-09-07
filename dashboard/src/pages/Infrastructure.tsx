import React from 'react'
import { Cloud, Server, HardDrive, ShieldCheck, Database, ArrowDown, Lock } from 'lucide-react'
import { ModeBanner } from '../components/ModeBanner'

export const Infrastructure: React.FC = () => {
  return (
    <div className="page-container">
      <ModeBanner />

      <div className="page-header">
        <div>
          <h2>인프라 아키텍처 및 토폴로지 (Infrastructure Console)</h2>
          <p className="page-subtitle">
            AWS 서울 리전 72시간 무인 수집 환경의 정적 아키텍처 및 보안 봉인 명세
          </p>
        </div>
        <div className="infra-label-badge">
          <Lock size={14} />
          <span>SEALED CONFIGURATION · NOT LIVE TELEMETRY</span>
        </div>
      </div>

      {/* Topology Architecture Diagram */}
      <section className="section-block">
        <h3 className="section-title">
          <Cloud size={18} />
          <span>데이터 파이프라인 수집 및 아카이브 토폴로지</span>
        </h3>

        <div className="card-surface topology-card">
          <div className="topo-node-grid">
            {/* Step 1: External Exchanges */}
            <div className="topo-stage">
              <div className="topo-stage-title">1. 외부 거래소 WebSocket (공개 시세)</div>
              <div className="topo-box-group">
                <div className="topo-box">빗썸 (KRW 20개 마켓)</div>
                <div className="topo-box">바이낸스 (USDT 4개 심볼)</div>
                <div className="topo-box">업비트 (KRW 4개 마켓)</div>
              </div>
            </div>

            <div className="topo-arrow"><ArrowDown size={20} /></div>

            {/* Step 2: EC2 Isolated Collector */}
            <div className="topo-stage">
              <div className="topo-stage-title">2. AWS EC2 격리 수집 인스턴스 (Seoul ap-northeast-2)</div>
              <div className="topo-box highlight">
                <Server size={18} />
                <div>
                  <strong>MultiExchangeMicrostructureCollector</strong>
                  <p className="muted-text">systemd 서비스 무인 자율 구동 (PID 격리 / JIT 번들 배포)</p>
                </div>
              </div>
            </div>

            <div className="topo-arrow"><ArrowDown size={20} /></div>

            {/* Step 3: Local Storage & Archive */}
            <div className="topo-stage">
              <div className="topo-stage-title">3. 로컬 고속 스토리지 & 롤링 아카이버</div>
              <div className="topo-box-group">
                <div className="topo-box">
                  <HardDrive size={16} />
                  <span>200 GiB gp3 EBS (WAL & RAW .zst)</span>
                </div>
                <div className="topo-box">
                  <Database size={16} />
                  <span>정시 롤링 아카이브 스케줄러 (매시간 영수증 봉인)</span>
                </div>
              </div>
            </div>

            <div className="topo-arrow"><ArrowDown size={20} /></div>

            {/* Step 4: Private S3 Backup */}
            <div className="topo-stage">
              <div className="topo-stage-title">4. 프라이빗 불변 객체 저장소</div>
              <div className="topo-box">
                <Cloud size={16} />
                <span>Private S3 Bucket (SSE-S3 암호화 / 외부 공개 차단)</span>
              </div>
            </div>

            <div className="topo-arrow"><ArrowDown size={20} /></div>

            {/* Step 5: Post-Soak Offline Processing */}
            <div className="topo-stage">
              <div className="topo-stage-title">5. 포스트소크 오프라인 검증 체계 (Post-Soak Offline Chain)</div>
              <div className="topo-box-group">
                <div className="topo-box">심층 DQ 전수 감사</div>
                <div className="topo-box">캐노니컬 나노초 변환</div>
                <div className="topo-box">연구 데이터셋 분할</div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Static Configuration Specs */}
      <section className="section-block">
        <h3 className="section-title">
          <ShieldCheck size={18} />
          <span>인프라 보안 및 스펙 동결 현황 (Sealed Infrastructure Specs)</span>
        </h3>

        <div className="status-grid">
          <div className="metric-card">
            <span className="metric-title">AWS REGION</span>
            <div className="metric-value">ap-northeast-2</div>
            <span className="metric-subtext">서울 리전 (지연시간 최소화)</span>
          </div>
          <div className="metric-card">
            <span className="metric-title">INSTANCE TYPE</span>
            <div className="metric-value">t3.medium</div>
            <span className="metric-subtext">2 vCPU / 4 GiB Memory</span>
          </div>
          <div className="metric-card">
            <span className="metric-title">ATTACHED STORAGE</span>
            <div className="metric-value">200 GiB gp3</div>
            <span className="metric-subtext">3,000 IOPS / 125 MB/s 전용 처리량</span>
          </div>
          <div className="metric-card">
            <span className="metric-title">SECURITY GROUP</span>
            <div className="metric-value">0 Inbound Ports</div>
            <span className="metric-subtext">외부 수신 포트 전면 차단 (SSM 전용)</span>
          </div>
        </div>
      </section>
    </div>
  )
}
