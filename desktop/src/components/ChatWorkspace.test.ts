import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ChatWorkspace } from './ChatWorkspace'

const user = {
  id: 'u1',
  role: 'user' as const,
  content: 'The portal addon stopped loading after the last update. Can you fix it?',
  thinking: null,
  tool_calls: [],
  tool_results: [],
  model: null,
  agent: null,
  tokens: null,
  created_at: '2026-11-14T20:59:00Z',
  reverted: false,
}

const assistant = {
  id: 'a1',
  role: 'assistant' as const,
  content: "I'll reach the folder through the shell.\n\nThe addon loads again.",
  thinking: 'Rename confirmed in history.',
  tool_calls: [
    {
      name: 'bash',
      args: { command: 'dir' },
      id: 'c1',
      narration: "I'll reach the folder through the shell.",
    },
  ],
  tool_results: [{ name: 'bash', output: 'ok', id: 'c1' }],
  model: null,
  agent: null,
  tokens: null,
  created_at: '2026-11-14T21:00:00Z',
  reverted: false,
}

const render = (props: Partial<Parameters<typeof ChatWorkspace>[0]> = {}) =>
  renderToStaticMarkup(
    createElement(ChatWorkspace, {
      messages: [user, assistant],
      partialText: '',
      streaming: false,
      loading: false,
      persist: false,
      actionsSide: 'left',
      actionsWidth: 380,
      ...props,
    } as never),
  )

describe('ChatWorkspace split', () => {
  it('puts coding/actions on the left and chat on the right by default', () => {
    const html = render()
    expect(html).toContain('data-testid="chat-workspace"')
    expect(html).toContain('data-actions-side="left"')
    const actionsAt = html.indexOf('data-testid="actions-column"')
    const chatAt = html.indexOf('data-testid="chat-column"')
    expect(actionsAt).toBeGreaterThan(-1)
    expect(chatAt).toBeGreaterThan(actionsAt)
    expect(html).toContain('Coding / Actions')
    expect(html).toContain('Chat')
    expect(html).toContain('data-testid="chat-split-swap"')
  }, 30_000)

  it('swaps sides when actionsSide is right', () => {
    const html = render({ actionsSide: 'right' })
    expect(html).toContain('data-actions-side="right"')
    const actionsAt = html.indexOf('data-testid="actions-column"')
    const chatAt = html.indexOf('data-testid="chat-column"')
    expect(chatAt).toBeGreaterThan(-1)
    expect(actionsAt).toBeGreaterThan(chatAt)
  }, 30_000)

  it('keeps work in the actions pane and speech bubbles only in chat', () => {
    const html = render()
    const actions = html.slice(
      html.indexOf('data-testid="actions-column"'),
      html.indexOf('data-testid="chat-column"'),
    )
    const chat = html.slice(html.indexOf('data-testid="chat-column"'))
    expect(actions).toContain('data-testid="action-turn"')
    expect(actions).toContain('data-testid="work-strip"')
    expect(actions).toContain('Rename confirmed in history.')
    expect(actions).toContain('portal addon stopped loading')
    expect(actions).not.toContain('data-testid="assistant-beats"')
    expect(chat).toContain('data-testid="assistant-beats"')
    expect(chat).toContain('reach the folder')
    expect(chat).toContain('The addon loads again.')
    expect(chat).toContain('chat-bubble-user')
    expect(chat).not.toContain('data-testid="work-strip"')
    expect(chat).not.toContain('Rename confirmed in history.')
  }, 30_000)

  it('shows a working chip in the chat header while a turn is live', () => {
    const html = render({
      streaming: true,
      partialText: 'Patching the loader.',
      partialThinking: 'Need the path.',
      processSteps: [
        { id: 's1', name: 'bash', label: 'Running command', status: 'running', startedAt: 1 },
      ],
    } as never)
    const chat = html.slice(html.indexOf('data-testid="chat-column"'))
    expect(chat).toContain('data-testid="chat-working-chip"')
    expect(chat).toContain('data-testid="chat-working-hint"')
    expect(chat).not.toContain('data-testid="work-strip"')
    const actions = html.slice(
      html.indexOf('data-testid="actions-column"'),
      html.indexOf('data-testid="chat-column"'),
    )
    expect(actions).toContain('data-testid="work-strip"')
    expect(actions).toContain('Need the path.')
  }, 30_000)

  it('offers reactions on mid-turn Remedy bubbles before the turn ends', () => {
    const narration = "I'll reach the folder through the shell."
    const html = render({
      messages: [user],
      streaming: true,
      partialText: narration,
      textBreaks: [narration.length],
      partialThinking: 'Need the path.',
      liveMessageId: 'draft-1',
      onReact: () => {},
      processSteps: [
        {
          id: 's1',
          name: 'bash',
          label: 'Running command',
          status: 'running',
          startedAt: 1,
          narration,
        },
      ],
    } as never)
    const chat = html.slice(html.indexOf('data-testid="chat-column"'))
    const actions = html.slice(
      html.indexOf('data-testid="actions-column"'),
      html.indexOf('data-testid="chat-column"'),
    )
    expect(chat).toContain('data-testid="live-settled-reply"')
    expect(chat).toContain('aria-label="Add reaction"')
    expect(chat).toMatch(/I(?:'|&#x27;)ll reach the folder through the shell/)
    expect(chat).not.toContain('Need the path.')
    expect(actions).toContain('Need the path.')
  }, 30_000)
})

describe('ChatWorkspace layout CSS', () => {
  it('uncaps the actions-pane work body and rounds both panes as cards', async () => {
    const { readFileSync } = await import('node:fs')
    const { dirname, join } = await import('node:path')
    const { fileURLToPath } = await import('node:url')
    const css = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), '../index.css'),
      'utf8',
    )
    const uncap = css.slice(css.indexOf('/* The pane itself scrolls.'))
    expect(uncap).toContain('.actions-pane .ws-body')
    expect(uncap).toContain('.actions-pane .ws-timeline')
    expect(uncap).toContain('.actions-pane .build-todos-list')
    expect(uncap).toMatch(/max-height:\s*none/)
    expect(css).toMatch(/\.actions-pane,[\s\S]*?border-radius:\s*var\(--chat-bubble-radius\)/)
    expect(css).toMatch(/\.chat-workspace \{[\s\S]*?gap:\s*0\.55rem/)
    // Last mid-turn bubble: caret stays on the text line.
    expect(css).toContain('.chat-bubble-body.stream-md > p:last-of-type')
    expect(css).toContain('display: inline')
  })
})
