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
  it('shows only the compact Working strip (no bubble) until reply text streams', () => {
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
    expect(html).not.toContain('data-testid="live-reply"')
    // Collapsed by default: narration and thinking wait behind the toggle.
    expect(html).not.toContain(narration)
    expect(html).not.toContain('Need the folder path first.')
  })

  it('renders the reply in its own bubble without narration once it streams', () => {
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
    expect(bubble).not.toContain(narration)
  })

  it('opens the strip by default in full process mode', () => {
    const html = render({
      partialText: narration,
      textBreaks: [narration.length],
      mode: 'full',
      processSteps: [step('1', 'running', { narration })],
    })
    expect(html).toContain('aria-expanded="true"')
    expect(html).toContain(narration)
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
})
