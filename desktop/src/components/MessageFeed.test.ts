import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const src = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), 'MessageFeed.tsx'),
  'utf8',
)

describe('MessageFeed live chrome order', () => {
  it('re-pins the feed on composer submit, not only Jump to latest', () => {
    expect(src).toContain("reattachKey: `${lastUserMsgId ?? ''}:${stickNonce}`")
    // Session switches floor via the scroller mount, not the reattach key.
    expect(src).not.toMatch(/reattachKey:.*sessionId/)
  })

  it('renders thinking/answer before live todos and process chips', () => {
    const dock = src.indexOf('className="live-stream-dock"')
    const reply = src.indexOf('id: \'streaming\'', dock)
    const work = src.indexOf('data-live-work="below-reply"', dock)
    const liveTodos = src.indexOf('<BuildTodos items={buildTodos} live', dock)
    expect(dock).toBeGreaterThan(-1)
    expect(reply).toBeGreaterThan(dock)
    expect(work).toBeGreaterThan(reply)
    expect(liveTodos).toBeGreaterThan(work)
  })

  it('keeps leftover todos after the live reply dock', () => {
    const dock = src.indexOf('className="live-stream-dock"')
    const leftover = src.indexOf('{!streaming && todosHaveOpen(buildTodos) && (')
    expect(leftover).toBeGreaterThan(dock)
  })

  it('puts historical process chips under the assistant answer', () => {
    const bubbleClose = src.indexOf('{/* Process under answer')
    const process = src.indexOf('className="process-under-answer')
    expect(bubbleClose).toBeGreaterThan(-1)
    expect(process).toBeGreaterThan(bubbleClose)
  })
})
