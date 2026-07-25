import { describe, expect, it } from 'vitest'
import {
  groupSessionsByProject,
  isNoProjectPath,
  projectDisplayName,
  projectKey,
} from './sessionProjects'
import type { ChatSession } from '../types'

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
    expect(isNoProjectPath('C:\\Work\\App')).toBe(false)
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
})
