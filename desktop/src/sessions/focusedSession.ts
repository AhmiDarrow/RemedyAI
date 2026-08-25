/**
 * Focused chat tab — used so a Stopped session is not auto-resumed from
 * another tab's in-flight 409 retry / detached stream.
 *
 * Owner clicked the tab (or New Session) → focused. A stream POST for that
 * id sends X-Remedy-Session-Select so the sidecar may restore continuity.
 */

let focusedId = ''

export function setFocusedSessionId(id: string | null | undefined): void {
  focusedId = String(id || '').trim()
}

export function getFocusedSessionId(): string {
  return focusedId
}

export function isFocusedSession(sessionId: string): boolean {
  const sid = String(sessionId || '').trim()
  return Boolean(sid) && sid === focusedId
}

/** Header for a user send from the focused tab. */
export function sessionSelectHeaders(sessionId: string): Record<string, string> {
  return isFocusedSession(sessionId) ? { 'X-Remedy-Session-Select': '1' } : {}
}

/** After 409, only retry if this tab is still focused and not Stopped. */
export function shouldRetryStreamAfter409(
  sessionId: string,
  opts: { aborted: boolean },
): boolean {
  if (opts.aborted) return false
  return isFocusedSession(sessionId)
}
