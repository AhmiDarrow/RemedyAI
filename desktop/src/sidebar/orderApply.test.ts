import { describe, expect, it } from 'vitest'
import type { ChatSession } from '../types'
import type { ProjectGroup } from '../utils/sessionProjects'
import {
  applyProjectOrder,
  applySessionOrder,
  applySidebarOrder,
} from './orderApply'
import { mergeProjectOrder, moveProject } from './projectOrder'
import { mergeSessionOrder, moveSession } from './sessionOrder'

function sess(
  id: string,
  opts?: { updated_at?: string; project_path?: string | null },
): ChatSession {
  return {
    id,
    title: id,
    model: null,
    agent: null,
    project_path: opts?.project_path ?? null,
    llm_provider: null,
    message_count: 0,
    origin_channel: null,
    external_chat_id: null,
    external_user: null,
    created_at: opts?.updated_at || '2026-01-01T00:00:00Z',
    updated_at: opts?.updated_at || '2026-01-01T00:00:00Z',
  }
}

describe('mergeProjectOrder', () => {
  it('keeps saved order and appends new keys', () => {
    expect(mergeProjectOrder(['B', 'A'], ['A', 'B', 'C'])).toEqual(['B', 'A', 'C'])
  })
})

describe('applyProjectOrder', () => {
  it('keeps No project first and reorders the rest', () => {
    const groups: ProjectGroup[] = [
      { key: '', path: '', label: 'No project', sessions: [] },
      { key: 'C:\\A', path: 'C:\\A', label: 'A', sessions: [] },
      { key: 'C:\\B', path: 'C:\\B', label: 'B', sessions: [] },
    ]
    const out = applyProjectOrder(groups, ['C:\\B', 'C:\\A'])
    expect(out.map((g) => g.key)).toEqual(['', 'C:\\B', 'C:\\A'])
  })
})

describe('applySessionOrder', () => {
  it('puts pinned first then manual order', () => {
    const list = [
      sess('a', { updated_at: '2026-01-03T00:00:00Z' }),
      sess('b', { updated_at: '2026-01-02T00:00:00Z' }),
      sess('c', { updated_at: '2026-01-01T00:00:00Z' }),
    ]
    const out = applySessionOrder(list, ['c', 'a', 'b'], new Set(['b']))
    expect(out.map((s) => s.id)).toEqual(['b', 'c', 'a'])
  })
})

describe('moveSession merge boundaries', () => {
  it('mergeSessionOrder appends unknown ids', () => {
    expect(mergeSessionOrder(['b', 'a'], ['a', 'b', 'c'])).toEqual(['b', 'a', 'c'])
  })
})

describe('applySidebarOrder', () => {
  it('applies both layers', () => {
    const groups: ProjectGroup[] = [
      {
        key: '',
        path: '',
        label: 'No project',
        sessions: [sess('n1', { updated_at: '2026-01-02T00:00:00Z' })],
      },
      {
        key: 'C:\\P',
        path: 'C:\\P',
        label: 'P',
        sessions: [
          sess('s1', { project_path: 'C:\\P', updated_at: '2026-01-02T00:00:00Z' }),
          sess('s2', { project_path: 'C:\\P', updated_at: '2026-01-01T00:00:00Z' }),
        ],
      },
    ]
    const out = applySidebarOrder(
      groups,
      ['C:\\P'],
      { 'C:\\P': ['s2', 's1'] },
      new Set(),
    )
    expect(out[1]!.sessions.map((s) => s.id)).toEqual(['s2', 's1'])
  })
})

// moveProject/moveSession need localStorage — use a minimal mock
describe('moveProject / moveSession with storage', () => {
  const store = new Map<string, string>()
  const ls = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => {
      store.set(k, v)
    },
    removeItem: (k: string) => {
      store.delete(k)
    },
  }

  it('moves project and session', () => {
    // @ts-expect-error test stub
    globalThis.localStorage = ls
    store.clear()
    const keys = ['C:\\A', 'C:\\B', 'C:\\C']
    expect(moveProject('C:\\B', 'up', keys)).toEqual(['C:\\B', 'C:\\A', 'C:\\C'])
    expect(moveProject('C:\\B', 'up', keys)).toEqual(['C:\\B', 'C:\\A', 'C:\\C']) // already top

    const ids = ['x', 'y', 'z']
    expect(moveSession('y', 'C:\\A', 'down', ids)).toEqual(['x', 'z', 'y'])
  })
})
