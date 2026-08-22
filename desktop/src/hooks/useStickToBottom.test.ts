import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const src = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), 'useStickToBottom.ts'),
  'utf8',
)

describe('useStickToBottom send re-pin', () => {
  it('re-attaches on reattachKey in useLayoutEffect so Enter pins before paint', () => {
    const layout = src.indexOf('useLayoutEffect(() => {')
    const reattach = src.indexOf('reattachKey === undefined')
    const effectOnly = src.indexOf('Owner sent a prompt')
    expect(layout).toBeGreaterThan(-1)
    expect(reattach).toBeGreaterThan(layout)
    expect(effectOnly).toBeGreaterThan(-1)
    // Post-paint useEffect reattach is the race that left Jump to latest up.
    expect(src).not.toMatch(
      /Owner sent something[\s\S]*useEffect\(\(\) => \{[\s\S]*reattachKey/,
    )
  })
})
