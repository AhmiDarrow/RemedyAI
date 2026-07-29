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
      className="flex flex-col items-center gap-0.5 py-1 border-r shrink-0"
      style={{
        width: 36,
        background: 'var(--bg-tertiary)',
        borderColor: 'var(--border)',
        borderRightWidth: side === 'left' ? 1 : 0,
        borderLeftWidth: side === 'right' ? 1 : 0,
        borderLeftStyle: 'solid',
        borderRightStyle: 'solid',
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
            className="w-8 h-8 rounded text-sm flex items-center justify-center"
            style={{
              background: on ? 'var(--accent)' : 'transparent',
              color: on ? '#fff' : 'var(--text-secondary)',
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
          className="flex items-center gap-1 px-2 py-1 border-b shrink-0 text-xs font-semibold"
          style={{ borderColor: 'var(--border)', color: 'var(--text-primary)' }}
        >
          <span className="truncate flex-1">{meta.label}</span>
          {meta.popout && onPopout && (
            <button
              type="button"
              className="px-1 opacity-70 hover:opacity-100"
              title="Pop out"
              onClick={onPopout}
            >
              ↗
            </button>
          )}
          {meta.popout && onFullscreen && (
            <button
              type="button"
              className="px-1 opacity-70 hover:opacity-100"
              title="Fullscreen"
              onClick={onFullscreen}
            >
              ⛶
            </button>
          )}
          {onClose && (
            <button
              type="button"
              className="px-1 opacity-70 hover:opacity-100"
              title="Hide panel"
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
          className="w-1 cursor-col-resize shrink-0 hover:bg-[var(--accent)]"
          style={{ background: 'transparent' }}
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
