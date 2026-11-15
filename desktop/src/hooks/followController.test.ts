import { describe, expect, it } from 'vitest'
import {
  NEAR_PX,
  anchorDelta,
  initialFollowState,
  isTypingTarget,
  reduceFollow,
  shouldPin,
  type FollowEvent,
  type FollowState,
} from './followController'

const H = 2000
const C = 500
const at = (scrollTop: number) => ({ scrollTop, scrollHeight: H, clientHeight: C })
const floor = H - C
const run = (events: FollowEvent[], s: FollowState = initialFollowState()) =>
  events.reduce(reduceFollow, s)

describe('followController', () => {
  it('starts following and keeps following within NEAR_PX of the floor', () => {
    const s = run([{ type: 'scroll', metrics: at(floor - NEAR_PX), now: 1000 }])
    expect(s.stuck).toBe(true)
    expect(shouldPin(s)).toBe(true)
  })

  it('pauses when the reader scrolls more than NEAR_PX above the floor', () => {
    const s = run([{ type: 'scroll', metrics: at(floor - NEAR_PX - 1), now: 1000 }])
    expect(s.stuck).toBe(false)
    expect(shouldPin(s)).toBe(false)
  })

  it('stays paused while content grows below a reader who scrolled up', () => {
    let s = run([{ type: 'scroll', metrics: at(400), now: 1000 }])
    // Content grows (scrollHeight up) but the reader did not move.
    s = reduceFollow(s, {
      type: 'scroll',
      metrics: { scrollTop: 400, scrollHeight: 4000, clientHeight: C },
      now: 2000,
    })
    expect(s.stuck).toBe(false)
  })

  it('resumes following when the reader returns to the floor', () => {
    const s = run([
      { type: 'scroll', metrics: at(400), now: 1000 },
      { type: 'scroll', metrics: at(floor - 10), now: 2000 },
    ])
    expect(s.stuck).toBe(true)
  })

  it('treats a scroll landing on our own pin as an echo, not a gesture', () => {
    const s = run([
      { type: 'pinned', top: floor, now: 1000 },
      // Content grew between the pin and the event; still exactly our value.
      { type: 'scroll', metrics: { scrollTop: floor, scrollHeight: H + 900, clientHeight: C }, now: 1010 },
    ])
    expect(s.stuck).toBe(true)
  })

  it('ignores floor-adjacent settling right after an instant pin, but not a real drag', () => {
    const settled = run([
      { type: 'pinned', top: floor, now: 1000, instant: true },
      { type: 'scroll', metrics: at(floor - 20), now: 1100 },
    ])
    expect(settled.stuck).toBe(true)
    const dragged = run([
      { type: 'pinned', top: floor, now: 1000, instant: true },
      { type: 'scroll', metrics: at(200), now: 1100 },
    ])
    expect(dragged.stuck).toBe(false)
  })

  it('ignores intermediate events of our own smooth glide', () => {
    const s = run([
      { type: 'pinned', top: floor, now: 1000, smoothMs: 450 },
      { type: 'scroll', metrics: at(300), now: 1200 },
    ])
    expect(s.stuck).toBe(true)
  })

  it('pauses on wheel-up and up keys only when there is something to scroll', () => {
    expect(run([{ type: 'wheel', deltaY: -40, canScroll: true }]).stuck).toBe(false)
    expect(run([{ type: 'wheel', deltaY: -40, canScroll: false }]).stuck).toBe(true)
    expect(run([{ type: 'wheel', deltaY: 40, canScroll: true }]).stuck).toBe(true)
    for (const key of ['ArrowUp', 'PageUp', 'Home']) {
      expect(run([{ type: 'key', key, canScroll: true }]).stuck).toBe(false)
    }
    expect(run([{ type: 'key', key: 'ArrowDown', canScroll: true }]).stuck).toBe(true)
  })

  it('jump (attach) resumes following from a paused state', () => {
    const s = run([{ type: 'detach' }, { type: 'attach' }])
    expect(s.stuck).toBe(true)
  })

  it('does not pin while a toggle is anchored, then re-derives from position', () => {
    let s = run([{ type: 'anchorStart' }])
    expect(shouldPin(s)).toBe(false)
    // Scroll events during compensation are ours.
    s = reduceFollow(s, { type: 'scroll', metrics: at(100), now: 1000 })
    expect(s.stuck).toBe(true)
    // Expanding pushed the floor away: following pauses instead of yanking.
    s = reduceFollow(s, { type: 'anchorEnd', metrics: at(900), top: 900 })
    expect(s.anchoring).toBe(false)
    expect(s.stuck).toBe(false)
    // Collapsing near the floor resumes following.
    s = run([{ type: 'anchorStart' }, { type: 'anchorEnd', metrics: at(floor), top: floor }], s)
    expect(s.stuck).toBe(true)
  })

  it('computes the compensation delta for a toggled element', () => {
    expect(anchorDelta(300, 300.2)).toBe(0)
    expect(anchorDelta(300, 180)).toBe(-120)
    expect(anchorDelta(300, 420)).toBe(120)
  })

  it('never treats typing targets as End-key jumps', () => {
    expect(isTypingTarget({ tagName: 'textarea' })).toBe(true)
    expect(isTypingTarget({ tagName: 'INPUT' })).toBe(true)
    expect(isTypingTarget({ tagName: 'DIV', isContentEditable: true })).toBe(true)
    expect(isTypingTarget({ tagName: 'DIV' })).toBe(false)
    expect(isTypingTarget(null)).toBe(false)
  })
})
