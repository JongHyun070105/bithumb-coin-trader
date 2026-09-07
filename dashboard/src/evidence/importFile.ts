import { MAX_FILE_SIZE, parseRawJsonToArtifact } from './artifactClassifier'
import { calculateSha256 } from './hashCalculator'
export async function importFile(file: File, id: string) {
  if (file.size > MAX_FILE_SIZE)
    throw Error('Metadata exceeds 10 MiB; RAW import skipped')
  if (file.size === 0) throw Error('Empty metadata file')
  const bytes = await file.arrayBuffer()
  const text = new TextDecoder('utf-8', {
    fatal: true,
    ignoreBOM: true,
  }).decode(bytes)
  if (text.charCodeAt(0) === 0xfeff)
    throw Error('UTF-8 BOM is unsupported; export plain UTF-8')
  const a = parseRawJsonToArtifact(id, file.name, file.size, '', text)
  a.calculatedSha256 = await calculateSha256(bytes)
  return a
}
