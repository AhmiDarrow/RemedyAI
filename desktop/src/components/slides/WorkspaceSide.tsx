import { useEffect, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { ALL_SLIDES, SLIDE_META, type SlideId } from '../../workspace/types'
import type { RailMode } from '../../workspace/layoutPrefs'

const RAIL_W = 36
const THIN_W = 10

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

  if (thin) {
    return (
      <button
        type="button"
        className="flex h-full min-h-0 shrink-0 items-center justify-center"
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
        <span style={{ fontSize: 9, writingMode: 'vertical-rl', transform: side === 'right' ? 'rotate(180deg)' : undefined }}>
          {side === 'left' ? '›' : '‹'}
        </span>
      </button>
    )
  }

  const rail = (
    <div
      className="flex flex-col items-center gap-0.5 py-1 shrink-0 h-full"
      style={{
        width: RAIL_W,
        background: 'var(--bg-tertiary)',
        borderRight: side === 'left' ? '1px solid var(--border)' : undefined,
        borderLeft: side === 'right' ? '1px solid var(--border)' : undefined,
      }}
    >
      <button
        type="button"
        title={open ? 'Minimize rail' : 'Open panel'}
        className="w-7 h-6 rounded text-[10px] flex items-center justify-center mb-0.5"
        style={{
          color: 'var(--text-muted)',
          border: '1px solid transparent',
        }}
        onClick={() => onRailMode(open ? 'thin' : 'open')}
      >
        {open ? (side === 'left' ? '‹' : '›') : (side === 'left' ? '›' : '‹')}
      </button>
      {ALL_SLIDES.map((id) => {
        const m = SLIDE_META[id]
        const on = open && id === active
        return (
          <button
            key={id}
            type="button"
            title={m.label}
            className="w-8 h-8 rounded text-sm flex items-center justify-center"
            style={{
              background: on ? 'var(--accent)' : 'transparent',
              color: on ? '#fff' : 'var(--text-secondary)',
            }}
            onClick={() => {
              onSelect(id)
              if (!open) onRailMode('open')
            }}
          >
            {m.short}
          </button>
        )
      })}
    </div>
  )

  if (!open) {
    return (
      <div className="flex h-full min-h-0 shrink-0" style={{ width: RAIL_W }}>
        {rail}
      </div>
    )
  }

  const resizeHandle = (
    <div
      className="w-1 shrink-0 cursor-col-resize self-stretch"
      style={{ background: 'var(--border)' }}
      onMouseDown={(e) => {
        e.preventDefault()
        const startX = e.clientX
        const startW = width
        const move = (ev: MouseEvent) => {
          const dx = side === 'left' ? ev.clientX - startX : startX - ev.clientX
          onWidth(Math.min(480, Math.max(200, startW + dx)))
        }
        const up = () => {
          window.removeEventListener('mousemove', move)
          window.removeEventListener('mouseup', up)
        }
        window.addEventListener('mousemove', move)
        window.addEventListener('mouseup', up)
      }}
    />
  )

  const body = (
    <div
      className="flex flex-col min-w-0 min-h-0 flex-1"
      style={{ background: 'var(--bg-secondary)' }}
    >
      <div
        className="flex items-center gap-1 px-2 py-1 border-b shrink-0 text-xs font-semibold"
        style={{ borderColor: 'var(--border)', color: 'var(--text-primary)' }}
      >
        <span className="truncate flex-1">{meta.label}</span>
        {onSwap && (
          <button
            type="button"
            className="px-1.5 py-0.5 rounded opacity-80 hover:opacity-100"
            style={{
              background: 'var(--bg-primary)',
              border: '1px solid var(--border)',
              color: 'var(--text-secondary)',
              fontSize: 10,
            }}
            title="Swap left and right panels"
            onClick={onSwap}
          >
            ⇄
          </button>
        )}
        {meta.popout && onPopout && (
          <button type="button" className="px-1 opacity-70" title="Pop out" onClick={onPopout}>
            ↗
          </button>
        )}
        {meta.popout && onFullscreen && (
          <button
            type="button"
            className="px-1 opacity-70"
            title="Fullscreen"
            onClick={onFullscreen}
          >
            ⛶
          </button>
        )}
        <button
          type="button"
          className="px-1 opacity-70"
          title="Minimize to thin rail"
          onClick={() => onRailMode('thin')}
        >
          ×
        </button>
      </div>
      <div className="flex-1 min-h-0 overflow-hidden">{children}</div>
    </div>
  )

  return (
    <div className="flex h-full min-h-0 shrink-0" style={{ width: width + RAIL_W }}>
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
        background: '#0d1117',
        border: fullscreen ? 'none' : '1px solid var(--border)',
        borderRadius: fullscreen ? 0 : 12,
        // Fullscreen always covers the whole webview — chrome stays in-flow above content
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
      {/*
        Always-visible exit chrome for ALL popout slides (Terminal / Browser / Scratch).
        - position relative + high z-index keeps it above DOM children
        - Browser native WebView2 is only positioned over the host *below* this bar
        - pointer-events ensured so buttons stay clickable
      */}
      <div
        className="flex items-center gap-2 px-3 shrink-0 select-none"
        data-popout-chrome
        style={{
          height: 44,
          minHeight: 44,
          maxHeight: 44,
          borderBottom: '1px solid #30363d',
          color: '#e6edf3',
          background: '#161b22',
          position: 'relative',
          zIndex: 20,
          boxShadow: '0 2px 8px rgba(0,0,0,0.45)',
          flexShrink: 0,
          pointerEvents: 'auto',
        }}
      >
        <span className="flex-1 font-semibold text-sm truncate">{title}</span>
        <span className="text-[11px] tabular-nums" style={{ color: '#8b949e' }}>
          Esc · {fullscreen ? 'exit fullscreen' : 'close'}
        </span>
        <button
          type="button"
          className="px-3 py-1.5 rounded text-xs font-semibold"
          style={{
            background: '#21262d',
            border: '1px solid #30363d',
            color: '#e6edf3',
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
            background: '#da3633',
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
      {/* Content clipped below chrome — terminal/browser/scratch cannot paint over the bar */}
      <div
        className="relative overflow-hidden"
        data-popout-body
        style={{
          flex: '1 1 0%',
          minHeight: 0,
          zIndex: 1,
          isolation: 'isolate',
        }}
      >
        {children}
      </div>
    </div>
  )

  if (typeof document === 'undefined') return overlay
  return createPortal(overlay, document.body)
}
