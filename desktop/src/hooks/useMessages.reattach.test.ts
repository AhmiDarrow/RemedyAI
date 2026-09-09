/**
 * `useMessages` is a React hook over a live SSE transport; without jsdom or a
 * React test renderer (neither is in this project) a behavioural test here
 * would assert nothing. The decision it makes is tested for real in
 * `src/sessions/reattach.test.ts` and the transport in
 * `src/api/attachTurn.test.ts`; what is left to pin is that the hook reaches
 * for those and that the polling stopgap is gone for good.
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const hook = readFileSync(join(here, 'useMessages.ts'), 'utf8')
const sessionsApi = readFileSync(join(here, '../api/sessions.ts'), 'utf8')

describe('reattach on load', () => {
  it('asks the session for its claim and attaches to the named turn', () => {
    expect(hook).toContain("import { reattachPlan } from '../sessions/reattach'")
    expect(hook).toContain('void getSession(sessionId)')
    expect(hook).toContain("if (plan.kind === 'attach')")
    expect(hook).toContain('attachToTurnRef.current?.(sessionId, plan.requestId')
  })

  it('opens the attach stream through the shared turn runner', () => {
    expect(hook).toContain("runTurnStream(sid, { kind: 'attach', requestId, after: 0, model })")
    expect(hook).toContain('attachTurn(targetId, run.requestId, handlers, { after: run.after })')
  })

  it('refuses to open a second stream for a turn it already paints', () => {
    expect(hook).toContain('if (isFollowingTurn(sid, requestId)) return')
  })

  it('records the turn id and the seq watermark from the stream', () => {
    expect(hook).toContain('setJobRequestId(targetId, info.requestId)')
    expect(hook).toContain('setJobLastSeq(targetId, seq)')
  })

  it('has no transcript poller left anywhere', () => {
    for (const gone of [
      'awaitRemoteReply',
      'nextRemotePollDelay',
      'shouldAwaitRemoteReply',
      'remoteReplyArrived',
      'remotePollTimerRef',
      'turnActive',
    ]) {
      expect(hook).not.toContain(gone)
    }
    expect(sessionsApi).not.toContain('turn-active')
  })

  it('re-attaches after an interrupted turn instead of leaving the page idle', () => {
    const interrupted = hook.indexOf('if (wasInterrupted) {')
    expect(interrupted).toBeGreaterThan(-1)
    expect(hook.indexOf('void getSession(targetId)', interrupted)).toBeGreaterThan(interrupted)
  })

  it('does not blame the owner when it cannot join the turn', () => {
    expect(hook).toContain("const joinFailed = run.kind === 'attach' && getJobLastSeq(targetId) === 0")
    expect(hook).toContain('void finishErr(errMsg, { quiet: joinFailed })')
    expect(hook).toContain("if (!opts?.quiet && sessionIdRef.current === targetId) {")
  })
})
