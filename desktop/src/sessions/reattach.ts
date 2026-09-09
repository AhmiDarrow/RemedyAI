/**
 * Reconnect to a turn the server is still running.
 *
 * A webview reload (or a dropped SSE) leaves the session mid-turn on the
 * server with this client painting nothing. `GET /api/sessions/{id}` answers
 * that with `claimed` + `active_request_id`; the turn log answers the rest
 * through `GET /api/sessions/{id}/stream/attach?request_id=…&after=<seq>`,
 * which replays what was missed and then tails the live turn.
 *
 * This module holds the decision — pure, so the rule that a claimed session
 * shows work in progress is testable without a browser.
 */

/** The liveness fields a session row carries (`GET /sessions`, `GET /sessions/{id}`). */
export type SessionLiveness = {
  claimed?: boolean
  active_request_id?: string | null
}

export type ReattachPlan =
  /** Nothing is running server-side — paint the session idle. */
  | { kind: 'idle' }
  /** This webview already streams that turn; leave it alone (idempotence). */
  | { kind: 'follow-local' }
  /**
   * A turn is running but the server did not name it (older sidecar, or the
   * claim landed before the log). Show the working state; the reply arrives
   * over the session-events refresh.
   */
  | { kind: 'working' }
  /** Replay this turn from its log, then follow it live. */
  | { kind: 'attach'; requestId: string }

export function reattachPlan(input: {
  liveness: SessionLiveness | null | undefined
  /** This webview owns a running stream job for the session. */
  localJobRunning: boolean
  /** Turn id of that running job, once `event: start` named it. */
  localRequestId?: string
}): ReattachPlan {
  const { liveness, localJobRunning, localRequestId } = input
  const requestId = (liveness?.active_request_id || '').trim()
  if (!liveness?.claimed) {
    // A local job that outlives the claim finishes on its own stream; the
    // server simply has nothing more for us to join.
    return localJobRunning ? { kind: 'follow-local' } : { kind: 'idle' }
  }
  if (localJobRunning) {
    // A job that has not learned its id yet is the turn this webview just
    // started — never open a second stream for it.
    if (!localRequestId || !requestId || localRequestId === requestId) {
      return { kind: 'follow-local' }
    }
  }
  if (!requestId) return { kind: 'working' }
  return { kind: 'attach', requestId }
}
