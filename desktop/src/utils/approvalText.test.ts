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
    expect(approvalOriginLabel({ origin: 'hive:parent' })).toBe('a hive task')
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
    expect(src).toContain("act(item, true, 'once')")
    expect(src).toContain('await listApprovals()')
    expect(src).toContain("origin.startsWith('hive:')")
    expect(src).toContain('i.blocking')
  })
})

describe('approvalOriginLabel — background work Remedy started herself', () => {
  it('names runtime origins in plain words, never raw ids', () => {
    expect(approvalOriginLabel({ origin: 'partner:child:5f2e-41a8' })).toBe('a side job')
    expect(approvalOriginLabel({ origin: 'PARTNER:routine:rt-1' })).toBe('a routine')
    expect(approvalOriginLabel({ origin: 'partner:goal:abc' })).toBe('goal autopilot')
    expect(approvalOriginLabel({ origin: 'partner:other' })).toBe('background work')
    // Lookalikes are not background work.
    expect(approvalOriginLabel({ origin: 'partnerx:child:1' })).not.toBe('a side job')
    expect(approvalHeadline({ summary: 'Remedy wants to run a command.', reason: '', origin: 'partner:child:x' })).toBe(
      'Remedy wants to run a command. · via a side job',
    )
  })
})
