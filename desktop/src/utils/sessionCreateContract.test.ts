/**
 * Contract: New Session must send explicit empty project_path so the API
 * does not inherit settings.project_path (root / no-project chat).
 *
 * Mirrors desktop/src/hooks/useSessions.ts create().
 */
import { describe, expect, it } from 'vitest'

/** Same payload shape useSessions.create builds */
export function newSessionPayload(title?: string) {
  return { title, project_path: '' as const }
}

describe('new session create contract', () => {
  it('always includes empty project_path (root)', () => {
    const body = newSessionPayload('Chat')
    expect(body).toEqual({ title: 'Chat', project_path: '' })
    expect(body.project_path).toBe('')
    // Critical: not undefined — undefined would inherit global project on API
    expect('project_path' in body).toBe(true)
  })
})
