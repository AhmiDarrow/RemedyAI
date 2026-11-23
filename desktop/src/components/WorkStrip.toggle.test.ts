/** @vitest-environment happy-dom */
import { createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { act } from 'react'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'

beforeAll(() => {
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
})
import { WorkStrip } from './WorkStrip'
import { BuildTodos, type BuildTodo } from './BuildTodos'
import type { ProcessStep } from '../utils/toolLabels'

const step = (id: string, extra: Partial<ProcessStep> = {}): ProcessStep => ({
  id,
  name: 'bash',
  label: 'Running command',
  status: 'done',
  startedAt: 1,
  endedAt: 2,
  argsText: JSON.stringify({ command: `echo ${id}` }),
  resultText: `output for ${id}\nmore lines`,
  ...extra,
})

const steps: ProcessStep[] = [
  step('1'),
  step('2', { status: 'error', error: 'boom\nstack' }),
  step('3', { name: 'web_fetch', argsText: JSON.stringify({ url: 'https://example.com/x' }), resultText: 'ok' }),
]

const todos: BuildTodo[] = [
  { id: 't1', content: 'Explore the folder', status: 'completed' },
  { id: 't2', content: 'Patch the loader', status: 'in_progress' },
  { id: 't3', content: 'Run the self-check', status: 'pending' },
]

function mount(node: ReturnType<typeof createElement>): { el: HTMLDivElement; root: Root } {
  const el = document.createElement('div')
  document.body.appendChild(el)
  const root = createRoot(el)
  act(() => {
    root.render(node)
  })
  return { el, root }
}

let live: { el: HTMLDivElement; root: Root } | null = null

afterEach(() => {
  if (live) {
    act(() => live!.root.unmount())
    live.el.remove()
    live = null
  }
})

describe('WorkStrip toggle-close', () => {
  it('opens and closes the Working header, Thinking, and each tool row', () => {
    live = mount(
      createElement(WorkStrip, {
        steps,
        thinking: 'Need the folder path first.',
        extras: createElement(BuildTodos, { items: todos, live: true }),
      }),
    )
    const el = live.el
    const head = () => el.querySelector('.ws-head') as HTMLButtonElement
    expect(head().getAttribute('aria-expanded')).toBe('false')
    expect(el.querySelector('[data-testid="ws-timeline"]')).toBeNull()

    act(() => {
      head().click()
    })
    expect(head().getAttribute('aria-expanded')).toBe('true')
    expect(el.querySelector('[data-testid="ws-timeline"]')).not.toBeNull()
    expect(el.querySelector('[data-testid="ws-thinking"]')).not.toBeNull()
    expect(el.querySelector('[data-testid="build-todos"]')).not.toBeNull()

    const think = () => el.querySelector('.ws-thinking-toggle') as HTMLButtonElement
    expect(think().getAttribute('aria-expanded')).toBe('false')
    expect(el.textContent).not.toContain('Need the folder path first.')
    act(() => {
      think().click()
    })
    expect(think().getAttribute('aria-expanded')).toBe('true')
    expect(el.textContent).toContain('Need the folder path first.')
    act(() => {
      think().click()
    })
    expect(think().getAttribute('aria-expanded')).toBe('false')
    expect(el.textContent).not.toContain('Need the folder path first.')

    const rows = () => [...el.querySelectorAll('.ws-step-row')] as HTMLButtonElement[]
    expect(rows()).toHaveLength(3)
    act(() => {
      rows()[1]!.click()
    })
    expect(el.querySelector('.ws-detail')?.textContent).toContain('boom')
    act(() => {
      rows()[1]!.click()
    })
    expect(el.querySelector('.ws-detail')).toBeNull()

    const buildHead = () => el.querySelector('.build-todos-head') as HTMLButtonElement
    expect(buildHead().getAttribute('aria-expanded')).toBe('true')
    expect(el.textContent).toContain('Patch the loader')
    act(() => {
      buildHead().click()
    })
    expect(buildHead().getAttribute('aria-expanded')).toBe('false')
    expect(el.textContent).not.toContain('Patch the loader')
    act(() => {
      buildHead().click()
    })
    expect(buildHead().getAttribute('aria-expanded')).toBe('true')

    act(() => {
      head().click()
    })
    expect(head().getAttribute('aria-expanded')).toBe('false')
    expect(el.querySelector('[data-testid="ws-timeline"]')).toBeNull()
    expect(el.querySelector('[data-testid="ws-thinking"]')).toBeNull()
    expect(el.querySelector('[data-testid="build-todos"]')).toBeNull()
  })
})
