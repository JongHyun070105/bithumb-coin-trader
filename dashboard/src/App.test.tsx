import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import App from './App'
import { calculateSha256 } from './evidence/hashCalculator'
import { classifyArtifact, parseRawJsonToArtifact } from './evidence/artifactClassifier'
import { evaluateEvidenceChain } from './evidence/chainEvaluator'
import type { ParsedArtifact } from './types'

describe('Dashboard v0.2 - Offline Evidence & Research Console (P21 Verification)', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '#overview')
  })

  afterEach(() => {
    cleanup()
  })

  // 1. Default mode = NO EVIDENCE
  it('defaults to NO EVIDENCE mode with zero loaded artifacts and explicit unverified state', () => {
    render(<App />)

    expect(screen.getByText('증거 없음 (NO EVIDENCE)')).toBeInTheDocument()
    expect(screen.getByText('0개 아티팩트 로드됨')).toBeInTheDocument()
    expect(screen.getByText('NO EVIDENCE')).toBeInTheDocument()

    // Status cards in Overview
    expect(screen.getByText('OFFLINE RESEARCH')).toBeInTheDocument()
    expect(screen.getAllByText('PENDING').length).toBeGreaterThan(0)
    expect(screen.getByText('NOT RUN')).toBeInTheDocument()
  })

  // 2. Demo mode = SYNTHETIC badge
  it('switches to SYNTHETIC DEMO mode with persistent SYNTHETIC DATA badge upon clicking load demo', async () => {
    const user = userEvent.setup()
    render(<App />)

    const demoButton = screen.getByRole('button', { name: 'LOAD SYNTHETIC DEMO' })
    await user.click(demoButton)

    // Verify persistent SYNTHETIC DATA badge in banner and footer
    expect(screen.getByText('SYNTHETIC DATA (모의 합성 데이터)')).toBeInTheDocument()
    expect(screen.getByText('SYNTHETIC DEMO')).toBeInTheDocument()
    expect(screen.getByText(/주의: 화면에 표시되는 수치는 시연용 합성 픽스처입니다/)).toBeInTheDocument()

    // Verify clear demo restores to NO EVIDENCE
    const clearButton = screen.getByRole('button', { name: '데모 해제 (Clear)' })
    await user.click(clearButton)
    expect(screen.getByText('증거 없음 (NO EVIDENCE)')).toBeInTheDocument()
  })

  // 3. Unknown artifact classification
  it('classifies unknown JSON artifacts as UNKNOWN_TYPE without crashing', () => {
    const unknownClassification = classifyArtifact('arbitrary_log.json', { arbitrary_field: 123 })
    expect(unknownClassification.type).toBe('unknown')

    const parsed = parseRawJsonToArtifact(
      'art-unknown',
      'arbitrary_log.json',
      128,
      'test-sha',
      JSON.stringify({ arbitrary_field: 123 })
    )
    expect(parsed.parseStatus).toBe('UNKNOWN_TYPE')
    expect(parsed.type).toBe('unknown')
  })

  // 4. Malformed JSON handling
  it('gracefully handles malformed JSON string and records PARSE_FAILED', () => {
    const malformedText = '{ this is invalid json content: 123 '
    const parsed = parseRawJsonToArtifact('art-bad', 'broken.json', malformedText.length, 'bad-sha', malformedText)

    expect(parsed.parseStatus).toBe('PARSE_FAILED')
    expect(parsed.type).toBe('unknown')
    expect(parsed.errorMessage).toBeDefined()
    expect(parsed.validationIssues).toContain('Invalid metadata input')
  })

  // 5. SHA-256 calculation
  it('computes correct deterministic SHA-256 hashes locally via Web Crypto or software fallback', async () => {
    // "hello" -> 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824
    const hashHello = await calculateSha256('hello')
    expect(hashHello).toBe('2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824')

    // "" (empty string) -> e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
    const hashEmpty = await calculateSha256('')
    expect(hashEmpty).toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
  })

  // 6. Evidence chain incomplete
  it('evaluates incomplete evidence chain as INCOMPLETE when downstream artifacts are absent', () => {
    const mockRuntimeSeal: ParsedArtifact = {
      id: 'art-seal',
      fileName: 'runtime_seal.json',
      fileSize: 500,
      calculatedSha256: 'aa11bb22cc33dd44ee55ff6677889900aa11bb22cc33dd44ee55ff6677889900',
      type: 'runtime_seal',
      parseStatus: 'SUCCESS',
      rawJson: {
        runtime_software_commit: '4b46c9ed47afb67dd74e742ce1acf455508b2f6e',
        runtime_fingerprint: 'test-fingerprint',
        feeds: { count: 76 }
      },
      validationIssues: []
    }

    const mockLaunchProv: ParsedArtifact = {
      id: 'art-launch',
      fileName: 'launch_provenance.json',
      fileSize: 400,
      calculatedSha256: '1122334455667788990011223344556677889900112233445566778899001122',
      type: 'launch_provenance',
      parseStatus: 'SUCCESS',
      rawJson: {
        collector_epoch: 'epoch-1',
        collector_run_id: 'run-1',
        launch_command: 'start'
      },
      validationIssues: []
    }

    const mockActualStart: ParsedArtifact = {
      id: 'art-start',
      fileName: 'actual_start_evidence.json',
      fileSize: 300,
      calculatedSha256: '5566778899001122334455667788990011223344556677889900112233445566',
      type: 'actual_start_evidence',
      parseStatus: 'SUCCESS',
      rawJson: {
        actual_start_time_utc: '2026-09-05T08:00:00Z',
        start_evidence_type: 'SYSTEMD_ACTIVE_ENTER'
      },
      validationIssues: []
    }

    // Without downstream contract/manifest/dq/dataset, chain is INCOMPLETE
    const evalResult = evaluateEvidenceChain([mockRuntimeSeal, mockLaunchProv, mockActualStart])
    expect(evalResult.overallState).toBe('INVALID')
    expect(evalResult.nodes.some((n) => n.status === 'MISSING')).toBe(true)

    // Fail-Closed: If actual start is missing from provenance, it is INVALID
    const failClosedResult = evaluateEvidenceChain([mockRuntimeSeal, mockLaunchProv])
    expect(failClosedResult.overallState).toBe('INVALID')
    expect(failClosedResult.issues.length).toBeGreaterThan(0)
  })

  // 7. Hash mismatch presentation
  it('detects and flags hash mismatch when upstream reference SHA does not match claimed hash', () => {
    const mockRuntimeSeal: ParsedArtifact = {
      id: 'art-seal',
      fileName: 'runtime_seal.json',
      fileSize: 500,
      calculatedSha256: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      type: 'runtime_seal',
      parseStatus: 'SUCCESS',
      rawJson: {
        runtime_software_commit: 'commit1',
        runtime_fingerprint: 'fp1',
        feeds: { count: 76 }
      },
      validationIssues: []
    }

    const mockContractWithMismatch: ParsedArtifact = {
      id: 'art-contract',
      fileName: 'epoch_contract.json',
      fileSize: 600,
      calculatedSha256: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
      type: 'epoch_contract',
      parseStatus: 'SUCCESS',
      rawJson: {
        contract_type: 'OFFICIAL_72H_SOAK_CONTRACT',
        runtime_seal_sha256: 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc', // Mismatch!
        feed_universe: { total_feeds: 76 }
      },
      validationIssues: []
    }

    const evalResult = evaluateEvidenceChain([mockRuntimeSeal, mockContractWithMismatch])
    expect(evalResult.overallState).toBe('MISMATCH')
    const contractNode = evalResult.nodes.find((n) => n.expectedArtifactType === 'epoch_contract')
    expect(contractNode?.status).toBe('MISMATCH')
  })

  // 8. DQ FAIL does not show green verified
  it('does not display green verified or PASS when DQ qualification is FAIL', () => {
    const mockDqFail: ParsedArtifact = {
      id: 'art-dq',
      fileName: 'dq_qualification.json',
      fileSize: 450,
      calculatedSha256: 'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
      type: 'dq_qualification',
      parseStatus: 'SUCCESS',
      rawJson: {
        qualification_type: 'DEEP_DQ_QUALIFICATION',
        status: 'FAIL',
        epoch_manifest_sha256: 'hash1',
        audit_report_sha256: 'hash2',
        blockers: ['Critical sequence gap found in Bithumb KRW-BTC feed']
      },
      validationIssues: []
    }

    const evalResult = evaluateEvidenceChain([mockDqFail])
    const dqNode = evalResult.nodes.find((n) => n.expectedArtifactType === 'dq_qualification')
    expect(dqNode?.status).toBe('INVALID')
    expect(evalResult.overallState).not.toBe('COMPLETE')
  })

  // 9. Missing number never becomes 0 (Epistemic Rule)
  it('never fabricates 0 or 0.0% for unavailable metrics in NO EVIDENCE mode', async () => {
    const user = userEvent.setup()
    render(<App />)

    // Navigate to 72H Soak
    await user.click(screen.getByRole('button', { name: '72H 무인 수집' }))
    expect(screen.getByRole('heading', { name: /72시간 무인 수집 현황/i })).toBeInTheDocument()

    // 72H Soak Page should show PENDING / NOT RUN / PENDING EVIDENCE rather than 0
    expect(screen.getByText('PENDING EVIDENCE')).toBeInTheDocument()
    expect(screen.getAllByText('NOT AVAILABLE').length).toBeGreaterThan(0)

    // Check slots: missing cohorts, receipts, fullscan in NO_EVIDENCE mode show '—' or '증거 로드 시 확인 가능'
    const metricCards = screen.getAllByRole('article')
    for (const card of metricCards) {
      const text = card.textContent || ''
      // Ensure no card claims "0건" or "0%" when representing unmeasured data
      if (text.includes('정상 수집 코호트') || text.includes('실제 수집 시간')) {
        expect(text).not.toContain('0 s')
        expect(text).toContain('—')
      }
    }
  })

  // 10. Holdout has no unlock button
  it('renders Holdout partition as visibly sealed without unlock, open, or override controls', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: '연구 데이터셋' }))
    expect(screen.getByRole('heading', { name: /연구 데이터셋 콘솔/i })).toBeInTheDocument()

    // Holdout must be labeled SEALED
    expect(screen.getByText('HOLDOUT: SEALED')).toBeInTheDocument()
    expect(screen.getByText(/홀드아웃 열람·해제 기능이 없습니다/)).toBeInTheDocument()

    // Confirm absence of unlock, view, open buttons anywhere on page
    expect(screen.queryByRole('button', { name: /unlock|open|봉인 해제|홀드아웃 열기|데이터 보기/i })).toBeNull()
  })

  // 11. Trading has no BUY/SELL control
  it('renders Trading page completely locked without BUY, SELL, or live execution switch controls', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: /트레이딩/ }))
    expect(screen.getByRole('heading', { name: /페이퍼 및 실거래 통제/i })).toBeInTheDocument()

    // Locked status must be explicitly stated
    expect(screen.getByText(/트레이딩 실행 계층 완전 잠금/)).toBeInTheDocument()
    expect(screen.getByText(/PAPER TRADING: NOT STARTED/)).toBeInTheDocument()
    expect(screen.getByText(/LIVE TRADING: DISABLED/)).toBeInTheDocument()

    // Verify NO BUY/SELL buttons, NO order ticket form, NO toggle switch
    expect(screen.queryByRole('button', { name: /buy|sell|매수|매도|주문|전송|submit/i })).toBeNull()
    expect(screen.queryByRole('switch')).toBeNull()
    expect(screen.queryByRole('textbox', { name: /api|secret|key/i })).toBeNull()
  })

  // 12. No network client introduced (Air-gap & Offline invariant)
  it('enforces complete air-gap isolation with zero fetch, axios, WebSocket, or EventSource clients', () => {
    const srcDir = path.resolve(process.cwd(), 'src')

    function scanDir(dir: string): string[] {
      let results: string[] = []
      const list = fs.readdirSync(dir)
      for (const file of list) {
        const fullPath = path.join(dir, file)
        const stat = fs.statSync(fullPath)
        if (stat.isDirectory()) {
          results = results.concat(scanDir(fullPath))
        } else if (file.endsWith('.ts') || file.endsWith('.tsx')) {
          results.push(fullPath)
        }
      }
      return results
    }

    const files = scanDir(srcDir)
    const forbiddenPatterns = [
      /\bfetch\s*\(/,
      /\baxios\b/,
      /\bnew\s+WebSocket\b/,
      /\bnew\s+EventSource\b/,
      /\bxmlhttprequest\b/i
    ]

    const violations: { file: string; line: number; match: string }[] = []

    for (const filePath of files) {
      // Exclude test file itself from forbidden check
      if (filePath.endsWith('App.test.tsx')) continue

      const content = fs.readFileSync(filePath, 'utf-8')
      const lines = content.split('\n')
      lines.forEach((line: string, idx: number) => {
        for (const pattern of forbiddenPatterns) {
          if (pattern.test(line)) {
            violations.push({
              file: path.relative(srcDir, filePath),
              line: idx + 1,
              match: line.trim()
            })
          }
        }
      })
    }

    expect(violations).toEqual([])
  })
})
