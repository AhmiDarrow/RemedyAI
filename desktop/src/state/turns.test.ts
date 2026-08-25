import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/sessions', () => ({
  abortSession: vi.fn(async () => ({ status: 'aborted' })),
}))

import { completeStreamJob, registerStreamJob } from '../sessions/streamJobs'
import {
  clearTurnsForSession,
  getTurn,
  isLiveTurnStatus,
  liveTurnForSession,
  plainTurnLabel,
  resetTurns,
  subscribeTurns,
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

  it('subscribeTurns notifies then stops after unsubscribe', () => {
    const seen: string[] = []
    const unsub = subscribeTurns((all) => {
      seen.push(all.map((t) => t.turnId).join(','))
    })
    upsertTurn({
      sessionId: 'a',
      turnId: 't-sub',
      status: 'running',
      startedAt: '2026-08-24T00:00:00Z',
    })
    expect(seen).toEqual(['t-sub'])
    unsub()
    upsertTurn({
      sessionId: 'a',
      turnId: 't-sub',
      status: 'waiting',
    })
    expect(seen).toEqual(['t-sub'])
  })

  it('plainTurnLabel uses partner-surface wording and hides terminal states', () => {
    expect(plainTurnLabel('planning')).toBe('Working…')
    expect(plainTurnLabel('running')).toBe('Working…')
    expect(plainTurnLabel('waiting')).toBe('Waiting for you…')
    expect(plainTurnLabel('verifying')).toBe('Checking…')
    expect(plainTurnLabel('completed')).toBeNull()
    expect(plainTurnLabel('failed')).toBeNull()
    expect(isLiveTurnStatus('running')).toBe(true)
    expect(isLiveTurnStatus('completed')).toBe(false)
  })

  it('liveTurnForSession picks the newest live turn and ignores completed', () => {
    expect(liveTurnForSession('empty')).toBeUndefined()
    upsertTurn({
      sessionId: 'a',
      turnId: 'old',
      status: 'running',
      startedAt: '2026-08-24T00:00:00Z',
    })
    upsertTurn({
      sessionId: 'a',
      turnId: 'newer',
      status: 'waiting',
      startedAt: '2026-08-24T00:00:05Z',
    })
    upsertTurn({
      sessionId: 'a',
      turnId: 'done',
      status: 'completed',
      startedAt: '2026-08-24T00:00:09Z',
      completedAt: '2026-08-24T00:00:10Z',
    })
    const live = liveTurnForSession('a')
    expect(live?.turnId).toBe('newer')
    expect(plainTurnLabel(live!.status)).toBe('Waiting for you…')
    upsertTurn({ sessionId: 'a', turnId: 'newer', status: 'completed' })
    upsertTurn({ sessionId: 'a', turnId: 'old', status: 'failed' })
    expect(liveTurnForSession('a')).toBeUndefined()
  })
})
