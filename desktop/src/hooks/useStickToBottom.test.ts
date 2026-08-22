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

  it('does not blanket-ignore scroll events after an instant pin', () => {
    // Scrollbar drag / touch emit only `scroll`; a blanket lock swallows them.
    const onScroll = src.slice(src.indexOf('const onScroll'), src.indexOf('const onWheel'))
    expect(onScroll).toMatch(/instantPinUntilRef[\s\S]*distanceFromBottom\(el\) <= NEAR_PX/)
    const pin = src.slice(src.indexOf('const pinNow'), src.indexOf('useEffect(() => {'))
    expect(pin).not.toContain('lockUntilRef')
  })
})
