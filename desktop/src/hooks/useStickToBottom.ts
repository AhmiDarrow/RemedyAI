import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import {
  anchorDelta,
  canScroll,
  initialFollowState,
  isTypingTarget,
  reduceFollow,
  shouldPin,
  type FollowEvent,
  type ScrollMetrics,
} from './followController'

export { NEAR_PX } from './followController'

type Options = {
  /** Empty welcome screens start at the top and must remain freely scrollable. */
  enabled?: boolean
  /** A live turn is in progress (stream / tool run). Only affects the jump pill. */
  followActive?: boolean
  /** Values whose change should re-pin while attached (tokens, tool events…). */
  deps?: unknown[]
  /** Offer the jump pill while detached even when nothing is streaming. */
  alwaysOfferJump?: boolean
  /**
   * Changing this value forces a re-attach + pin (e.g. the id of the newest
   * user message — the owner just sent something and expects to see it).
   */
  reattachKey?: unknown
  /** Begin attached (pinned to the floor) when the scroller mounts. Default true. */
  startAtBottom?: boolean
  /**
   * Window-level End key jumps to latest (ignored while typing in an input,
   * textarea, or contenteditable — focus is never moved).
   */
  endKeyJumps?: boolean
}

const metricsOf = (el: HTMLElement): ScrollMetrics => ({
  scrollTop: el.scrollTop,
  scrollHeight: el.scrollHeight,
  clientHeight: el.clientHeight,
})

export function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

/**
 * Stick-to-bottom for any scroller (the chat feed, the Working timeline).
 *
 * The rules live in followController (pure, unit-tested); this hook only
 * wires DOM events and writes scrollTop:
 *
 * - Auto-follow ONLY while the reader is at the bottom (≤ NEAR_PX).
 * - Any scroll away from the floor (wheel, touch, keyboard, scrollbar drag)
 *   pauses following. Content growth never re-attaches.
 * - Re-attach when the reader scrolls back down, clicks the jump pill,
 *   presses End, or `reattachKey` changes.
 * - While detached we never write scrollTop; native scroll anchoring keeps
 *   the view steady when content above the viewport grows.
 * - `anchorToggle` keeps an expanded/collapsed element where the reader saw
 *   it, then re-derives follow state from the resulting position.
 */
