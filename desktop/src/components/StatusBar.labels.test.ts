import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const src = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), 'StatusBar.tsx'),
  'utf8',
)

describe('StatusBar keeps internals off the strip', () => {
  it('does not render memory counts, organism mood or metabolism tiers as alerts', () => {
    const alerts = src.slice(src.indexOf('const bits: string[] = []'), src.indexOf('setAlerts(bits'))
    expect(alerts).not.toMatch(/ mem`/)
    expect(alerts).not.toMatch(/soma\.label/)
    expect(alerts).not.toMatch(/tier_label|`L\$\{/)
    expect(alerts).not.toMatch(/EU \$\{/)
    // Actionable bits stay.
    expect(alerts).toContain('approve')
    expect(alerts).toContain('active_goal')
  })

  it('only shows the vision dock while installing or on error', () => {
    expect(src).not.toContain("'Vision ready'")
    expect(src).not.toContain("'Vision idle'")
    expect(src).not.toContain("'Vision setup pending'")
    const show = src.slice(src.indexOf('const show ='), src.indexOf('return { line, pct, phase, busy, show }'))
    expect(show).toContain('busy')
    expect(show).toContain("phase === 'error'")
    expect(show).not.toContain('vision.installed')
  })
})
