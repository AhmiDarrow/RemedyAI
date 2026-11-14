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

  it('routes the live turn through LiveTurnView: reply bubble plus a Working… section', () => {
    const dock = src.indexOf('className="live-stream-dock"')
    const view = src.indexOf('<LiveTurnView', dock)
    const reply = src.indexOf('renderReply=', view)
    const work = src.indexOf('data-live-work="activity"', view)
    const liveTodos = src.indexOf('<BuildTodos items={buildTodos} live', view)
    expect(dock).toBeGreaterThan(-1)
    expect(view).toBeGreaterThan(dock)
    expect(reply).toBeGreaterThan(view)
    expect(work).toBeGreaterThan(reply)
    expect(liveTodos).toBeGreaterThan(work)
    // Thinking goes to the activity section, never into the reply bubble.
    expect(src.slice(reply, work)).toContain('partialThinking=""')
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
