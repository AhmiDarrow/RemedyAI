import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/sessions', () => ({
  abortSession: vi.fn(async () => ({ status: 'aborted' })),
}))

import {
  appendJobToken,
  getJobLastSeq,
  getJobRequestId,
  isFollowingTurn,
  setJobLastSeq,
  setJobRequestId,
  appendJobThinking,
  replaceJobThinking,
  completeStreamJob,
  countRunningJobs,
  detachStreamJob,
  getBusySessionIds,
  getJobPaint,
  getStreamJob,
  isJobUiCommitted,
  markJobUiCommitted,
  registerStreamJob,
  markLiveTurn,
  setJobClaimEpoch,
  setJobProcessSteps,
  shouldRestoreStoppedPartial,
  stopStreamJob,
  withStoppedMarker,
} from './streamJobs'
import { getTurn, resetTurns } from '../state/turns'

describe('streamJobs', () => {
  afterEach(() => {
    // Clear registry by completing any leftovers
    for (const id of getBusySessionIds()) {
      completeStreamJob(id, 'aborted')
    }
    resetTurns()
  })

  it('markLiveTurn writes verifying onto TurnStore', () => {
    const c = new AbortController()
    const job = registerStreamJob('s-v', c, 'grok')
    markLiveTurn('s-v', 'verifying')
    expect(getTurn('s-v', job.turnId)?.status).toBe('verifying')
    completeStreamJob('s-v', 'done')
  })

  it('register and count running', () => {
    const c = new AbortController()
    registerStreamJob('s1', c, 'grok')
    expect(countRunningJobs()).toBe(1)
    expect(getBusySessionIds().has('s1')).toBe(true)
    completeStreamJob('s1', 'done')
    expect(countRunningJobs()).toBe(0)
  })

  it('detach leaves job running', () => {
    const c = new AbortController()
    registerStreamJob('s2', c)
    detachStreamJob('s2')
    expect(countRunningJobs()).toBe(1)
    completeStreamJob('s2', 'done')
  })

  it('stopStreamJob aborts', async () => {
    const { abortSession } = await import('../api/sessions')
    const order: string[] = []
    vi.mocked(abortSession).mockImplementation(async () => {
      order.push('abort')
      return { status: 'aborted' }
    })
    const c = new AbortController()
    c.signal.addEventListener('abort', () => order.push('controller'))
    registerStreamJob('s3', c)
    await stopStreamJob('s3')
    expect(c.signal.aborted).toBe(true)
    expect(order[0]).toBe('abort')
    expect(order).toContain('controller')
    expect(vi.mocked(abortSession).mock.calls.at(-1)?.[2]).toBeUndefined()
  })

  it('stopStreamJob sends the claim epoch from event start', async () => {
    const { abortSession } = await import('../api/sessions')
    vi.mocked(abortSession).mockClear()
    const c = new AbortController()
    registerStreamJob('s-epoch', c)
    setJobClaimEpoch('s-epoch', 7)
    await stopStreamJob('s-epoch')
    expect(vi.mocked(abortSession)).toHaveBeenCalledWith('s-epoch', 'stop', 7)
  })

  it('setJobClaimEpoch ignores a finished job and non-positive epochs', () => {
    const c = new AbortController()
    registerStreamJob('s-ep', c)
    setJobClaimEpoch('s-ep', 0)
    expect(getStreamJob('s-ep')?.claimEpoch).toBeUndefined()
    setJobClaimEpoch('s-ep', 3)
    expect(getStreamJob('s-ep')?.claimEpoch).toBe(3)
    completeStreamJob('s-ep', 'done')
    setJobClaimEpoch('s-ep', 9)
    expect(getStreamJob('s-ep')?.claimEpoch).not.toBe(9)
  })

  it('completeStreamJob does not revive terminal status', async () => {
    const c = new AbortController()
    registerStreamJob('s4', c)
    await stopStreamJob('s4')
    // stop already set aborted — a late onDone must not flip to done
    completeStreamJob('s4', 'done')
    expect(countRunningJobs()).toBe(0)
  })

  it('per-job paint accumulates while detached (multi-tab isolation)', () => {
    const a = new AbortController()
    const b = new AbortController()
    registerStreamJob('tab-a', a, 'grok')
    registerStreamJob('tab-b', b, 'gpt')
    detachStreamJob('tab-a')
    appendJobToken('tab-a', 'Hello ')
    appendJobToken('tab-a', 'from A')
    appendJobThinking('tab-a', 'think-a')
    appendJobToken('tab-b', 'Only B')
    setJobProcessSteps('tab-a', [
      {
        id: '1',
        name: 'file_read',
        label: 'Reading file',
        status: 'done',
        startedAt: 1,
      },
    ])
    const paintA = getJobPaint('tab-a')
    const paintB = getJobPaint('tab-b')
    expect(paintA?.partialText).toBe('Hello from A')
    expect(paintA?.partialThinking).toBe('think-a')
    expect(paintA?.processSteps).toHaveLength(1)
    expect(paintB?.partialText).toBe('Only B')
    expect(paintB?.processSteps).toHaveLength(0)
    replaceJobThinking('tab-a', 'round-two')
    expect(getJobPaint('tab-a')?.partialThinking).toBe('round-two')
    completeStreamJob('tab-a', 'done')
    completeStreamJob('tab-b', 'done')
  })

  it('abort UX: Stopped marker + uiCommitted double-commit guard', async () => {
    expect(withStoppedMarker('Hello world')).toContain('_[Stopped]_')
    expect(withStoppedMarker('done\n\n_[Stopped]_')).toBe('done\n\n_[Stopped]_')
    expect(withStoppedMarker('')).toBe('_[Stopped]_')
    expect(shouldRestoreStoppedPartial([], 'Partial answer')).toBe(true)
    expect(
      shouldRestoreStoppedPartial(
        [{ role: 'user', content: 'hi' }],
        'Partial answer',
      ),
    ).toBe(true)
    expect(
      shouldRestoreStoppedPartial(
        [{ role: 'assistant', content: 'Partial answer\n\n_[Stopped]_' }],
        'Partial answer',
      ),
    ).toBe(false)
    expect(
      shouldRestoreStoppedPartial(
        [{ role: 'assistant', content: 'Partial answer' }],
        'Partial answer',
      ),
    ).toBe(false)

    const c = new AbortController()
    registerStreamJob('abort-ux', c)
    appendJobToken('abort-ux', 'Partial answer')
    markJobUiCommitted('abort-ux')
    expect(isJobUiCommitted('abort-ux')).toBe(true)
    await stopStreamJob('abort-ux')
    expect(getStreamJob('abort-ux')?.status).toBe('aborted')
    // Late finishOk as done must not revive
    completeStreamJob('abort-ux', 'done')
    expect(getStreamJob('abort-ux')?.status).toBe('aborted')
  })
})

