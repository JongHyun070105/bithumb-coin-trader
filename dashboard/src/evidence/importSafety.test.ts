import { it, expect, vi } from 'vitest'
import { File as NodeFile } from 'node:buffer'
import { parseRawJsonToArtifact, MAX_FILE_SIZE } from './artifactClassifier'
import { importFile } from './importFile'
import { evidenceReducer, initialState } from '../context/evidenceContextDef'
const parse = (text: string) =>
  parseRawJsonToArtifact('x', 'evil_contract.json', text.length, '', text)
for (const input of [
  '',
  '{',
  '[]',
  '42',
  'null',
  'true',
  '{"a":1,"a":2}',
  '['.repeat(66) + '0' + ']'.repeat(66),
])
  it('rejects ' + input.slice(0, 24), () =>
    expect(parse(input).parseStatus).not.toBe('SUCCESS'),
  )
it('oversize is rejected before any read', async () => {
  const read = vi.fn()
  await expect(
    importFile(
      { size: MAX_FILE_SIZE + 1, arrayBuffer: read } as unknown as File,
      'x',
    ),
  ).rejects.toThrow('10 MiB')
  expect(read).not.toHaveBeenCalled()
})
it('empty file is rejected before read', async () => {
  const read = vi.fn()
  await expect(
    importFile({ size: 0, arrayBuffer: read } as unknown as File, 'x'),
  ).rejects.toThrow('Empty')
  expect(read).not.toHaveBeenCalled()
})
it('invalid UTF8 is refused', async () =>
  await expect(
    importFile(
      new NodeFile([new Uint8Array([0xff])], 'bad.json') as unknown as File,
      'x',
    ),
  ).rejects.toThrow())
it('UTF8 BOM is refused', async () =>
  await expect(
    importFile(
      new NodeFile(
        [new Uint8Array([239, 187, 191, 123, 125])],
        'bom.json',
      ) as unknown as File,
      'x',
    ),
  ).rejects.toThrow('BOM'))
it('unknown schema cannot become trusted', () =>
  expect(
    parse('{"schema_version":999,"contract_type":"OFFICIAL_72H_SOAK_CONTRACT"}')
      .validationLevel,
  ).toBe('RECOGNIZED_INVALID'))
it('import clears demo, including demo events and streams', () => {
  const demo = evidenceReducer(initialState, { type: 'LOAD_DEMO' })
  const imported = parse('{"a":1}')
  const next = evidenceReducer(demo, {
    type: 'ADD_ARTIFACTS',
    payload: [imported],
  })
  expect(next.mode).toBe('IMPORTED_EVIDENCE')
  expect(next.artifacts).toEqual([imported])
  expect(next.streams).toEqual([])
  expect(next.projectSummary.realDqStatus).toBe('NOT RUN')
})
it('same filename never overwrites an authoritative candidate', () => {
  const one = parse('{"contract_type":"OFFICIAL_72H_SOAK_CONTRACT"}')
  const next = evidenceReducer(initialState, {
    type: 'ADD_ARTIFACTS',
    payload: [one, { ...one, id: 'two' }],
  })
  expect(next.artifacts).toHaveLength(2)
  expect(next.chainEvaluation.overallState).toBe('AMBIGUOUS_EVIDENCE')
})
