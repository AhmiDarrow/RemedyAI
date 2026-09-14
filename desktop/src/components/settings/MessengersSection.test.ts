import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const src = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), 'MessengersSection.tsx'),
  'utf8',
)

describe('MessengersSection denied inbound', () => {
  it('loads refused inbound from /api/status and paints it', () => {
    expect(src).toContain('getRuntimeStatus')
    expect(src).toContain('denied_inbound')
    expect(src).toContain('data-testid="denied-inbound"')
  })
})