describe('turn identity on a stream job', () => {
  afterEach(() => {
    for (const id of getBusySessionIds()) completeStreamJob(id, 'aborted')
    resetTurns()
  })

  it('records the turn id from `start` and the seq watermark from the stream', () => {
    registerStreamJob('s-id', new AbortController())
    expect(getJobRequestId('s-id')).toBeUndefined()
    setJobRequestId('s-id', 'req-7')
    expect(getJobRequestId('s-id')).toBe('req-7')
    setJobLastSeq('s-id', 5)
    setJobLastSeq('s-id', 9)
    // A watermark never moves backwards — a late replay frame cannot lower it.
    setJobLastSeq('s-id', 3)
    expect(getJobLastSeq('s-id')).toBe(9)
  })

  it('seeds identity when the job is a re-attach', () => {
    registerStreamJob('s-att', new AbortController(), 'grok', {
      requestId: 'req-8',
      lastSeq: 12,
      attached: true,
    })
    expect(getJobRequestId('s-att')).toBe('req-8')
    expect(getJobLastSeq('s-att')).toBe(12)
    expect(getStreamJob('s-att')?.attached).toBe(true)
  })

  it('reports a turn it already follows so a second attach is a no-op', () => {
    registerStreamJob('s-follow', new AbortController(), undefined, {
      requestId: 'req-8',
    })
    expect(isFollowingTurn('s-follow', 'req-8')).toBe(true)
    expect(isFollowingTurn('s-follow', 'req-9')).toBe(false)
    expect(isFollowingTurn('s-other', 'req-8')).toBe(false)
  })

  it('treats a turn that has not named itself yet as already followed', () => {
    registerStreamJob('s-fresh', new AbortController())
    expect(isFollowingTurn('s-fresh', 'req-anything')).toBe(true)
  })

  it('stops following once the job is terminal', () => {
    registerStreamJob('s-endz', new AbortController(), undefined, { requestId: 'req-8' })
    completeStreamJob('s-endz', 'done')
    expect(isFollowingTurn('s-endz', 'req-8')).toBe(false)
  })
})
