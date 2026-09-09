import { describe, expect, it } from 'vitest'
import { reattachPlan } from './reattach'

describe('reattachPlan', () => {
  it('shows a claimed session as work in progress by attaching to its turn', () => {
    expect(
      reattachPlan({
        liveness: { claimed: true, active_request_id: 'req-9' },
        localJobRunning: false,
      }),
    ).toEqual({ kind: 'attach', requestId: 'req-9' })
  })

  it('paints an unclaimed session idle', () => {
    expect(
      reattachPlan({
        liveness: { claimed: false, active_request_id: null },
        localJobRunning: false,
      }),
    ).toEqual({ kind: 'idle' })
    expect(reattachPlan({ liveness: null, localJobRunning: false })).toEqual({ kind: 'idle' })
  })

  it('never opens a second stream for a turn this webview already paints', () => {
    expect(
      reattachPlan({
        liveness: { claimed: true, active_request_id: 'req-9' },
        localJobRunning: true,
        localRequestId: 'req-9',
      }),
    ).toEqual({ kind: 'follow-local' })
  })

  it('leaves a just-started turn alone before `start` names it', () => {
    expect(
      reattachPlan({
        liveness: { claimed: true, active_request_id: 'req-9' },
        localJobRunning: true,
      }),
    ).toEqual({ kind: 'follow-local' })
  })

  it('attaches when the running claim belongs to a different turn than the local job', () => {
    expect(
      reattachPlan({
        liveness: { claimed: true, active_request_id: 'req-10' },
        localJobRunning: true,
        localRequestId: 'req-9',
      }),
    ).toEqual({ kind: 'attach', requestId: 'req-10' })
  })

  it('still shows working when the server claims a turn it cannot name', () => {
    expect(
      reattachPlan({
        liveness: { claimed: true, active_request_id: null },
        localJobRunning: false,
      }),
    ).toEqual({ kind: 'working' })
  })

  it('lets a local job finish on its own stream once the claim is gone', () => {
    expect(
      reattachPlan({
        liveness: { claimed: false },
        localJobRunning: true,
        localRequestId: 'req-9',
      }),
    ).toEqual({ kind: 'follow-local' })
  })
})
