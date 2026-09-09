import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { approvalHeadline, approvalOriginLabel, bulkApprovable } from './approvalText'

describe('approval copy', () => {
  it('shows the origin channel in the headline when the item carries one', () => {
    expect(
      approvalHeadline({ summary: 'Remedy wants to run a shell command', reason: 'x', origin: 'telegram' }),
    ).toBe('Remedy wants to run a shell command · via Telegram')
    expect(approvalHeadline({ summary: 's', reason: 'r', channel: 'discord' })).toBe('s · via Discord')
  })

  it('falls back to the reason and omits origin when absent', () => {
    expect(approvalHeadline({ summary: '', reason: 'needs shell' })).toBe('needs shell')
    expect(approvalHeadline({ summary: 'ok', reason: 'r', origin: null })).toBe('ok')
  })

  it('prettifies unknown origins', () => {
    expect(approvalOriginLabel({ origin: 'matrix_bridge' })).toBe('Matrix Bridge')
    expect(approvalOriginLabel({})).toBeNull()
  })

  it('never lets a sensitive item into a bulk set', () => {
    const items = [
      { id: 'a', sensitive: true },
      { id: 'b', sensitive: false },
      { id: 'c' },
    ]
    expect(bulkApprovable(items).map((i) => i.id)).toEqual(['b', 'c'])
  })

  it('banner keeps per-item buttons only — no approve-all control', () => {
    const src = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), '..', 'components', 'ApprovalBanner.tsx'),
      'utf8',
    )
    expect(src).not.toMatch(/approve\s*all/i)
    expect(src).toContain('approvalHeadline(item)')
    expect(src).toContain('data-sensitive')
  })
})
