import React, { useState, useRef } from 'react'
import { UploadCloud, Shield, AlertTriangle, FileCheck2 } from 'lucide-react'
import { useEvidence } from '../context/useEvidence'
import { importFile } from '../evidence/importFile'
import type { ParsedArtifact } from '../types'

export const EvidenceDropZone: React.FC = () => {
  const { addArtifacts } = useEvidence()
  const [isDragging, setIsDragging] = useState(false)
  const [processing, setProcessing] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    setProcessing(true)
    setErrorMessage(null)

    const parsedList: ParsedArtifact[] = []
    const MAX_SIZE = 10 * 1024 * 1024 // 10MB limit warning

    try {
      for (let i = 0; i < files.length; i++) {
        const file = files[i]
        if (file.size > MAX_SIZE) {
          setErrorMessage(
            `경고: '${file.name}' (${(file.size / 1024 / 1024).toFixed(1)}MB) 파일이 너무 큽니다. 이 뷰어는 수 기가바이트의 RAW 데이터가 아닌 메타데이터 아티팩트(JSON) 전용입니다.`,
          )
          continue
        }

        const parsed = await importFile(file, `art-${crypto.randomUUID()}`)
        parsedList.push(parsed)
      }

      if (parsedList.length > 0) {
        addArtifacts(parsedList)
      }
    } catch (err) {
      setErrorMessage(
        err instanceof Error
          ? err.message
          : '파일 로드 중 오류가 발생했습니다.',
      )
    } finally {
      setProcessing(false)
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
    }
  }

  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(true)
  }

  const onDragLeave = () => {
    setIsDragging(false)
  }

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
    handleFiles(e.dataTransfer.files)
  }

  return (
    <div className="evidence-dropzone-wrapper">
      <div
        className={`evidence-dropzone ${isDragging ? 'dragging' : ''} ${processing ? 'processing' : ''}`}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            fileInputRef.current?.click()
          }
        }}
        onClick={() => fileInputRef.current?.click()}
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".json"
          style={{ display: 'none' }}
          onChange={(e) => handleFiles(e.target.files)}
        />
        <div className="dropzone-icon">
          {processing ? (
            <FileCheck2 size={32} className="spin-slow" />
          ) : (
            <UploadCloud size={32} />
          )}
        </div>
        <div className="dropzone-content">
          <strong>증거 아티팩트 파일 드래그 앤 드롭 또는 클릭하여 선택</strong>
          <span>
            {processing
              ? '로컬 SHA-256 계산 및 스키마 분석 중...'
              : '다중 JSON 파일 선택 지원 (runtime.json, launch-provenance.json, contract, manifest, deep audit, DQ report 등)'}
          </span>
        </div>
      </div>

      <div className="dropzone-notice">
        <Shield size={14} className="icon-shield" />
        <span>
          파일은 브라우저 로컬에서만 처리되며 서버로 업로드되지 않습니다.
          (Client-side Web Crypto SHA-256)
        </span>
      </div>

      {errorMessage && (
        <div className="dropzone-error">
          <AlertTriangle size={15} />
          <span>{errorMessage}</span>
        </div>
      )}
    </div>
  )
}
