import React from 'react'
import { useEvidence } from '../context/useEvidence'
import { Database, FileSpreadsheet, Trash2 } from 'lucide-react'

export const ModeBanner: React.FC = () => {
  const { mode, loadDemo, clearEvidence, artifacts } = useEvidence()

  return (
    <div className={`mode-banner banner-${mode.toLowerCase()}`}>
      <div className="banner-left">
        {mode === 'NO_EVIDENCE' && (
          <div className="banner-content">
            <span className="mode-pill pill-neutral">증거 없음 (NO EVIDENCE)</span>
            <span className="banner-desc">
              현재 로컬 메모리에 로드된 증거 아티팩트가 없습니다. 실제 72H 소크 증거를 드롭하거나 합성 데모를 로드하십시오.
            </span>
          </div>
        )}

        {mode === 'SYNTHETIC_DEMO' && (
          <div className="banner-content">
            <span className="mode-pill pill-synthetic">
              <Database size={13} />
              SYNTHETIC DATA (모의 합성 데이터)
            </span>
            <span className="banner-desc">
              주의: 화면에 표시되는 수치는 시연용 합성 픽스처입니다. 실제 프로덕션 72H 소크 결과가 아닙니다.
            </span>
          </div>
        )}

        {mode === 'IMPORTED_EVIDENCE' && (
          <div className="banner-content">
            <span className="mode-pill pill-imported">
              <FileSpreadsheet size={13} />
              로컬 증거 임포트 모드 ({artifacts.length}개 파일)
            </span>
            <span className="banner-desc">
              브라우저 로컬 메모리에서 파싱된 증거 아티팩트를 시각화 중입니다 (외부 서버 업로드 0건).
            </span>
          </div>
        )}
      </div>

      <div className="banner-actions">
        {mode !== 'SYNTHETIC_DEMO' ? (
          <button className="btn-action btn-demo" onClick={loadDemo} title="72H 소크 파이프라인 시연용 합성 데이터 로드">
            <Database size={14} />
            <span>LOAD SYNTHETIC DEMO</span>
          </button>
        ) : (
          <button className="btn-action btn-clear" onClick={clearEvidence} title="합성 데모 해제 및 초기화">
            <Trash2 size={14} />
            <span>데모 해제 (Clear)</span>
          </button>
        )}

        {mode === 'IMPORTED_EVIDENCE' && (
          <button className="btn-action btn-clear" onClick={clearEvidence} title="임포트된 증거 제거">
            <Trash2 size={14} />
            <span>증거 초기화</span>
          </button>
        )}
      </div>
    </div>
  )
}
