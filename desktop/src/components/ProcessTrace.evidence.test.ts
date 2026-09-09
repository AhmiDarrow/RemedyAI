/**
 * The evidence panel's *behaviour* (fetch on open, image rendering) needs a
 * DOM: this project's vitest runs in `node` with no jsdom and no React test
 * renderer, so a rendering assertion here would assert nothing. The fetch
 * policy and the bounded cache are covered for real in
 * `src/api/turnEvidence.test.ts`; what is left to pin is the wiring — that the
 * trail fetches only on open, never on render, and that images go through the
 * authenticated media path rather than a bare <img src>.
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(here, 'ProcessTrace.tsx'), 'utf8')
const feed = readFileSync(join(here, 'MessageFeed.tsx'), 'utf8')

describe('process trail evidence', () => {
  it('fetches the recorded result only from the open handler', () => {
    expect(src).toContain('fetchToolEvidence')
    const inToggle = src.indexOf('const toggleEvidence')
    const fetchAt = src.indexOf('void fetchToolEvidence(')
    const toggleEnd = src.indexOf('[evidenceRefOf],', inToggle)
    expect(inToggle).toBeGreaterThan(-1)
    expect(fetchAt).toBeGreaterThan(inToggle)
    expect(fetchAt).toBeLessThan(toggleEnd)
    // Exactly one call site, and it is that one.
    expect(src.match(/fetchToolEvidence\(/g)).toHaveLength(1)
    // Opening a row that is already resident must not re-request it.
    expect(src).toContain('if (peekToolEvidence(ref.sid, ref.rid, ref.cid)) return')
  })

  it('holds no bodies in component state — only ids', () => {
    expect(src).toContain('useState<Set<string>>(() => new Set())')
    expect(src).not.toMatch(/useState<[^>]*ToolEvidence[^>]*>/)
    expect(src).toContain('peekToolEvidence(')
  })

  it('renders recorded images through the authenticated media resolver', () => {
    expect(src).toContain("import { ChatImage } from './ChatImage'")
    expect(src).toContain('<ChatImage src={img.url}')
  })

  it('is addressable only when the step names its turn and call', () => {
    expect(src).toContain('if (!sessionId || !step.requestId || !step.callId) return null')
  })

  it('the live trail is given the session so its rows are addressable', () => {
    const live = feed.indexOf('steps={processSteps}')
    expect(live).toBeGreaterThan(-1)
    expect(feed.slice(live, live + 200)).toContain('sessionId={sessionId}')
  })
})
