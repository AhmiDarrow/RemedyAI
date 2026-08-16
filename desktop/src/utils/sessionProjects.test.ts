import { describe, expect, it } from 'vitest'
import {
  getLockedProjects,
  groupSessionsByProject,
  isNoProjectPath,
  isProjectLocked,
  projectDisplayName,
  projectKey,
  removeKnownProject,
  setProjectLocked,
  toggleProjectLocked,
  addKnownProject,
} from './sessionProjects'
import type { ChatSession } from '../types'

function installMemoryLocalStorage() {
  const store = new Map<string, string>()
  const ls = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => {
      store.set(k, v)
    },
    removeItem: (k: string) => {
      store.delete(k)
    },
    clear: () => store.clear(),
  }
  // @ts-expect-error test stub
  globalThis.localStorage = ls
  return store
}

function sess(
  id: string,
  project_path: string | null,
  updated_at = '2026-01-02',
): ChatSession {
  return {
    id,
    title: id,
    model: null,
    agent: null,
    project_path,
    message_count: 1,
    created_at: updated_at,
    updated_at,
  }
}

describe('sessionProjects', () => {
  it('detects no-project paths', () => {
    expect(isNoProjectPath(null)).toBe(true)
    expect(isNoProjectPath('')).toBe(true)
    expect(isNoProjectPath('.')).toBe(true)
    expect(isNoProjectPath('C:\\')).toBe(true)
    expect(isNoProjectPath('C:')).toBe(true)
    expect(isNoProjectPath('/')).toBe(true)
    expect(isNoProjectPath('C:\\Work\\App')).toBe(false)
  })

  it('does not grow a volume-root project bucket', () => {
    const groups = groupSessionsByProject(
      [sess('rooty', 'C:\\'), sess('real', 'C:\\Work\\App')],
      [],
    )
    expect(groups.find((g) => g.key === 'C:' || g.label === 'C:')).toBeUndefined()
    expect(groups[0]!.sessions.map((s) => s.id)).toContain('rooty')
    expect(groups.find((g) => g.label === 'App')?.sessions.map((s) => s.id)).toEqual([
      'real',
    ])
  })

  it('normalizes keys and display names', () => {
    expect(projectKey('c:/Users/Me/RemedyAI/')).toBe('C:\\Users\\Me\\RemedyAI')
    expect(projectDisplayName('C:\\Users\\Me\\RemedyAI')).toBe('RemedyAI')
  })

  it('groups no-project first, then projects as parents', () => {
    const groups = groupSessionsByProject(
      [
        sess('a', null, '2026-01-03'),
        sess('b', 'C:\\Work\\App', '2026-01-04'),
        sess('c', 'C:\\Work\\App', '2026-01-02'),
        sess('d', '.', '2026-01-01'),
      ],
      ['C:\\Work\\Empty'],
    )
    expect(groups[0]!.key).toBe('')
    expect(groups[0]!.sessions.map((s) => s.id)).toEqual(['a', 'd'])
    const app = groups.find((g) => g.label === 'App')
    expect(app).toBeTruthy()
    expect(app!.sessions.map((s) => s.id)).toEqual(['b', 'c'])
    const empty = groups.find((g) => g.path.includes('Empty'))
    expect(empty?.sessions).toEqual([])
  })

  it('tree snapshot: stable structure for sidebar', () => {
    const groups = groupSessionsByProject(
      [
        sess('1', null),
        sess('2', 'D:\\RemedyAI'),
        sess('3', 'D:\\RemedyAI'),
        sess('4', 'D:\\Other'),
      ],
      [],
    )
    // Structural snapshot (not a screenshot): labels + child ids
    const snap = groups.map((g) => ({
      label: g.label,
      kids: g.sessions.map((s) => s.id).sort(),
    }))
    expect(snap).toEqual([
      { label: 'No project', kids: ['1'] },
      { label: 'Other', kids: ['4'] },
      { label: 'RemedyAI', kids: ['2', '3'] },
    ])
  })

  it('locks project folders and blocks remove while locked', () => {
    installMemoryLocalStorage()
    const path = 'C:\\Work\\LockedApp'
    addKnownProject(path)
    expect(isProjectLocked(path)).toBe(false)

    setProjectLocked(path, true)
    expect(isProjectLocked(path)).toBe(true)
    expect(getLockedProjects().has(projectKey(path))).toBe(true)

    // removeKnownProject must no-op while locked
    const still = removeKnownProject(path)
    expect(still).toContain(projectKey(path))
    expect(isProjectLocked(path)).toBe(true)

    toggleProjectLocked(path)
    expect(isProjectLocked(path)).toBe(false)
    const gone = removeKnownProject(path)
    expect(gone).not.toContain(projectKey(path))
  })
})
