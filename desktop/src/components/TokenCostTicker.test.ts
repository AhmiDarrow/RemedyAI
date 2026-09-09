/**
 * The ticker's formatting rules are tested for real in
 * `src/utils/tokenCost.test.ts` (`formatCacheUsage`). Rendering needs a DOM,
 * which this project's vitest does not have, so what is pinned here is that
 * the component reaches for that formatter and shows cache reads next to the
 * token counts rather than only behind the expander.
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const src = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), 'TokenCostTicker.tsx'),
  'utf8',
)

describe('usage ticker', () => {
  it('reads cache counts off the run usage', () => {
    expect(src).toContain('formatCacheUsage')
    expect(src).toContain('run?.cache_read_tokens')
  })

  it('shows cache reads on the collapsed row, not only when expanded', () => {
    const collapsedRow = src.indexOf('{formatTokens(displayTok)} tok')
    const chip = src.indexOf('{cacheRead > 0 && (')
    const expander = src.indexOf('{expanded && (')
    expect(collapsedRow).toBeGreaterThan(-1)
    expect(chip).toBeGreaterThan(collapsedRow)
    expect(chip).toBeLessThan(expander)
  })

  it('keeps in/out and adds a cache line to the details', () => {
    const details = src.indexOf('{expanded && (')
    expect(src.indexOf('in {formatTokens(run?.prompt_tokens ?? 0)}', details)).toBeGreaterThan(details)
    expect(src.indexOf('Prompt cache', details)).toBeGreaterThan(details)
  })

  it('draws nothing about caching when the provider reports none', () => {
    // `cacheRead > 0` and a truthy `cacheLine` gate both surfaces.
    expect(src).toContain('{cacheRead > 0 && (')
    expect(src).toContain('{cacheLine && (')
  })
})
