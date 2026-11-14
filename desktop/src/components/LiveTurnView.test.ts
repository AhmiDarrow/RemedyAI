import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { LiveTurnView } from './LiveTurnView'
import type { ProcessStep } from '../utils/toolLabels'

const step = (id: string, status: ProcessStep['status']): ProcessStep => ({
  id,
  name: 'shell',
  label: 'Shell',
  status,
  startedAt: 0,
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
  it('shows an open Working… section and no reply bubble before the reply starts', () => {
    const html = render({
      partialText: narration,
      textBreaks: [narration.length],
      partialThinking: 'Need the folder path first.',
      processSteps: [step('1', 'running')],
    })
    expect(html).toContain('data-testid="live-activity"')
    expect(html).toContain('aria-expanded="true"')
    expect(html).toContain('Working… · 1 step')
    expect(html).toContain('data-testid="live-narration"')
    expect(html).toContain(narration)
    expect(html).toContain('Need the folder path first.')
    expect(html).not.toContain('data-testid="live-reply"')
  })

  it('collapses background work once the reply arrives and keeps narration out of the bubble', () => {
    const reply = 'The addon loads now.'
    const text = `${narration}\n\n${reply}`
    const html = render({
      partialText: text,
      textBreaks: [narration.length],
      processSteps: [step('1', 'done'), step('2', 'error')],
    })
    expect(html).toContain('aria-expanded="false"')
    expect(html).toContain('Worked · 2 steps · 1 error')
    expect(html).not.toContain('data-testid="live-narration"')
    const bubble = html.slice(html.indexOf('data-testid="live-reply"'))
    expect(bubble).toContain(reply)
    expect(bubble).not.toContain(narration)
  })

  it('renders a plain reply with no tools as just the bubble plus nothing else heavy', () => {
    const html = render({ partialText: 'Hi! What can I do?' })
    expect(html).toContain('data-testid="live-reply"')
    expect(html).not.toContain('data-testid="live-activity"')
  })
})
