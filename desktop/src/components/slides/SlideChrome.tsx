import type { ReactNode } from 'react'
import { ALL_SLIDES, SLIDE_META, type SlideId } from '../../workspace/types'
import { clampRailWidth } from '../../workspace/layoutPrefs'

export function SlideRail({
  active,
  onSelect,
  side,
}: {
  active: SlideId
  onSelect: (id: SlideId) => void
  side: 'left' | 'right'
}) {
  return (
    <div
      className="slide-rail flex flex-col items-center border-r shrink-0"
      data-side={side}
      role="toolbar"
      aria-label={side === 'left' ? 'Left workspace rail' : 'Right workspace rail'}
      style={{
        width: 36,
        borderRightWidth: side === 'left' ? 1 : 0,
        borderLeftWidth: side === 'right' ? 1 : 0,
        borderLeftStyle: 'solid',
        borderRightStyle: 'solid',
        borderColor: 'var(--border)',
      }}
    >
      {ALL_SLIDES.map((id) => {
        const m = SLIDE_META[id]
        const on = id === active
        return (
          <button
            key={id}
            type="button"
            title={m.label}
            aria-pressed={on}
            aria-current={on ? 'page' : undefined}
            className={`slide-rail-btn flex items-center justify-center${on ? ' is-active' : ''}`}
            data-label={m.label}
            aria-label={m.label}
            style={{
              background: 'transparent',
              color: on ? undefined : 'var(--text-secondary)',
              border: 'none',
              cursor: 'pointer',
            }}
            onClick={() => onSelect(id)}
          >
            {m.short}
          </button>
        )
      })}
    </div>
  )
}

export function SlideFrame({
  id,
  side,
  width,
  onWidth,
  onClose,
  onPopout,
  onFullscreen,
  children,
}: {
  id: SlideId
  side: 'left' | 'right'
  width: number
  onWidth?: (w: number) => void
  onClose?: () => void
  onPopout?: () => void
  onFullscreen?: () => void
  children: ReactNode
}) {
  const meta = SLIDE_META[id]
  return (
    <div
      className="flex h-full min-h-0 shrink-0"
      style={{ width, borderColor: 'var(--border)' }}
    >
      {side === 'left' && (
        <SlideRail active={id} onSelect={() => {}} side="left" />
      )}
      {/* Note: rail selection is handled by parent via SlideHost */}
      <div
        className="flex flex-col min-w-0 min-h-0 flex-1 border-r"
        style={{
          background: 'var(--bg-secondary)',
          borderColor: 'var(--border)',
        }}
      >
        <div
          className="slide-frame-chrome flex items-center gap-1 px-2 py-1.5 shrink-0 text-xs font-semibold border-b"
          data-workspace-panel-header
          style={{
            color: 'var(--text-primary)',
            borderColor: 'var(--border)',
            background: 'var(--bg-secondary)',
          }}
        >
          <span className="truncate flex-1" title={meta.label}>
            {meta.label}
          </span>
          {meta.popout && onPopout && (
            <button
              type="button"
              className="workspace-chrome-btn"
              title="Pop out to floating window"
              aria-label={`Pop out ${meta.label}`}
              onClick={onPopout}
            >
              ↗
            </button>
          )}
          {meta.popout && onFullscreen && (
            <button
              type="button"
              className="workspace-chrome-btn"
              title="Fullscreen panel"
              aria-label={`Fullscreen ${meta.label}`}
              onClick={onFullscreen}
            >
              ⛶
            </button>
          )}
          {onClose && (
            <button
              type="button"
              className="workspace-chrome-btn"
              title="Hide panel"
              aria-label={`Hide ${meta.label}`}
              onClick={onClose}
            >
              ×
            </button>
          )}
        </div>
        <div className="flex-1 min-h-0 overflow-hidden">{children}</div>
      </div>
      {side === 'right' && (
        <SlideRail active={id} onSelect={() => {}} side="right" />
      )}
      {onWidth && (
        <div
          className="slide-resize-handle"
          title="Drag to resize panel"
          onMouseDown={(e) => {
            e.preventDefault()
            const startX = e.clientX
            const startW = width
            const move = (ev: MouseEvent) => {
              const dx = side === 'left' ? ev.clientX - startX : startX - ev.clientX
              onWidth(clampRailWidth(startW + dx, startW))
            }
            const up = () => {
              window.removeEventListener('mousemove', move)
              window.removeEventListener('mouseup', up)
            }
            window.addEventListener('mousemove', move)
            window.addEventListener('mouseup', up)
          }}
        />
      )}
    </div>
  )
}
