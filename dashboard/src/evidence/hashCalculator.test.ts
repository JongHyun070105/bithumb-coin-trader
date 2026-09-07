import { it, expect } from 'vitest'
import { createHash } from 'node:crypto'
import { sha256Text } from './hashCalculator'
for (const n of [0, 1, 3, 55, 56, 63, 64, 65, 119, 120, 1000])
  it('SHA fallback byte length ' + n, () => {
    const t = 'x'.repeat(n)
    expect(sha256Text(t)).toBe(createHash('sha256').update(t).digest('hex'))
  })
