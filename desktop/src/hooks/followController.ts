/**
 * Pure follow/pause state machine behind useStickToBottom.
 *
 * Kept free of DOM and React so the rules are unit-testable:
 *  - follow (stick to the floor) only while the reader is within NEAR_PX;
 *  - any gesture that moves away from the floor pauses following;
 *  - returning to the floor, the jump pill, End, or a reattach resumes it;
 *  - our own programmatic scrolls (pins) are echoes, never gestures;
 *  - while a toggle is being anchored nothing pins, and afterwards the
 *    follow state is re-derived from where the reader actually is.
 */

/** Reader counts as "at the bottom" within this many px of the floor. */
export const NEAR_PX = 48

export interface ScrollMetrics {
  scrollTop: number
  scrollHeight: number
  clientHeight: number
}

export interface FollowState {
  /** Following the floor. */
  stuck: boolean
  /** scrollTop we last wrote ourselves; a scroll landing here is an echo. */
  expectedTop: number | null
  /** A smooth glide is ours until this time (ms). */
  lockUntil: number
  /** Just pinned instantly: floor-adjacent scrolls until then are settling. */
  instantUntil: number
  /** A toggle is being anchored; pins wait until it ends. */
  anchoring: boolean
}

export type FollowEvent =
  | { type: 'scroll'; metrics: ScrollMetrics; now: number }
  | { type: 'wheel'; deltaY: number; canScroll: boolean }
  | { type: 'key'; key: string; canScroll: boolean }
  | { type: 'pinned'; top: number; now: number; instant?: boolean; smoothMs?: number }
  | { type: 'attach' }
  | { type: 'detach' }
  | { type: 'anchorStart' }
  | { type: 'anchorEnd'; metrics: ScrollMetrics; top: number }

export const INSTANT_SETTLE_MS = 280

export function initialFollowState(startAtBottom = true): FollowState {
  return { stuck: startAtBottom, expectedTop: null, lockUntil: 0, instantUntil: 0, anchoring: false }
}

export function distanceFromBottom(m: ScrollMetrics): number {
  return m.scrollHeight - m.scrollTop - m.clientHeight
}

export function isNearBottom(m: ScrollMetrics, near = NEAR_PX): boolean {
  return distanceFromBottom(m) <= near
}

export function canScroll(m: Pick<ScrollMetrics, 'scrollHeight' | 'clientHeight'>): boolean {
  return m.scrollHeight - m.clientHeight > 1
}

/** Keys that mean "I want to read further up". */
const UP_KEYS = new Set(['ArrowUp', 'PageUp', 'Home'])

export function reduceFollow(s: FollowState, e: FollowEvent): FollowState {
  switch (e.type) {
    case 'scroll': {
      if (s.anchoring) return s
      if (e.now < s.lockUntil) return s
      if (s.expectedTop != null && Math.abs(e.metrics.scrollTop - s.expectedTop) <= 1) return s
      const near = isNearBottom(e.metrics)
      // Just pinned and still at the floor: layout settling, not a gesture.
      if (e.now < s.instantUntil && near) return s
      return { ...s, expectedTop: null, stuck: near }
    }
    case 'wheel':
      return e.deltaY < 0 && e.canScroll && s.stuck ? { ...s, stuck: false } : s
    case 'key':
      if (!e.canScroll) return s
      if (UP_KEYS.has(e.key)) return s.stuck ? { ...s, stuck: false } : s
      return s
    case 'pinned':
      return {
        ...s,
        expectedTop: e.top,
        lockUntil: e.smoothMs ? e.now + e.smoothMs : s.lockUntil,
        instantUntil: e.instant ? e.now + INSTANT_SETTLE_MS : s.instantUntil,
      }
    case 'attach':
      return s.stuck ? s : { ...s, stuck: true }
    case 'detach':
      return s.stuck ? { ...s, stuck: false } : s
    case 'anchorStart':
      return { ...s, anchoring: true }
    case 'anchorEnd':
      return { ...s, anchoring: false, expectedTop: e.top, stuck: isNearBottom(e.metrics) }
  }
}

/** Whether a content change should pin to the floor right now. */
export function shouldPin(s: FollowState): boolean {
  return s.stuck && !s.anchoring
}

/**
 * Scroll delta that keeps a toggled element where the reader saw it.
 * `before`/`after` are the element's viewport tops around the toggle.
 */
export function anchorDelta(before: number, after: number): number {
  const d = after - before
  return Math.abs(d) < 0.5 ? 0 : d
}

/** End jumps to latest unless the reader is typing somewhere. */
export function isTypingTarget(t: { tagName?: string; isContentEditable?: boolean } | null): boolean {
  if (!t) return false
  if (t.isContentEditable) return true
  const tag = (t.tagName || '').toUpperCase()
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
}
