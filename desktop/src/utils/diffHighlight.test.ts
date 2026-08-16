import { describe, expect, it } from 'vitest'
import {
  classifyDiffLine,
  looksLikeUnifiedDiff,
  shouldRenderAsDiff,
} from './diffHighlight'

describe('diffHighlight', () => {
  const sample = `--- a/foo.ts
+++ b/foo.ts
@@ -1,3 +1,4 @@
 keep
-remove me
+add me
 context
`

  it('detects unified diffs', () => {
    expect(looksLikeUnifiedDiff(sample)).toBe(true)
    expect(looksLikeUnifiedDiff('hello\nworld')).toBe(false)
    expect(looksLikeUnifiedDiff('- only one\nplain')).toBe(false)
  })

  it('classifies lines', () => {
    expect(classifyDiffLine('+add')).toBe('add')
    expect(classifyDiffLine('-del')).toBe('del')
    expect(classifyDiffLine('@@ -1 +1 @@')).toBe('hunk')
    expect(classifyDiffLine('--- a/x')).toBe('meta')
    expect(classifyDiffLine(' context')).toBe('ctx')
  })

  it('respects language-diff class', () => {
    expect(shouldRenderAsDiff('not a real diff', 'language-diff')).toBe(true)
    expect(shouldRenderAsDiff(sample, undefined)).toBe(true)
  })
})
