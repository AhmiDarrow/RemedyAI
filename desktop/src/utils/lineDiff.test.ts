import { describe, expect, it } from 'vitest'
import { unifiedLineDiff, unifiedNewFile } from './lineDiff'
import { looksLikeUnifiedDiff } from './diffHighlight'
import { formatToolArgsDisplay } from './toolProcessFormat'

describe('lineDiff', () => {
  it('marks pure additions for new files', () => {
    const d = unifiedNewFile('a.txt', 'hello\nworld')
    expect(d).toContain('--- /dev/null')
    expect(d).toContain('+hello')
    expect(d).toContain('+world')
    expect(looksLikeUnifiedDiff(d)).toBe(true)
  })

  it('shows red/green style lines for edits', () => {
    const d = unifiedLineDiff('one\ntwo\nthree', 'one\nTWO\nthree', {
      oldLabel: 'a',
      newLabel: 'b',
    })
    expect(d).toContain('-two')
    expect(d).toContain('+TWO')
    expect(d).toContain(' one')
    expect(looksLikeUnifiedDiff(d)).toBe(true)
  })
})

describe('formatToolArgsDisplay', () => {
  it('synthesizes new-file diff for file_write JSON', () => {
    const map = new Map<string, string>()
    const f = formatToolArgsDisplay(
      'file_write',
      JSON.stringify({ path: 'C:\\\\Desktop\\\\x.txt', content: 'alpha\nbeta' }),
      map,
    )
    expect(f.className).toBe('language-diff')
    expect(f.text).toContain('+alpha')
    expect(f.text).toContain('+beta')
    expect(f.caption).toMatch(/New file/i)
  })

  it('diffs second write to same path', () => {
    const map = new Map<string, string>()
    formatToolArgsDisplay(
      'file_write',
      JSON.stringify({ path: '/tmp/a.txt', content: 'old line\nstay' }),
      map,
    )
    const f2 = formatToolArgsDisplay(
      'file_write',
      JSON.stringify({ path: '/tmp/a.txt', content: 'new line\nstay' }),
      map,
    )
    expect(f2.caption).toMatch(/Edit/i)
    expect(f2.text).toContain('-old line')
    expect(f2.text).toContain('+new line')
    expect(f2.text).toContain(' stay')
  })
})
