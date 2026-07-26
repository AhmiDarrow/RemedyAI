import type { ReactNode } from 'react'
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
  return (
    <div
      className="fixed z-[90] flex flex-col rounded-lg shadow-2xl overflow-hidden"
      style={{
        background: 'var(--bg-secondary)',
        border: '1px solid var(--border)',
        ...(fullscreen
          ? { inset: 8 }
          : {
              top: '10%',
              left: '15%',
              width: '70%',
              height: '75%',
            }),
      }}
    >
      <div
        className="flex items-center gap-2 px-3 py-1.5 border-b text-xs font-semibold shrink-0"
        style={{ borderColor: 'var(--border)', color: 'var(--text-primary)' }}
      >
        <span className="flex-1">{title}</span>
        <button type="button" onClick={onToggleFullscreen} title="Toggle fullscreen">
          ⛶
        </button>
        <button type="button" onClick={onClose} title="Re-embed">
          ×
        </button>
      </div>
      <div className="flex-1 min-h-0">{children}</div>
    </div>
  )
}
