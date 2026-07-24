import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

const NEAR_PX = 100

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
 */
export function useStickToBottom(options: Options = {}) {
  const { followActive = false, deps = [], alwaysOfferJump = false } = options

  const scrollerRef = useRef<HTMLElement | null>(null)
  const contentRef = useRef<HTMLElement | null>(null)
  const stickRef = useRef(true)
  const autoLockRef = useRef(false)
  const lockTimerRef = useRef<number | null>(null)
  const [showJump, setShowJump] = useState(false)

  const setScroller = useCallback((node: HTMLElement | null) => {
    scrollerRef.current = node
  }, [])

  const setContent = useCallback((node: HTMLElement | null) => {
    contentRef.current = node
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
      const max = el.scrollHeight - el.clientHeight
      if (smooth) {
        el.scrollTo({ top: Math.max(0, max), behavior: 'smooth' })
      } else {
        el.scrollTop = Math.max(0, el.scrollHeight)
      }
    }
    apply()
    requestAnimationFrame(() => {
      apply()
      requestAnimationFrame(() => {
        apply()
        lockTimerRef.current = window.setTimeout(() => {
          autoLockRef.current = false
          lockTimerRef.current = null
        }, smooth ? 380 : 64)
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
    const el = scrollerRef.current
    if (!el) return
    const onScroll = () => syncStickFromScroll()
    const onUser = () => {
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
  }, [syncStickFromScroll, followActive])

  // New active turn → always re-follow
  useEffect(() => {
    if (followActive) {
      stickRef.current = true
      pinToBottom(false)
    }
  }, [followActive, pinToBottom])

  // After DOM commits (tokens / thinking / tools / process)
  useLayoutEffect(() => {
    if (!stickRef.current) return
    pinToBottom(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- caller passes growth deps
  }, [pinToBottom, followActive, ...deps])

  // Height growth (markdown reflow, expanding process, images)
  useEffect(() => {
    const content = contentRef.current
    if (!content) return
    const ro = new ResizeObserver(() => {
      if (stickRef.current) pinToBottom(false)
    })
    ro.observe(content)
    return () => ro.disconnect()
  }, [pinToBottom, followActive])

  // Subtree mutations (thinking text, raw dumps) while following
  useEffect(() => {
    const content = contentRef.current
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
  }, [followActive, pinToBottom])

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
