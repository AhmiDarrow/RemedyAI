import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

/** User counts as "at the bottom" within this many px of the floor. */
const NEAR_PX = 48

type Options = {
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
}

/**
 * Stick-to-bottom contract:
 *
 * - Auto-follow ONLY while the user is already at the bottom (≤ NEAR_PX).
 * - Any user scroll away from the floor (wheel, touch, keyboard, scrollbar
 *   drag) detaches. Token appends, image decode, code blocks, process-trace
 *   growth — none of these re-attach.
 * - Re-attach only when the user scrolls back to the floor, clicks the jump
 *   pill, or `reattachKey` changes.
 * - While detached we never write scrollTop; native scroll anchoring keeps
 *   the view steady when content above the viewport grows.
 *
 * Programmatic scrolls are told apart from user scrolls by value: every pin
 * records the scrollTop it produced, and a scroll event landing on exactly
 * that value is an echo, not a gesture.
 */
export function useStickToBottom(options: Options = {}) {
  const {
    followActive = false,
    deps = [],
    alwaysOfferJump = false,
    reattachKey,
    startAtBottom = true,
  } = options

  const scrollerRef = useRef<HTMLElement | null>(null)
  const contentRef = useRef<HTMLElement | null>(null)
  const [scrollerEl, setScrollerEl] = useState<HTMLElement | null>(null)
  const [contentEl, setContentEl] = useState<HTMLElement | null>(null)

  const stuckRef = useRef(startAtBottom)
  /** scrollTop we last set ourselves; a scroll event landing here is an echo. */
  const expectedTopRef = useRef<number | null>(null)
  /** During a smooth jump, intermediate scroll events are ours — ignore until this time. */
  const lockUntilRef = useRef(0)
  const pinRafRef = useRef<number | null>(null)

  const [detached, setDetachedState] = useState(false)
  const detachedRef = useRef(false)
  const setDetached = useCallback((next: boolean) => {
    if (detachedRef.current === next) return
    detachedRef.current = next
    setDetachedState(next)
  }, [])

  const setScroller = useCallback((node: HTMLElement | null) => {
    scrollerRef.current = node
    setScrollerEl(node)
  }, [])

  const setContent = useCallback((node: HTMLElement | null) => {
    contentRef.current = node
    setContentEl(node)
  }, [])

  const distanceFromBottom = (el: HTMLElement) =>
    el.scrollHeight - el.scrollTop - el.clientHeight

  const canScroll = (el: HTMLElement) => el.scrollHeight - el.clientHeight > 1

  const attach = useCallback(() => {
    stuckRef.current = true
    setDetached(false)
  }, [setDetached])

  const detach = useCallback(() => {
    stuckRef.current = false
    if (pinRafRef.current != null) {
      window.cancelAnimationFrame(pinRafRef.current)
      pinRafRef.current = null
    }
    setDetached(true)
  }, [setDetached])

  /** Coalesced (one per frame) scroll-to-floor, only while attached. */
  const pinToBottom = useCallback(
    (smooth = false) => {
      const el = scrollerRef.current
      if (!el || !stuckRef.current) return

      const apply = () => {
        if (!stuckRef.current) return
        const max = Math.max(0, el.scrollHeight - el.clientHeight)
        if (smooth) {
          lockUntilRef.current = performance.now() + 450
          el.scrollTo({ top: max, behavior: 'smooth' })
          expectedTopRef.current = max
          return
        }
        el.scrollTop = max
        // Read back — the browser clamps, and we compare echoes by value.
        expectedTopRef.current = el.scrollTop
      }

      if (smooth) {
        apply()
        return
      }
      if (pinRafRef.current != null) return
      pinRafRef.current = window.requestAnimationFrame(() => {
        pinRafRef.current = null
        apply()
      })
    },
    [],
  )

  useEffect(() => {
    const el = scrollerEl
    if (!el) return

    const onScroll = () => {
      if (performance.now() < lockUntilRef.current) return
      const expected = expectedTopRef.current
      if (expected != null && Math.abs(el.scrollTop - expected) <= 1) {
        // Echo of our own pin (or a clamp onto it) — not a gesture.
        return
      }
      expectedTopRef.current = null
      if (distanceFromBottom(el) <= NEAR_PX) attach()
      else detach()
    }

    // Wheel up = intent to read history, even before the scroll event lands.
    const onWheel = (e: WheelEvent) => {
      if (e.deltaY < 0 && canScroll(el)) detach()
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (!canScroll(el)) return
      if (e.key === 'ArrowUp' || e.key === 'PageUp' || e.key === 'Home') detach()
    }

    el.addEventListener('scroll', onScroll, { passive: true })
    el.addEventListener('wheel', onWheel, { passive: true })
    el.addEventListener('keydown', onKeyDown)
    return () => {
      el.removeEventListener('scroll', onScroll)
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('keydown', onKeyDown)
    }
  }, [scrollerEl, attach, detach])

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
    attach()
    const el = scrollerRef.current
    if (!el) return
    const max = Math.max(0, el.scrollHeight - el.clientHeight)
    el.scrollTop = max
    expectedTopRef.current = el.scrollTop
    lockUntilRef.current = performance.now() + 280
  }, [reattachKey, attach])

  // New scroller (mount / session switch) starts at the floor.
  useLayoutEffect(() => {
    if (!scrollerEl || !startAtBottom) return
    attach()
    pinToBottom(false)
  }, [scrollerEl, startAtBottom, attach, pinToBottom])

  // Re-pin from content deps while attached (tokens, tool rows, todos…).
  useLayoutEffect(() => {
    if (!scrollerEl || !stuckRef.current) return
    pinToBottom(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pinToBottom, scrollerEl, ...deps])

  // Layout growth (images decoding, code blocks, process trace) and viewport
  // shrink (composer autosize, window resize): attached → pin; detached → leave
  // scrollTop alone so native anchoring keeps the reading position.
  useEffect(() => {
    const content = contentEl
    const scroller = scrollerEl
    if (!content || !scroller) return
    const ro = new ResizeObserver(() => {
      if (!stuckRef.current) return
      pinToBottom(false)
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

  // Instant, not smooth: a smooth glide emits intermediate scroll events that
  // look like gestures once content keeps growing underneath it.
  const jumpLatest = useCallback(() => {
    attach()
    const el = scrollerRef.current
    if (el) {
      const max = Math.max(0, el.scrollHeight - el.clientHeight)
      el.scrollTop = max
      expectedTopRef.current = el.scrollTop
      lockUntilRef.current = performance.now() + 280
    } else {
      pinToBottom(false)
    }
  }, [attach, pinToBottom])

  const showJump = detached && (followActive || alwaysOfferJump)

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
    isStuck: () => stuckRef.current,
  }
}
