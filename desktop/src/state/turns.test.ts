import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/sessions', () => ({
  abortSession: vi.fn(async () => ({ status: 'aborted' })),
}))

import { completeStreamJob, registerStreamJob } from '../sessions/streamJobs'
import {
  clearTurnsForSession,
  getTurn,
  resetTurns,
  turnsForSession,
  upsertTurn,
} from './turns'

describe('TurnStore', () => {
  afterEach(() => {
    resetTurns()
    for (const id of ['sess-wire', 'sess-goal']) {
      completeStreamJob(id, 'aborted')
    }
  })

  it('keeps background and foreground turns from crossing sessions', () => {
    upsertTurn({
      sessionId: 'a',
      turnId: 't1',
      jobId: 'j1',
      status: 'running',
      goal: 'one',
      startedAt: '2026-08-24T00:00:00Z',
    })
    upsertTurn({
      sessionId: 'b',
      turnId: 't2',
      jobId: 'j2',
      status: 'running',
      goal: 'two',
      startedAt: '2026-08-24T00:00:01Z',
    })
    expect(turnsForSession('a')).toHaveLength(1)
    expect(turnsForSession('a')[0].turnId).toBe('t1')
    expect(getTurn('b', 't2')?.jobId).toBe('j2')
    clearTurnsForSession('a')
    expect(turnsForSession('a')).toHaveLength(0)
    expect(getTurn('b', 't2')?.status).toBe('running')
    clearTurnsForSession('b')
  })

  it('merges patches without wiping an existing goal', () => {
    upsertTurn({
      sessionId: 'a',
      turnId: 't-merge',
      jobId: 'j',
      status: 'running',
      goal: 'keep me',
      startedAt: '2026-08-24T00:00:00Z',
    })
    upsertTurn({
      sessionId: 'a',
      turnId: 't-merge',
      status: 'completed',
      completedAt: '2026-08-24T00:00:02Z',
    })
    const t = getTurn('a', 't-merge')
    expect(t?.goal).toBe('keep me')
    expect(t?.status).toBe('completed')
    expect(t?.jobId).toBe('j')
  })

  it('streamJobs register and complete upsert the same turn', () => {
    const c = new AbortController()
    const job = registerStreamJob('sess-wire', c)
    expect(job.turnId).toMatch(/^turn-/)
    const live = getTurn('sess-wire', job.turnId)
    expect(live?.status).toBe('running')
    expect(live?.jobId).toBe('sess-wire')
    completeStreamJob('sess-wire', 'done')
    const done = getTurn('sess-wire', job.turnId)
    expect(done?.status).toBe('completed')
    expect(done?.completedAt).toBeTruthy()
  })
})
