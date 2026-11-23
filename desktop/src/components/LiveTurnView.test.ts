import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { LiveTurnView } from './LiveTurnView'
import type { ProcessStep } from '../utils/toolLabels'

const step = (id: string, status: ProcessStep['status'], extra: Partial<ProcessStep> = {}): ProcessStep => ({
  id,
  name: 'bash',
  label: 'Running command',
  status,
  startedAt: 0,
  ...extra,
})

const render = (props: Partial<Parameters<typeof LiveTurnView>[0]>) =>
  renderToStaticMarkup(
    createElement(LiveTurnView, {
      partialText: '',
      processSteps: [],
      renderReply: (_msg, reply) => createElement('div', { className: 'bubble' }, reply),
      ...props,
    }),
  )

const narration = 'Reaching the portal folder through the shell.'

describe('LiveTurnView', () => {
  it('shows user-facing text as a bubble while work stays in the collapsed strip', () => {
    const html = render({
      partialText: narration,
      textBreaks: [narration.length],
      partialThinking: 'Need the folder path first.',
      processSteps: [step('1', 'running', { narration, argsText: '{"command":"dir"}' })],
    })
    expect(html).toContain('data-testid="work-strip"')
    expect(html).toContain('Working')
    expect(html).toContain('data-testid="ws-status"')
    expect(html).toContain('Bash')
    expect(html).toContain('dir')
    const bubble = html.slice(html.indexOf('data-testid="live-reply"'))
    expect(bubble).toContain(narration)
    // Collapsed by default: thinking and tool details wait behind the toggle.
    expect(html).not.toContain('Need the folder path first.')
    expect(html).not.toContain('data-testid="ws-timeline"')
    expect(html).not.toContain('data-testid="ws-thinking"')
  })

  it('keeps all user-facing text in the bubble once the reply streams', () => {
    const reply = 'The addon loads now.'
    const html = render({
      partialText: `${narration}\n\n${reply}`,
      textBreaks: [narration.length],
      processSteps: [step('1', 'done', { narration }), step('2', 'error', { error: 'exit 3' })],
    })
    expect(html).toContain('2 steps')
    expect(html).toContain('1 error')
    // Reply streaming and nothing running: the live status line steps aside.
    expect(html).not.toContain('data-testid="ws-status"')
    const bubble = html.slice(html.indexOf('data-testid="live-reply"'))
    expect(bubble).toContain(reply)
    expect(bubble).toContain(narration)
    // Collapsed strip does not repeat the prose.
    const strip = html.slice(html.indexOf('data-testid="work-strip"'), html.indexOf('data-testid="live-reply"'))
    expect(strip).not.toContain(narration)
  })

  it('stays collapsed by default even in full process mode', () => {
    const html = render({
      partialText: narration,
      textBreaks: [narration.length],
      mode: 'full',
      processSteps: [step('1', 'running', { narration })],
    })
    expect(html).toContain('aria-expanded="false"')
    expect(html).not.toContain('data-testid="ws-timeline"')
  })

  it('a plain reply with no background work is just the bubble', () => {
    const html = render({ partialText: 'Hi! What can I do?' })
    expect(html).toContain('data-testid="live-reply"')
    expect(html).not.toContain('data-testid="work-strip"')
  })

  it('shows a waiting strip before the first token of a plain reply', () => {
    const html = render({ partialText: '' })
    expect(html).toContain('data-testid="work-strip"')
    expect(html).toContain('Starting…')
  })

  it('hides the Working strip when showWork is false', () => {
    const html = render({
      partialText: narration,
      partialThinking: 'Need the folder path first.',
      processSteps: [step('1', 'running', { narration, argsText: '{"command":"dir"}' })],
      showWork: false,
    })
    expect(html).toContain('data-testid="live-reply"')
    expect(html).toContain(narration)
    expect(html).not.toContain('data-testid="work-strip"')
    expect(html).not.toContain('Need the folder path first.')
  })
})
