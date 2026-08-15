/** Per-chat composer draft / attachment stash (do not share across tabs). */

export const NONE_SESSION_KEY = '_none'

export function sessionStashKey(sessionId?: string | null): string {
  const s = String(sessionId || '').trim()
  return s || NONE_SESSION_KEY
}

/**
 * Park `current` under `prevKey` and restore `nextKey` (or `empty`).
 * Empty-shell (`_none`) only carries onto a session this composer just created
 * (`carryNoneTo`). Clicking an existing chat must not inherit the draft.
 */
export function swapSessionStash<T>(
  stash: Map<string, T>,
  prevKey: string,
  nextKey: string,
  current: T,
  empty: T,
  carryNoneTo?: string | null,
): { key: string; value: T; carried: boolean } {
  if (nextKey === prevKey) {
    return { key: prevKey, value: current, carried: false }
  }
  if (
    prevKey === NONE_SESSION_KEY
    && nextKey !== NONE_SESSION_KEY
    && carryNoneTo
    && carryNoneTo === nextKey
  ) {
    // Live `current` wins — the user may have edited after parking _none.
    stash.set(nextKey, current)
    stash.delete(NONE_SESSION_KEY)
    return { key: nextKey, value: current, carried: true }
  }
  stash.set(prevKey, current)
  return {
    key: nextKey,
    value: stash.has(nextKey) ? (stash.get(nextKey) as T) : empty,
    carried: false,
  }
}
