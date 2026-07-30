import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

const NEAR_PX = 80

type Options = {
  followActive?: boolean
  deps?: unknown[]
  alwaysOfferJump?: boolean
}

/**
 * Stick to bottom while following, but never fight the user when scrolling
 * through history — especially past images that change height as they decode.
 */
export function useStickToBottom(options: Options = {}) {
  const { followActive = false, deps = [], alwaysOfferJump = false } = options

  const scrollerRef = useRef<HTMLElement | null>(null)
  const contentRef = useRef<HTMLElement | null>(null)
  const [scrollerEl, setScrollerEl] = useState<HTMLElement | null>(null)
  const [contentEl, setContentEl] = useState<HTMLElement | null>(null)

  const stickRef = useRef(true)
  const userDetachedRef = useRef(false)
  const autoLockRef = useRef(false)
  const lockTimerRef = useRef<number | null>(null)
  const pinRafRef = useRef<number | null>(null)
  const lastScrollHeightRef = useRef(0)
  const showJumpRef = useRef(false)
  const [showJump, setShowJumpState] = useState(false)

  const setShowJump = useCallback((next: boolean) => {
    if (showJumpRef.current === next) return
    showJumpRef.current = next
    setShowJumpState(next)
  }, [])

  const setScroller = useCallback((node: HTMLElement | null) => {
    scrollerRef.current = node
    setScrollerEl(node)
  }, [])

  const setContent = useCallback((node: HTMLElement | null) => {
    contentRef.current = node
    setContentEl(node)
  }, [])

  const clearAutoLock = useCallback(() => {
    autoLockRef.current = false
    if (lockTimerRef.current != null) {
      window.clearTimeout(lockTimerRef.current)
      lockTimerRef.current = null
    }
  }, [])

  const pinToBottom = useCallback(
    (smooth = false) => {
      const el = scrollerRef.current
      if (!el || !stickRef.current || userDetachedRef.current) return

      const apply = () => {
        if (!stickRef.current || userDetachedRef.current) return
        const max = Math.max(0, el.scrollHeight - el.clientHeight)
        autoLockRef.current = true
        if (smooth) el.scrollTo({ top: max, behavior: 'smooth' })
        else el.scrollTop = max
        lastScrollHeightRef.current = el.scrollHeight
        if (lockTimerRef.current != null) window.clearTimeout(lockTimerRef.current)
        lockTimerRef.current = window.setTimeout(
          () => {
            autoLockRef.current = false
            lockTimerRef.current = null
          },
          smooth ? 360 : 40,
        )
      }

      if (smooth) {
        apply()
        setShowJump(false)
        return
      }
      if (pinRafRef.current != null) return
      pinRafRef.current = window.requestAnimationFrame(() => {
        pinRafRef.current = null
        apply()
        setShowJump(false)
      })
    },
    [setShowJump],
  )

  const distanceFromBottom = (el: HTMLElement) =>
    el.scrollHeight - el.scrollTop - el.clientHeight

  const syncStickFromScroll = useCallback(() => {
    const el = scrollerRef.current
    if (!el || autoLockRef.current) return
    const near = distanceFromBottom(el) <= NEAR_PX
    if (near) {
      userDetachedRef.current = false
      stickRef.current = true
      setShowJump(false)
    } else {
      userDetachedRef.current = true
      stickRef.current = false
      setShowJump(followActive || alwaysOfferJump)
    }
    lastScrollHeightRef.current = el.scrollHeight
  }, [followActive, alwaysOfferJump, setShowJump])

  useEffect(() => {
    const el = scrollerEl
    if (!el) return

    const detach = () => {
      userDetachedRef.current = true
      stickRef.current = false
      clearAutoLock()
      if (pinRafRef.current != null) {
        window.cancelAnimationFrame(pinRafRef.current)
        pinRafRef.current = null
      }
      setShowJump(true)
    }

    const onScroll = () => syncStickFromScroll()
    // Any wheel while not at the floor: detach (covers trackpads / image regions)
    const onWheel = (e: WheelEvent) => {
      if (e.deltaY < 0) {
        detach()
        return
      }
      // Scrolling down: only stay stuck if already near bottom after event
      requestAnimationFrame(() => {
        const sc = scrollerRef.current
        if (!sc) return
        if (distanceFromBottom(sc) > NEAR_PX) detach()
      })
    }
    const onTouchMove = () => detach()

    el.addEventListener('scroll', onScroll, { passive: true })
    el.addEventListener('wheel', onWheel, { passive: true })
    el.addEventListener('touchmove', onTouchMove, { passive: true })
    return () => {
      el.removeEventListener('scroll', onScroll)
      el.removeEventListener('wheel', onWheel)
      el.removeEventListener('touchmove', onTouchMove)
    }
  }, [scrollerEl, syncStickFromScroll, clearAutoLock, setShowJump])

  const wasFollowRef = useRef(false)
  useEffect(() => {
    if (followActive && !wasFollowRef.current) {
      userDetachedRef.current = false
      stickRef.current = true
      pinToBottom(false)
    }
    wasFollowRef.current = followActive
  }, [followActive, pinToBottom, scrollerEl])

  // Only re-pin from message deps while actively streaming (not idle image decode)
  useLayoutEffect(() => {
    if (!followActive) return
    if (!stickRef.current || userDetachedRef.current || !scrollerEl) return
    pinToBottom(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pinToBottom, followActive, scrollerEl, ...deps])

  /**
   * Image height changes:
   * - following → one coalesced pin
   * - detached → keep visual anchor (adjust scrollTop by height delta so
   *   scrolling through images does not jump)
   */
  useEffect(() => {
    const content = contentEl
    if (!content) return

    const ro = new ResizeObserver(() => {
      const scroller = scrollerRef.current
      if (!scroller) return
      const h = scroller.scrollHeight
      const prev = lastScrollHeightRef.current || h
      const delta = h - prev
      lastScrollHeightRef.current = h
      if (Math.abs(delta) < 1) return

      // Reading history: never pin and never rewrite scrollTop.
      // (Adjusting by delta mis-fires when images below the viewport load.)
      if (userDetachedRef.current || !stickRef.current) return

      // Following latest: coalesce pin (streaming tokens / new bottom content)
      pinToBottom(false)
    })
    ro.observe(content)
    const sc = scrollerRef.current
    if (sc) lastScrollHeightRef.current = sc.scrollHeight
    return () => ro.disconnect()
  }, [pinToBottom, contentEl, followActive])

  useEffect(() => {
    const content = contentEl
    if (!content || !followActive) return
    const mo = new MutationObserver(() => {
      if (stickRef.current && !userDetachedRef.current) pinToBottom(false)
    })
    mo.observe(content, { childList: true, subtree: true, characterData: true })
    return () => mo.disconnect()
  }, [followActive, pinToBottom, contentEl])

  useEffect(
    () => () => {
      if (pinRafRef.current != null) window.cancelAnimationFrame(pinRafRef.current)
      clearAutoLock()
    },
    [clearAutoLock],
  )

  const jumpLatest = useCallback(() => {
    userDetachedRef.current = false
    stickRef.current = true
    pinToBottom(true)
  }, [pinToBottom])

  return {
    setScroller,
    setContent,
    scrollerRef,
    contentRef,
    showJump,
    jumpLatest,
    pinToBottom,
    isStuck: () => stickRef.current && !userDetachedRef.current,
  }
}
