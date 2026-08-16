/** useSplit — a slide-able divider the owner drags to size their windows.
 *
 * Returns the primary pane's share (0–1) and props for the divider element.
 * Pointer-drag and keyboard (arrows) both work; the chosen size persists
 * per key so the house remembers how the owner likes their rooms.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

export interface SplitDividerProps {
  role: 'separator'
  tabIndex: number
  'aria-orientation': 'vertical' | 'horizontal'
  'aria-valuenow': number
  'aria-valuemin': number
  'aria-valuemax': number
  'aria-label': string
  onPointerDown: (e: React.PointerEvent<HTMLElement>) => void
  onKeyDown: (e: React.KeyboardEvent<HTMLElement>) => void
  onDoubleClick: () => void
}

export function useSplit(opts: {
  storageKey: string
  /** 'x' = vertical divider (left/right panes); 'y' = horizontal (top/bottom). */
  axis: 'x' | 'y'
  initial?: number
  min?: number
  max?: number
  label?: string
}): {
  ratio: number
  dragging: boolean
  containerRef: React.RefObject<HTMLDivElement | null>
  dividerProps: SplitDividerProps
} {
  const { storageKey, axis, initial = 0.55, min = 0.25, max = 0.75, label } = opts
  const clamp = useCallback(
    (v: number) => Math.min(max, Math.max(min, v)),
    [min, max],
  )
  const [ratio, setRatio] = useState<number>(() => {
    try {
      const raw = localStorage.getItem(storageKey)
      const v = raw ? Number(raw) : NaN
      return Number.isFinite(v) ? clamp(v) : initial
    } catch {
      return initial
    }
  })
  const [dragging, setDragging] = useState(false)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const ratioRef = useRef(ratio)
  ratioRef.current = ratio

  const persist = useCallback(
    (v: number) => {
      try {
        localStorage.setItem(storageKey, String(v))
      } catch {
        /* */
      }
    },
    [storageKey],
  )

  const apply = useCallback(
    (v: number, save = true) => {
      const c = clamp(v)
      setRatio(c)
      if (save) persist(c)
    },
    [clamp, persist],
  )

  // Live listeners for the current drag, so unmount can detach them.
  const dragCleanupRef = useRef<(() => void) | null>(null)
  useEffect(() => () => dragCleanupRef.current?.(), [])

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      e.preventDefault()
      const el = containerRef.current
      if (!el) return
      setDragging(true)
      const target = e.currentTarget
      target.setPointerCapture?.(e.pointerId)
      const move = (ev: PointerEvent) => {
        // Recompute each move: the container can resize mid-drag.
        const rect = el.getBoundingClientRect()
        const frac =
          axis === 'x'
            ? (ev.clientX - rect.left) / Math.max(1, rect.width)
            : (ev.clientY - rect.top) / Math.max(1, rect.height)
        apply(frac, false) // live-follow; persist once on release
      }
      const up = () => {
        setDragging(false)
        persist(ratioRef.current)
        window.removeEventListener('pointermove', move)
        window.removeEventListener('pointerup', up)
        window.removeEventListener('pointercancel', up)
        dragCleanupRef.current = null
      }
      dragCleanupRef.current = up
      window.addEventListener('pointermove', move)
      window.addEventListener('pointerup', up)
      window.addEventListener('pointercancel', up)
    },
    [axis, apply, persist],
  )

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLElement>) => {
      const step = e.shiftKey ? 0.1 : 0.03
      const dec = axis === 'x' ? 'ArrowLeft' : 'ArrowUp'
      const inc = axis === 'x' ? 'ArrowRight' : 'ArrowDown'
      if (e.key === dec) {
        e.preventDefault()
        apply(ratioRef.current - step)
      } else if (e.key === inc) {
        e.preventDefault()
        apply(ratioRef.current + step)
      } else if (e.key === 'Home') {
        e.preventDefault()
        apply(min)
      } else if (e.key === 'End') {
        e.preventDefault()
        apply(max)
      }
    },
    [axis, apply, min, max],
  )

  const onDoubleClick = useCallback(() => apply(initial), [apply, initial])

  // Body cursor + no-select while dragging, so the drag feels solid.
  useEffect(() => {
    if (!dragging) return
    const prevCursor = document.body.style.cursor
    const prevSelect = document.body.style.userSelect
    document.body.style.cursor = axis === 'x' ? 'col-resize' : 'row-resize'
    document.body.style.userSelect = 'none'
    return () => {
      document.body.style.cursor = prevCursor
      document.body.style.userSelect = prevSelect
    }
  }, [dragging, axis])

  return {
    ratio,
    dragging,
    containerRef,
    dividerProps: {
      role: 'separator',
      tabIndex: 0,
      'aria-orientation': axis === 'x' ? 'vertical' : 'horizontal',
      'aria-valuenow': Math.round(ratio * 100),
      'aria-valuemin': Math.round(min * 100),
      'aria-valuemax': Math.round(max * 100),
      'aria-label': label || 'Resize panes',
      onPointerDown,
      onKeyDown,
      onDoubleClick,
    },
  }
}
