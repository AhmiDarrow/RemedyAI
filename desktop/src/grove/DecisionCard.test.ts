/** @vitest-environment happy-dom */
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PendingApproval } from '../api/partner'

const api = vi.hoisted(() => ({
  list: [] as PendingApproval[],
  resolved: [] as string[],
}))

vi.mock('../api/partner', () => ({
  listApprovals: async () => api.list,
  resolveApproval: async (id: string) => {
    api.resolved.push(id)
    api.list = api.list.filter((i) => i.id !== id)
    return { hint: 'ok' }
  },
}))

import { DECISION_ARM_MS, DecisionCard } from './DecisionCard'

beforeAll(() => {
  ;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
})

const approval = (id: string, created_at: number, command: string): PendingApproval =>
  ({ id, tool_name: 'shell.exec', command, reason: '', created_at, status: 'pending' }) as PendingApproval

let root: Root | null = null
let el: HTMLDivElement | null = null

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

async function mount() {
  el = document.createElement('div')
  document.body.appendChild(el)
  root = createRoot(el)
  await act(async () => {
    root!.render(
      createElement(DecisionCard, {
        serverReady: true,
        tucked: false,
        onTucked: () => {},
        seen: new Set<string>(),
      }),
    )
  })
  await tick(0)
}

function yes(): HTMLButtonElement {
  return el!.querySelector('button.dc-yes') as HTMLButtonElement
}

async function clickYes() {
  await act(async () => {
    yes().dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
  await tick(0)
}

beforeEach(() => {
  vi.useFakeTimers()
  api.list = []
  api.resolved = []
})

afterEach(() => {
  act(() => root?.unmount())
  el?.remove()
  root = null
  el = null
  vi.useRealTimers()
})

describe('DecisionCard binds Yes to the decision on screen', () => {
  it('a poll that brings in an older decision does not swap the card under the click', async () => {
    api.list = [approval('shown', 200, 'ls -la')]
    await mount()
    await tick(DECISION_ARM_MS)
    expect(el!.textContent).toContain('Remedy is asking')

    // The next poll returns an older decision too; ordering alone would put it first.
    api.list = [approval('older', 100, 'rm -rf ~'), approval('shown', 200, 'ls -la')]
    await tick(4000)
    await tick(DECISION_ARM_MS)
    expect(el!.textContent).toContain('1 of 2')

    await clickYes()
    expect(api.resolved).toEqual(['shown'])
  })

  it('a card that just took the front ignores a click until it has been shown', async () => {
    api.list = [approval('a', 100, 'ls'), approval('b', 200, 'rm -rf ~')]
    await mount()
    await tick(DECISION_ARM_MS)
    await clickYes()
    expect(api.resolved).toEqual(['a'])

    // "b" is now in front; a second click right away (double click, held Y) must not approve it.
    expect(yes().disabled).toBe(true)
    await clickYes()
    expect(api.resolved).toEqual(['a'])

    await tick(DECISION_ARM_MS)
    expect(yes().disabled).toBe(false)
    await clickYes()
    expect(api.resolved).toEqual(['a', 'b'])
  })
})
