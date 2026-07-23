import { useCallback, useEffect, useState } from 'react'
import { isTauri } from '../api/tauri'

/**
 * Custom themed window chrome (replaces the default white OS title bar).
 * Only interactive controls; drag region covers the rest.
 */
export function TitleBar({ title = 'Remedy Desktop' }: { title?: string }) {
  const [maximized, setMaximized] = useState(false)
  const inTauri = isTauri()

  useEffect(() => {
    if (!inTauri) return
    let unlisten: (() => void) | undefined
    ;(async () => {
      try {
        const { getCurrentWindow } = await import('@tauri-apps/api/window')
        const win = getCurrentWindow()
        setMaximized(await win.isMaximized())
        unlisten = await win.onResized(async () => {
          try {
            setMaximized(await win.isMaximized())
          } catch {
            /* ignore */
          }
        })
      } catch {
        /* browser / no window API */
      }
    })()
    return () => {
      unlisten?.()
    }
  }, [inTauri])

  const withWindow = useCallback(async (fn: (win: Awaited<ReturnType<typeof import('@tauri-apps/api/window').getCurrentWindow>>) => void | Promise<void>) => {
    if (!inTauri) return
    try {
      const { getCurrentWindow } = await import('@tauri-apps/api/window')
      await fn(getCurrentWindow())
    } catch (e) {
      console.warn('[remedy] titlebar action failed', e)
    }
  }, [inTauri])

  const onMinimize = () => void withWindow((w) => w.minimize())
  const onToggleMax = () => void withWindow(async (w) => {
    await w.toggleMaximize()
    setMaximized(await w.isMaximized())
  })
  const onClose = () => void withWindow((w) => w.close())

  // In browser dev mode, a slim themed bar still looks consistent.
  return (
    <div
      className="titlebar flex items-stretch flex-shrink-0 select-none"
      style={{
        height: 36,
        background: 'var(--bg-secondary)',
        borderBottom: '1px solid var(--border)',
        color: 'var(--text-primary)',
      }}
    >
      <div
        className="flex-1 flex items-center gap-2 min-w-0 px-3"
        data-tauri-drag-region
        onDoubleClick={(e) => {
          // Double-click title area → maximize (Windows convention).
          if ((e.target as HTMLElement).closest('button')) return
          onToggleMax()
        }}
      >
        <img
          src="/favicon.png"
          alt=""
          width={16}
          height={16}
          draggable={false}
          data-tauri-drag-region
          style={{ opacity: 0.95 }}
        />
        <span
          className="text-xs font-medium truncate"
          data-tauri-drag-region
          style={{ color: 'var(--text-secondary)' }}
        >
          {title}
        </span>
      </div>

      <div className="flex titlebar-controls">
        <button
          type="button"
          className="titlebar-btn"
          title="Minimize"
          aria-label="Minimize"
          onClick={onMinimize}
        >
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
            <path fill="currentColor" d="M2 6.5h8v1H2z" />
          </svg>
        </button>
        <button
          type="button"
          className="titlebar-btn"
          title={maximized ? 'Restore' : 'Maximize'}
          aria-label={maximized ? 'Restore' : 'Maximize'}
          onClick={onToggleMax}
        >
          {maximized ? (
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
              <path
                fill="none"
                stroke="currentColor"
                strokeWidth="1"
                d="M3.5 4.5h5v5h-5zM4.5 3.5h5v5"
              />
            </svg>
          ) : (
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
              <rect
                x="2.5"
                y="2.5"
                width="7"
                height="7"
                fill="none"
                stroke="currentColor"
                strokeWidth="1"
              />
            </svg>
          )}
        </button>
        <button
          type="button"
          className="titlebar-btn titlebar-btn-close"
          title="Close"
          aria-label="Close"
          onClick={onClose}
        >
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
            <path
              fill="currentColor"
              d="M3.2 2.5 2.5 3.2 5.3 6 2.5 8.8l.7.7L6 6.7l2.8 2.8.7-.7L6.7 6l2.8-2.8-.7-.7L6 5.3z"
            />
          </svg>
        </button>
      </div>
    </div>
  )
}
