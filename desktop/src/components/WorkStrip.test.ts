import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { STEP_WINDOW, WorkStrip, type WorkStripProps } from './WorkStrip'
import type { ProcessStep } from '../utils/toolLabels'

const mk = (i: number, extra: Partial<ProcessStep> = {}): ProcessStep => ({
  id: `s${i}`,
  name: 'bash',
  label: 'Running command',
  status: 'done',
  startedAt: 1000 * i,
  endedAt: 1000 * i + 500,
  argsText: JSON.stringify({ command: `step ${i}` }),
  ...extra,
})

const render = (props: Partial<WorkStripProps>) =>
  renderToStaticMarkup(createElement(WorkStrip, { steps: [], ...props }))

const steps: ProcessStep[] = [
  mk(1, { narration: "I'll reach the portal folder through the shell." }),
  mk(2, { status: 'error', error: 'FINDSTR: Cannot open *.json\nmore detail' }),
  mk(3, {
    name: 'file_read',
    label: 'Reading file',
    narration: 'Found the manifest. Reading the loader next.',
    argsText: JSON.stringify({ path: 'C:\\Games\\Portal\\addons\\portal-hub\\loader.lua' }),
  }),
]

describe('WorkStrip', () => {
  it('collapses a finished turn to one summary line', () => {
    const html = render({ steps })
    expect(html).toContain('Worked')
    expect(html).toContain('3 steps')
    expect(html).toContain('1 error')
    expect(html).toContain('aria-expanded="false"')
    expect(html).not.toContain('data-testid="ws-status"')
    expect(html).not.toContain('data-testid="ws-timeline"')
    expect(html).not.toContain('reach the portal folder')
  })

  it('expands to a height-capped tool timeline without user-facing prose', () => {
    const html = render({ steps, defaultOpen: true })
    expect(html).toContain('data-testid="ws-timeline"')
    expect(html.match(/class="ws-group"/g)?.length).toBe(2)
    // Assistant prose belongs in the bubble, not the work log.
    expect(html).not.toContain('reach the portal folder')
    expect(html).not.toContain('Found the manifest')
    // Short tool names and short targets.
    expect(html).toContain('>Bash<')
    expect(html).toContain('>Read<')
    expect(html).toContain('…/portal-hub/loader.lua')
    // Errors: ✕ icon and the first line only, in the muted error style.
    expect(html).toContain('data-status="error"')
    expect(html).toContain('class="ws-err">FINDSTR: Cannot open *.json<')
    expect(html).not.toContain('more detail')
  })

  it('keeps thinking as its own collapsed item', () => {
    const html = render({ steps, thinking: 'Rename confirmed in history.', defaultOpen: true })
    expect(html).toContain('data-testid="ws-thinking"')
    expect(html).toContain('Thought')
    expect(html).not.toContain('Rename confirmed in history.')
  })

  it('labels a thinking-only turn as thought, not work', () => {
    const html = render({ thinking: 'Short think.' })
    expect(html).toContain('>Thought<')
    expect(html).not.toContain('steps')
  })

  it('shows a live status line with the running action and a progress bar', () => {
    const live = [...steps, mk(4, { status: 'running', name: 'grep', argsText: JSON.stringify({ pattern: 'entry', path: 'C:\\a\\b\\c' }) })]
    const html = render({
      steps: live,
      live: true,
      progress: { label: 'Taking that in…', step: 29, total: 36 },
    })
    expect(html).toContain('Working')
    expect(html).toContain('data-testid="ws-status"')
    expect(html).toContain('Grep')
    expect(html).toContain('entry in …/b/c')
    expect(html).toContain('role="progressbar"')
    expect(html).toContain('aria-valuenow="81"')
  })

  it('renders nothing for a finished turn with no background work', () => {
    expect(render({})).toBe('')
  })

  it('windows long step lists to the newest steps', () => {
    const many = Array.from({ length: STEP_WINDOW + 15 }, (_, i) => mk(i))
    const html = render({ steps: many, defaultOpen: true })
    expect(html).toContain('Show 15 earlier steps')
    expect(html.match(/class="ws-step /g)?.length).toBe(STEP_WINDOW)
  })
})
