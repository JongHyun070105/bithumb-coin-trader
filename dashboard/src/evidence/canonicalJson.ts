// Python json.dumps(sort_keys=True, separators=(',', ':'), ensure_ascii=True).
// Parse tokens separately so integer precision and float-vs-int are not lost by JSON.parse.
const quote = (s: string) =>
  JSON.stringify(s).replace(
    /[\u007f-\uffff]/g,
    (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'),
  )
const compareKeys = (a: string, b: string) => {
  const x = Array.from(a, (c) => c.codePointAt(0)!),
    y = Array.from(b, (c) => c.codePointAt(0)!)
  for (let i = 0; i < Math.min(x.length, y.length); i++)
    if (x[i] !== y[i]) return x[i] - y[i]
  return x.length - y.length
}
export function canonicalJson(text: string, excluded: string[] = []): string {
  let i = 0
  const ws = () => {
    while (/\s/.test(text[i] ?? '') && i < text.length) i++
  }
  const string = () => {
    const start = i++
    while (i < text.length) {
      if (text[i++] === '"') return JSON.parse(text.slice(start, i)) as string
      if (text[i - 1] === '\\') i++
    }
    throw Error('Unterminated string')
  }
  const value = (depth: number): string => {
    if (depth > 64) throw Error('JSON nesting exceeds 64')
    ws()
    const c = text[i]
    if (c === '"') return quote(string())
    if (c === '{') {
      i++
      ws()
      const entries: [string, string][] = []
      const keys = new Set<string>()
      if (text[i] !== '}')
        while (true) {
          ws()
          if (text[i] !== '"') throw Error('Expected object key')
          const key = string()
          if (keys.has(key)) throw Error('Duplicate JSON key')
          keys.add(key)
          ws()
          if (text[i++] !== ':') throw Error('Expected colon')
          const v = value(depth + 1)
          if (depth !== 0 || !excluded.includes(key)) entries.push([key, v])
          ws()
          if (text[i] !== ',') break
          i++
        }
      if (text[i++] !== '}') throw Error('Expected object end')
      return (
        '{' +
        entries
          .sort((a, b) => compareKeys(a[0], b[0]))
          .map(([k, v]) => quote(k) + ':' + v)
          .join(',') +
        '}'
      )
    }
    if (c === '[') {
      i++
      ws()
      const items: string[] = []
      if (text[i] !== ']')
        while (true) {
          items.push(value(depth + 1))
          ws()
          if (text[i] !== ',') break
          i++
        }
      if (text[i++] !== ']') throw Error('Expected array end')
      return '[' + items.join(',') + ']'
    }
    const literal = /^(true|false|null)/.exec(text.slice(i))
    if (literal) {
      i += literal[0].length
      return literal[0]
    }
    const number = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(
      text.slice(i),
    )?.[0]
    if (!number) throw Error('Invalid JSON token')
    i += number.length
    if (!/[.eE]/.test(number)) return BigInt(number).toString()
    // Fail closed outside the exact Python-produced float spelling subset.
    // Never silently round a Python float into a different canonical number.
    const n = Number(number)
    if (!Number.isFinite(n)) throw Error('Non-finite number')
    let out: string
    if (Object.is(n, -0)) out = '-0.0'
    else if (n === 0) out = '0.0'
    else if (Math.abs(n) < 1e-4 || Math.abs(n) >= 1e16) {
      const [mantissa, exponent] = n.toExponential().split('e')
      const e = Number(exponent)
      out =
        mantissa +
        'e' +
        (e < 0 ? '-' : '+') +
        Math.abs(e).toString().padStart(2, '0')
    } else out = Number.isInteger(n) ? n.toString() + '.0' : n.toString()
    if (number !== out)
      throw Error(
        'Unsupported noncanonical float spelling; export using Python JSON',
      )
    return out
  }
  const result = value(0)
  ws()
  if (i !== text.length) throw Error('Trailing JSON data')
  return result
}
