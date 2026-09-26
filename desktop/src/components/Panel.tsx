import { useEffect, useRef, useState, type ReactNode } from 'react'

interface PanelProps {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
  /**
   * Fixed chrome under the title (tabs, etc.) — not inside the scroll body,
   * so it cannot scroll/clip above the visible viewport.
   */
  toolbar?: ReactNode
}

/** Non-modal side panel: focus can move between the panel and chat. */
export function Panel({ open, onClose, title, children, toolbar }: PanelProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const prevFocus = useRef<HTMLElement | null>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    if (!open) return
    prevFocus.current = document.activeElement as HTMLElement | null
    closeRef.current?.focus()

    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.isComposing) return
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) return
      if (e.key === 'Escape') {
        e.preventDefault()
        onCloseRef.current()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      prevFocus.current?.focus?.()
    }
  }, [open])

  const [statusBarHeight, setStatusBarHeight] = useState(44)
  useEffect(() => {
    const bar = document.querySelector('[data-remedy-status-bar]')
    if (!open || !bar) return
    const measure = () => setStatusBarHeight(bar.getBoundingClientRect().height)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(bar)
    return () => observer.disconnect()
  }, [open])

  // Critical: unmount when closed. A width:0 panel still contributes content height
  // in a column flex parent and was collapsing the chat feed to 0px.
  if (!open) return null

  // Sit below the in-app title bar (36px). top:0 hid the Skills title + Library
  // tabs under the window chrome so users only saw the filter list.
  const TITLEBAR_H = 36
  // Leave room for the bottom status bar so close/tabs aren't covered either.


  return (
    <div
      ref={rootRef}
      role="complementary"
      aria-label={title}
      data-keep-focus
      className="flex flex-col border-l overflow-hidden fixed right-0 z-[80]"
      style={{
        background: 'color-mix(in srgb, var(--bg-secondary) 96%, var(--bg-primary))',
        borderColor: 'color-mix(in srgb, var(--border) 85%, transparent)',
        top: TITLEBAR_H,
        bottom: statusBarHeight,
        width: 'min(380px, 100vw)',
        boxShadow: '-8px 0 24px rgba(0,0,0,0.22)',
      }}
    >
      <div
        className="flex items-center justify-between px-3 py-2.5 border-b text-xs font-semibold tracking-tight flex-shrink-0"
        style={{
          borderColor: 'color-mix(in srgb, var(--border) 80%, transparent)',
          color: 'var(--text-primary)',
        }}
      >
        <span>{title}</span>
        <button
          ref={closeRef}
          type="button"
          onClick={onClose}
          className="ui-btn ui-btn-ghost text-base leading-none"
          style={{ padding: '0.15rem 0.4rem' }}
          aria-label={`Close ${title}`}
        >
          {'\u00D7'}
        </button>
      </div>
      {toolbar != null && (
        <div
          className="flex-shrink-0 px-2 py-2 border-b"
          style={{ borderColor: 'var(--border)', background: 'var(--bg-tertiary)' }}
        >
          {toolbar}
        </div>
      )}
      <div className="flex-1 min-h-0 overflow-y-auto p-2 text-xs">
        {children}
      </div>
    </div>
  )
}
