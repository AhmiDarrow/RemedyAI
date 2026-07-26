import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

const NEAR_PX = 140

type Options = {
  /** When this flips true (e.g. streaming/live), re-pin and follow again. */
  followActive?: boolean
  /** Re-run pin after React commits these (tokens, steps, thinking…). */
  deps?: unknown[]
  /** Show jump control whenever not stuck (not only while streaming). */
  alwaysOfferJump?: boolean
}

/**
 * Stick a scroll container to the bottom while content grows
 * (tokens, thinking, tools, process dumps) unless the user scrolls up.
 * Jump button re-enables following.
 *
 * Callback refs update state so effects re-bind when the scroller mounts
 * (e.g. Process panel expands after first paint).
 */
export function useStickToBottom(options: Options = {}) {
  const { followActive = false, deps = [], alwaysOfferJump = false } = options

  const scrollerRef = useRef<HTMLElement | null>(null)
  const contentRef = useRef<HTMLElement | null>(null)
  const [scrollerEl, setScrollerEl] = useState<HTMLElement | null>(null)
  const [contentEl, setContentEl] = useState<HTMLElement | null>(null)
  const stickRef = useRef(true)
  const autoLockRef = useRef(false)
  const lockTimerRef = useRef<number | null>(null)
  const [showJump, setShowJump] = useState(false)

  const setScroller = useCallback((node: HTMLElement | null) => {
    scrollerRef.current = node
    setScrollerEl(node)
  }, [])

  const setContent = useCallback((node: HTMLElement | null) => {
    contentRef.current = node
    setContentEl(node)
  }, [])

  const pinToBottom = useCallback((smooth = false) => {
    const el = scrollerRef.current
    if (!el) return
    autoLockRef.current = true
    if (lockTimerRef.current != null) {
      window.clearTimeout(lockTimerRef.current)
      lockTimerRef.current = null
    }
    const apply = () => {
      // Always clamp to true bottom (scrollHeight alone can be off-by-one on some engines)
      const max = Math.max(0, el.scrollHeight - el.clientHeight)
      if (smooth) {
        el.scrollTo({ top: max, behavior: 'smooth' })
      } else {
        el.scrollTop = max
      }
    }
    apply()
    requestAnimationFrame(() => {
      apply()
      requestAnimationFrame(() => {
        apply()
        // Longer lock so rapid dump growth doesn't look like user scroll
        lockTimerRef.current = window.setTimeout(() => {
          autoLockRef.current = false
          lockTimerRef.current = null
        }, smooth ? 420 : 120)
      })
    })
    setShowJump(false)
  }, [])

  const syncStickFromScroll = useCallback(() => {
    const el = scrollerRef.current
    if (!el || autoLockRef.current) return
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    const near = distance <= NEAR_PX
    stickRef.current = near
    const offer = !near && (followActive || alwaysOfferJump)
    setShowJump(offer)
  }, [followActive, alwaysOfferJump])

  // User scroll / wheel / touch → may detach from bottom
  useEffect(() => {
    const el = scrollerEl
    if (!el) return
    const onScroll = () => syncStickFromScroll()
    const onUser = () => {
      // Only detach on real user intent, not layout-driven scroll
      if (autoLockRef.current) return
      requestAnimationFrame(syncStickFromScroll)
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    el.addEventListener('wheel', onUser, { passive: true })
    el.addEventListener('touchmove', onUser, { passive: true })
    return () => {
      el.removeEventListener('scroll', onScroll)
      el.removeEventListener('wheel', onUser)
      el.removeEventListener('touchmove', onUser)
    }
  }, [scrollerEl, syncStickFromScroll])

  // New active turn → always re-follow
  useEffect(() => {
    if (followActive) {
      stickRef.current = true
      pinToBottom(false)
    }
  }, [followActive, pinToBottom, scrollerEl])

  // After DOM commits (tokens / thinking / tools / process)
  useLayoutEffect(() => {
    if (!stickRef.current || !scrollerEl) return
    pinToBottom(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- caller passes growth deps
  }, [pinToBottom, followActive, scrollerEl, ...deps])

  // Height growth (markdown reflow, expanding process, images)
  useEffect(() => {
    const content = contentEl
    if (!content) return
    const ro = new ResizeObserver(() => {
      if (stickRef.current) pinToBottom(false)
    })
    ro.observe(content)
    return () => ro.disconnect()
  }, [pinToBottom, contentEl, followActive])

  // Subtree mutations (thinking text, raw dumps) while following
  useEffect(() => {
    const content = contentEl
    if (!content || !followActive) return
    const mo = new MutationObserver(() => {
      if (stickRef.current) pinToBottom(false)
    })
    mo.observe(content, {
      childList: true,
      subtree: true,
      characterData: true,
    })
    return () => mo.disconnect()
  }, [followActive, pinToBottom, contentEl])

  const jumpLatest = useCallback(() => {
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
    isStuck: () => stickRef.current,
  }
}