export function useStickToBottom(options: Options = {}) {
  const {
    enabled = true,
    followActive = false,
    deps = [],
    alwaysOfferJump = false,
    reattachKey,
    startAtBottom = true,
    endKeyJumps = false,
  } = options
  const enabledRef = useRef(enabled)
  enabledRef.current = enabled

  const scrollerRef = useRef<HTMLElement | null>(null)
  const contentRef = useRef<HTMLElement | null>(null)
  const [scrollerEl, setScrollerEl] = useState<HTMLElement | null>(null)
  const [contentEl, setContentEl] = useState<HTMLElement | null>(null)

  const stateRef = useRef(initialFollowState(startAtBottom))
  const pinRafRef = useRef<number | null>(null)
  const [detached, setDetachedState] = useState(false)

  const dispatch = useCallback((e: FollowEvent) => {
    const next = reduceFollow(stateRef.current, e)
    stateRef.current = next
    if (!next.stuck && pinRafRef.current != null) {
      window.cancelAnimationFrame(pinRafRef.current)
      pinRafRef.current = null
    }
    setDetachedState(!next.stuck)
  }, [])

  const setScroller = useCallback((node: HTMLElement | null) => {
    scrollerRef.current = node
    setScrollerEl(node)
  }, [])

  const setContent = useCallback((node: HTMLElement | null) => {
    contentRef.current = node
    setContentEl(node)
  }, [])

  /** Coalesced (one per frame) scroll-to-floor, only while following. */
  const pinToBottom = useCallback(
    (smooth = false) => {
      const el = scrollerRef.current
      if (!enabledRef.current || !el || !shouldPin(stateRef.current)) return
      const glide = smooth && !prefersReducedMotion()
      const apply = () => {
        if (!enabledRef.current || !shouldPin(stateRef.current)) return
        const max = Math.max(0, el.scrollHeight - el.clientHeight)
        if (glide) {
          el.scrollTo({ top: max, behavior: 'smooth' })
          dispatch({ type: 'pinned', top: max, now: performance.now(), smoothMs: 450 })
          return
        }
        el.scrollTop = max
        // Read back — the browser clamps, and echoes are compared by value.
        dispatch({ type: 'pinned', top: el.scrollTop, now: performance.now() })
      }
      if (glide) {
        apply()
        return
      }
      if (pinRafRef.current != null) return
      pinRafRef.current = window.requestAnimationFrame(() => {
        pinRafRef.current = null
        apply()
      })
    },
    [dispatch],
  )

  /**
   * Instant pin: attach and write scrollTop synchronously. Shared by the
   * reattachKey effect (Enter), the Jump pill and End so they never diverge.
   */
  const pinNow = useCallback(() => {
    if (!enabledRef.current) return
    dispatch({ type: 'attach' })
    const el = scrollerRef.current
    if (!el) return
    el.scrollTop = Math.max(0, el.scrollHeight - el.clientHeight)
    dispatch({ type: 'pinned', top: el.scrollTop, now: performance.now(), instant: true })
  }, [dispatch])

  useEffect(() => {
    const el = scrollerEl
    if (!el) return
    const onScroll = () =>
      dispatch({ type: 'scroll', metrics: metricsOf(el), now: performance.now() })
    // Wheel up = intent to read history, even before the scroll event lands.
    const onWheel = (e: WheelEvent) =>
      dispatch({ type: 'wheel', deltaY: e.deltaY, canScroll: canScroll(metricsOf(el)) })
    const onKeyDown = (e: KeyboardEvent) =>
      dispatch({ type: 'key', key: e.key, canScroll: canScroll(metricsOf(el)) })
    el.addEventListener('scroll', onScroll, { passive: true })
    el.addEventListener('wheel', onWheel, { passive: true })
    el.addEventListener('keydown', onKeyDown)
    return () => {
      el.removeEventListener('scroll', onScroll)
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('keydown', onKeyDown)
    }
  }, [scrollerEl, dispatch])

  // End = jump to latest, unless the reader is typing (composer keeps focus).
  useEffect(() => {
    if (!endKeyJumps) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'End' || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return
      if (isTypingTarget(e.target as HTMLElement | null)) return
      e.preventDefault()
      pinNow()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [endKeyJumps, pinNow])

  /**
   * Owner sent a prompt (reattachKey changed). Pin *before paint* — a
   * post-paint effect loses the race: the new bubble grows the scroller,
   * onScroll sees we are still far from the floor, detaches, and the
   * delayed pin no-ops. Then the owner is stuck with Jump to latest.
   */
  const prevReattachRef = useRef(reattachKey)
  useLayoutEffect(() => {
    if (reattachKey === undefined) return
    if (prevReattachRef.current === reattachKey) return
    prevReattachRef.current = reattachKey
    pinNow()
  }, [reattachKey, pinNow])

  // New scroller (mount / session switch) starts at the floor.
  useLayoutEffect(() => {
    if (scrollerEl && !enabled) {
      scrollerEl.scrollTop = 0
      return
    }
    if (!scrollerEl || !startAtBottom) return
    dispatch({ type: 'attach' })
    pinToBottom(false)
  }, [enabled, scrollerEl, startAtBottom, dispatch, pinToBottom])

  // Re-pin from content deps while following (tokens, tool rows, todos…).
  useLayoutEffect(() => {
    if (!scrollerEl || !shouldPin(stateRef.current)) return
    pinToBottom(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pinToBottom, scrollerEl, ...deps])

  // Layout growth (images, code blocks, timeline rows) and viewport shrink
  // (composer autosize, resize): following → pin; paused → leave scrollTop
  // alone so native anchoring keeps the reading position.
  useEffect(() => {
    const content = contentEl
    const scroller = scrollerEl
    if (!content || !scroller || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => {
      if (shouldPin(stateRef.current)) pinToBottom(false)
    })
    ro.observe(content)
    ro.observe(scroller)
    return () => ro.disconnect()
  }, [pinToBottom, contentEl, scrollerEl])

  useEffect(
    () => () => {
      if (pinRafRef.current != null) window.cancelAnimationFrame(pinRafRef.current)
    },
    [],
  )

  /**
   * Run a toggle (expand/collapse) without yanking the view: the element
   * stays at the same viewport offset, then follow state is re-derived.
   */
  const anchorToggle = useCallback(
    (anchor: HTMLElement | null, mutate: () => void) => {
      const el = scrollerRef.current
      if (!el || !anchor) {
        mutate()
        return
      }
      const before = anchor.getBoundingClientRect().top
      dispatch({ type: 'anchorStart' })
      flushSync(mutate)
      const d = anchorDelta(before, anchor.getBoundingClientRect().top)
      if (d) el.scrollTop += d
      dispatch({ type: 'anchorEnd', metrics: metricsOf(el), top: el.scrollTop })
    },
    [dispatch],
  )

  // Instant, not smooth: a smooth glide emits intermediate scroll events that
  // look like gestures once content keeps growing underneath it.
  const jumpLatest = pinNow

  const showJump = enabled && detached && (followActive || alwaysOfferJump)

  return {
    setScroller,
    setContent,
    scrollerRef,
    contentRef,
    /** User scrolled away from the floor. */
    detached,
    showJump,
    jumpLatest,
    pinToBottom,
    anchorToggle,
    isStuck: () => stateRef.current.stuck,
  }
}
