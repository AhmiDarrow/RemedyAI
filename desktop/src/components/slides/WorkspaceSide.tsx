import { useEffect, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { isTauri, tauriListen } from '../../api/tauri'
import { ALL_SLIDES, SLIDE_META, type SlideId } from '../../workspace/types'
import {
  clampRailWidth,
  RAIL_WIDTH_MIN,
  type RailMode,
} from '../../workspace/layoutPrefs'

const RAIL_W = 36
const THIN_W = 10
/** Double-click resize handle resets to this default body width. */
const DEFAULT_BODY_W = 300

/**
 * Outer-edge workspace rail + optional body.
 *
 * Modes:
 * - thin  — narrow strip; click expands to icons
 * - icons — icon rail only; click icon opens panel
 * - open  — icons + panel body; header × minimizes to thin
 */
export function WorkspaceSide({
  side,
  active,
  width,
  railMode,
  onSelect,
  onWidth,
  onRailMode,
  onSwap,
  onPopout,
  onFullscreen,
  children,
}: {
  side: 'left' | 'right'
  active: SlideId
  width: number
  railMode: RailMode
  onSelect: (id: SlideId) => void
  onWidth: (w: number) => void
  onRailMode: (mode: RailMode) => void
  onSwap?: () => void
  onPopout?: () => void
  onFullscreen?: () => void
  children?: ReactNode
}) {
  const meta = SLIDE_META[active] ?? SLIDE_META.sessions
  const open = railMode === 'open'
  const thin = railMode === 'thin'
  /** Browser video/HTML fullscreen: hide panel header so host fills this rail tab. */
  const [browserPageFs, setBrowserPageFs] = useState(false)
  const [resizing, setResizing] = useState(false)

  useEffect(() => {
    if (!isTauri() || active !== 'browser') {
      setBrowserPageFs(false)
      return
    }
    let unlisten: (() => void) | undefined
    void tauriListen<{ fullscreen?: boolean }>('browser-page-fullscreen', (p) => {
      setBrowserPageFs(Boolean(p?.fullscreen))
    })
      .then((u) => {
        unlisten = u
      })
      .catch(() => {})
    return () => {
      unlisten?.()
      setBrowserPageFs(false)
    }
  }, [active])

  // Cursor + selection lock while dragging the resize handle
  useEffect(() => {
    if (!resizing) return
    const prev = document.body.style.cursor
    const prevUserSelect = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    document.body.classList.add('is-rail-resizing')
    return () => {
      document.body.style.cursor = prev
      document.body.style.userSelect = prevUserSelect
      document.body.classList.remove('is-rail-resizing')
    }
  }, [resizing])

  if (thin) {
    return (
      <button
        type="button"
        className="workspace-thin-rail flex h-full min-h-0 shrink-0 items-center justify-center"
        style={{
          width: THIN_W,
          background: 'var(--bg-tertiary)',
          borderRight: side === 'left' ? '1px solid var(--border)' : undefined,
          borderLeft: side === 'right' ? '1px solid var(--border)' : undefined,
          color: 'var(--text-muted)',
          cursor: 'pointer',
          padding: 0,
        }}
        title={side === 'left' ? 'Expand left rail' : 'Expand right rail'}
        aria-label={side === 'left' ? 'Expand left rail' : 'Expand right rail'}
        onClick={() => onRailMode('icons')}
      >
        <span
          style={{
            fontSize: 9,
            writingMode: 'vertical-rl',
            transform: side === 'right' ? 'rotate(180deg)' : undefined,
            letterSpacing: '0.04em',
            opacity: 0.85,
          }}
        >
          {side === 'left' ? '›' : '‹'}
        </span>
      </button>
    )
  }

  const rail = (
    <div
      className="slide-rail flex flex-col items-center shrink-0 h-full"
      data-side={side}
      role="toolbar"
      aria-label={side === 'left' ? 'Left workspace rail' : 'Right workspace rail'}
      style={{
        width: RAIL_W,
        borderRightWidth: side === 'left' ? 1 : 0,
        borderLeftWidth: side === 'right' ? 1 : 0,
        borderRightStyle: 'solid',
        borderLeftStyle: 'solid',
      }}
    >
      <button
        type="button"
        title={open ? 'Collapse panel (icons only)' : 'Open panel'}
        aria-label={open ? 'Collapse panel' : 'Open panel'}
        className="slide-rail-btn flex items-center justify-center mb-0.5"
        style={{
          background: 'transparent',
          color: 'var(--text-muted)',
          border: 'none',
          cursor: 'pointer',
          fontSize: 11,
        }}
        onClick={() => onRailMode(open ? 'icons' : 'open')}
      >
        {open ? (side === 'left' ? '‹' : '›') : side === 'left' ? '›' : '‹'}
      </button>
      {ALL_SLIDES.map((id, i) => {
        const m = SLIDE_META[id]
        const on = id === active
        // Soft divider between core (sessions/settings) and tools
        const afterCore = id === 'settings'
        return (
          <div key={id} className="flex flex-col items-center w-full">
            <button
              type="button"
              title={m.label}
              data-label={m.label}
              aria-label={m.label}
              aria-pressed={open && on}
              aria-current={open && on ? 'page' : undefined}
              className={`slide-rail-btn flex items-center justify-center${
                on ? (open ? ' is-active' : ' is-selected') : ''
              }`}
              style={{
                background: 'transparent',
                color: on && !open ? 'var(--accent)' : on ? undefined : 'var(--text-secondary)',
                border: 'none',
                cursor: 'pointer',
              }}
              onClick={() => {
                // Clicking the already-open active tab collapses to icons
                if (open && on) {
                  onRailMode('icons')
                  return
                }
                onSelect(id)
                if (!open) onRailMode('open')
              }}
            >
              {m.short}
            </button>
            {afterCore && i < ALL_SLIDES.length - 1 && (
              <div
                className="my-1 w-5 shrink-0"
                style={{
                  height: 1,
                  background: 'color-mix(in srgb, var(--border) 80%, transparent)',
                }}
                aria-hidden
              />
            )}
          </div>
        )
      })}
      <div className="flex-1 min-h-0" aria-hidden />
      {open && (
        <button
          type="button"
          title="Minimize to thin edge"
          aria-label="Minimize rail"
          className="slide-rail-btn flex items-center justify-center mt-auto"
          style={{
            background: 'transparent',
            color: 'var(--text-muted)',
            border: 'none',
            cursor: 'pointer',
            fontSize: 12,
          }}
          onClick={() => onRailMode('thin')}
        >
          −
        </button>
      )}
    </div>
  )

  if (!open) {
    return (
      <div className="flex h-full min-h-0 shrink-0" style={{ width: RAIL_W }}>
        {rail}
      </div>
    )
  }

  const startResize = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    const startX = e.clientX
    const startW = width
    setResizing(true)
    const move = (ev: globalThis.MouseEvent) => {
      const dx = side === 'left' ? ev.clientX - startX : startX - ev.clientX
      onWidth(clampRailWidth(startW + dx, startW))
    }
    const up = () => {
      setResizing(false)
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }

  const resizeHandle = (
    <div
      className={`slide-resize-handle${resizing ? ' is-active' : ''}`}
      title="Drag to resize · double-click to reset width"
      role="separator"
      aria-orientation="vertical"
      aria-valuenow={width}
      aria-valuemin={RAIL_WIDTH_MIN}
      onMouseDown={startResize}
      onDoubleClick={(e) => {
        e.preventDefault()
        onWidth(clampRailWidth(DEFAULT_BODY_W))
      }}
    />
  )

  const chromeBtn = (opts: {
    title: string
    label: string
    onClick: () => void
    danger?: boolean
  }) => (
    <button
      type="button"
      className="workspace-chrome-btn"
      title={opts.title}
      aria-label={opts.title}
      onClick={opts.onClick}
      style={opts.danger ? { color: 'var(--error)' } : undefined}
    >
      {opts.label}
    </button>
  )

  const body = (
    <div
      className="workspace-panel-body flex flex-col min-w-0 min-h-0 flex-1"
      style={{ background: 'var(--bg-secondary)' }}
    >
      {!(active === 'browser' && browserPageFs) && (
        <div
          data-workspace-panel-header
          className="slide-frame-chrome flex items-center gap-0.5 px-2 py-1 shrink-0 text-xs font-semibold"
          style={{ color: 'var(--text-primary)' }}
        >
          <span className="truncate flex-1" title={meta.label}>
            {meta.label}
          </span>
          {onSwap &&
            chromeBtn({
              title: 'Swap left and right panels',
              label: '⇄',
              onClick: onSwap,
            })}
          {meta.popout &&
            onPopout &&
            chromeBtn({
              title: 'Pop out to floating window',
              label: '↗',
              onClick: onPopout,
            })}
          {meta.popout &&
            onFullscreen &&
            chromeBtn({
              title: 'Fullscreen panel',
              label: '⛶',
              onClick: onFullscreen,
            })}
          {chromeBtn({
            title: 'Minimize to thin rail',
            label: '×',
            onClick: () => onRailMode('thin'),
          })}
        </div>
      )}
      <div className="flex-1 min-h-0 overflow-hidden">{children}</div>
    </div>
  )

  return (
    <div
      className="flex h-full min-h-0 shrink-0"
      style={{ width: width + RAIL_W }}
      data-workspace-side={side}
      data-rail-open={open ? '1' : '0'}
    >
      {side === 'left' ? (
        <>
          {rail}
          {body}
          {resizeHandle}
        </>
      ) : (
        <>
          {resizeHandle}
          {body}
          {rail}
        </>
      )}
    </div>
  )
}

export function PopoutOverlay({
  title,
  fullscreen,
  onClose,
  onToggleFullscreen,
  children,
}: {
  title: string
  fullscreen: boolean
  onClose: () => void
  onToggleFullscreen: () => void
  children: ReactNode
}) {
  // Esc: exit fullscreen first, then close popout.
  // Capture phase so it wins over xterm focus / helper textarea.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopPropagation()
      if (fullscreen) onToggleFullscreen()
      else onClose()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [fullscreen, onClose, onToggleFullscreen])

  // Portal to body so no parent transform/overflow can trap fixed positioning
  // or let xterm / layout paint over the exit chrome (Terminal, Browser, Scratch).
  const overlay = (
    <div
      className="fixed flex flex-col overflow-hidden shadow-2xl"
      data-popout-overlay
      data-fullscreen={fullscreen ? 'true' : 'false'}
      style={{
        // Above titlebar, panels, lightbox (z-100), help (z-200)
        zIndex: 500,
        background: 'var(--bg-primary)',
        border: fullscreen ? 'none' : '1px solid var(--border)',
        borderRadius: fullscreen ? 0 : 12,
        boxShadow: fullscreen
          ? 'none'
          : '0 24px 64px rgba(0,0,0,0.45), 0 0 0 1px color-mix(in srgb, var(--border) 70%, transparent)',
        ...(fullscreen
          ? { top: 0, left: 0, right: 0, bottom: 0 }
          : {
              top: '8%',
              left: '12%',
              width: '76%',
              height: '80%',
            }),
      }}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className="flex items-center gap-2 px-3 shrink-0 select-none"
        data-popout-chrome
        style={{
          height: 44,
          minHeight: 44,
          maxHeight: 44,
          borderBottom: '1px solid var(--border)',
          color: 'var(--text-primary)',
          background: 'var(--bg-secondary)',
          position: 'relative',
          zIndex: 20,
          boxShadow: '0 2px 8px color-mix(in srgb, #000 35%, transparent)',
          flexShrink: 0,
          pointerEvents: 'auto',
        }}
      >
        <span className="flex-1 font-semibold text-sm truncate">{title}</span>
        <span className="text-[11px] tabular-nums" style={{ color: 'var(--text-muted)' }}>
          Esc · {fullscreen ? 'exit fullscreen' : 'close'}
        </span>
        <button
          type="button"
          className="px-3 py-1.5 rounded text-xs font-semibold"
          style={{
            background: 'var(--bg-tertiary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
            cursor: 'pointer',
          }}
          onClick={(e) => {
            e.stopPropagation()
            onToggleFullscreen()
          }}
          title={fullscreen ? 'Exit fullscreen (Esc)' : 'Fullscreen'}
        >
          {fullscreen ? '↘ Exit fullscreen' : '⛶ Fullscreen'}
        </button>
        <button
          type="button"
          className="px-3 py-1.5 rounded text-xs font-semibold"
          style={{
            background: 'var(--error)',
            color: '#fff',
            border: 'none',
            cursor: 'pointer',
          }}
          onClick={(e) => {
            e.stopPropagation()
            onClose()
          }}
          title="Close panel (Esc when not fullscreen)"
        >
          ✕ Close
        </button>
      </div>
      <div
        className="relative overflow-hidden"
        data-popout-body
        style={{
          flex: '1 1 0%',
          minHeight: 0,
          zIndex: 1,
          isolation: 'isolate',
          background: 'var(--bg-primary)',
        }}
      >
        {children}
      </div>
    </div>
  )

  if (typeof document === 'undefined') return overlay
  return createPortal(overlay, document.body)
}
