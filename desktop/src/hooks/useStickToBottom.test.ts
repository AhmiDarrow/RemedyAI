import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const src = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), 'useStickToBottom.ts'),
  'utf8',
)

describe('useStickToBottom send re-pin', () => {
  const reattach = src.indexOf('prevReattachRef.current = reattachKey')
  // The effect that owns the reattach line: last hook opener before it.
  const effectOpener = (() => {
    const head = src.slice(0, reattach)
    const layout = head.lastIndexOf('useLayoutEffect(() => {')
    const plain = head.lastIndexOf('useEffect(() => {')
    return layout > plain ? 'useLayoutEffect' : 'useEffect'
  })()

  it('re-attaches on reattachKey in useLayoutEffect so Enter pins before paint', () => {
    expect(reattach).toBeGreaterThan(-1)
    // Post-paint useEffect reattach is the race that left Jump to latest up.
    expect(effectOpener).toBe('useLayoutEffect')
  })

  it('shares one instant pin between Enter and the Jump pill', () => {
    expect(src).toContain('const jumpLatest = pinNow')
    const body = src.slice(reattach, src.indexOf('}, [reattachKey'))
    expect(body).toContain('pinNow()')
    expect(body).not.toContain('scrollTop =')
  })

  it('delegates follow/pause decisions to the pure controller', () => {
    expect(src).toContain("from './followController'")
    expect(src).toContain('shouldPin(stateRef.current)')
    // No ad-hoc distance checks left in the hook itself.
    expect(src).not.toMatch(/scrollHeight - el\.scrollTop - el\.clientHeight/)
  })

  it('never moves focus when End jumps to latest', () => {
    const end = src.slice(src.indexOf("e.key !== 'End'"), src.indexOf('Owner sent a prompt'))
    expect(end).toContain('isTypingTarget')
    expect(end).not.toContain('.focus(')
  })
})
