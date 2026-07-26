import { useCallback, useEffect, useRef, useState } from 'react'
import { isTauri, tauriInvoke } from '../api/tauri'

export type AppMenuAction =
  | 'settings'
  | 'memory'
  | 'skills'
  | 'help'
  | 'switch_web_ui'
  | 'check_updates'
  | 'install_update'
  | 'about'
  | 'new_session'
  | 'quit'

interface TitleBarProps {
  title?: string
  version?: string
  updateAvailable?: boolean
  onMenuAction?: (action: AppMenuAction) => void
}

/** Prefer official window API; fall back to custom Rust commands. */
async function withMainWindow<T>(
  fn: (win: {
    minimize: () => Promise<void>
    toggleMaximize: () => Promise<void>
    isMaximized: () => Promise<boolean>
    close: () => Promise<void>
    hide: () => Promise<void>
  }) => Promise<T>,
): Promise<T> {
  const { getCurrentWindow } = await import('@tauri-apps/api/window')
  return fn(getCurrentWindow())
}

/**
 * Custom themed window chrome. Wordmark logo opens the app menu.
 * Drag region is a dedicated middle strip only — never wraps chrome buttons
 * (WebView2 steals clicks if min/max/close sit inside app-region:drag).
 */
export function TitleBar({
  title = 'Remedy',
  version,
  updateAvailable,
  onMenuAction,
}: TitleBarProps) {
  const [maximized, setMaximized] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
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
        /* browser */
      }
    })()
    return () => {
      unlisten?.()
    }
  }, [inTauri])

  useEffect(() => {
    if (!menuOpen) return
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node
      if (menuRef.current?.contains(t) || btnRef.current?.contains(t)) return
      setMenuOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      window.removeEventListener('keydown', onKey)
    }
  }, [menuOpen])

  const onMinimize = useCallback(() => {
    if (!inTauri) return
    void (async () => {
      try {
        await withMainWindow((w) => w.minimize())
        return
      } catch (e) {
        console.warn('[remedy] window.minimize failed, trying command', e)
      }
      try {
        await tauriInvoke('minimize_main_window')
      } catch (e2) {
        console.warn('[remedy] minimize failed', e2)
      }
    })()
  }, [inTauri])

  const onToggleMax = useCallback(() => {
    if (!inTauri) return
    void (async () => {
      try {
        await withMainWindow(async (w) => {
          await w.toggleMaximize()
          setMaximized(await w.isMaximized())
        })
        return
      } catch (e) {
        console.warn('[remedy] window.toggleMaximize failed, trying command', e)
      }
      try {
        const m = await tauriInvoke<boolean>('toggle_maximize_main_window')
        setMaximized(Boolean(m))
      } catch (e2) {
        console.warn('[remedy] maximize failed', e2)
      }
    })()
  }, [inTauri])

  const onClose = useCallback(() => {
    if (!inTauri) return
    // Prefer Rust close-to-tray path; fall back to OS close (CloseRequested).
    void (async () => {
      try {
        await tauriInvoke('request_close_main_window')
        return
      } catch (e) {
        console.warn('[remedy] request_close_main_window failed, trying window API', e)
      }
      try {
        await withMainWindow((w) => w.close())
      } catch (e2) {
        console.warn('[remedy] close failed', e2)
        try {
          await withMainWindow((w) => w.hide())
        } catch {
          /* last resort exhausted */
        }
      }
    })()
  }, [inTauri])

  const run = (action: AppMenuAction) => {
    setMenuOpen(false)
    onMenuAction?.(action)
  }

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
      {/* Logo / menu — no-drag */}
      <div className="titlebar-no-drag relative flex-shrink-0 flex items-center px-1" style={{ zIndex: 60 }}>
        <button
          ref={btnRef}
          type="button"
          className="titlebar-btn flex items-center px-1.5 rounded"
          style={{
            width: 'auto',
            height: 32,
            background: menuOpen ? 'var(--bg-tertiary)' : 'transparent',
          }}
          title="Remedy menu"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label="Open Remedy menu"
          onClick={(e) => {
            e.stopPropagation()
            e.preventDefault()
            setMenuOpen((o) => !o)
          }}
        >
          <img
            src="/logo.png"
            alt="Remedy"
            draggable={false}
            style={{
              height: 24,
              width: 'auto',
              maxWidth: 150,
              objectFit: 'contain',
              objectPosition: 'left center',
              display: 'block',
            }}
          />
          <span
            className="ml-0.5 text-[9px]"
            style={{ color: 'var(--text-muted)' }}
            aria-hidden
          >
            ▾
          </span>
        </button>

        {menuOpen && (
          <div
            ref={menuRef}
            role="menu"
            className="titlebar-no-drag absolute top-full left-0 mt-1 z-[80] min-w-[200px] rounded-lg py-1 shadow-xl"
            style={{
              background: 'var(--bg-secondary)',
              border: '1px solid var(--border)',
              boxShadow: '0 8px 28px rgba(0,0,0,0.35)',
            }}
          >
            <MenuItem label="New session" onClick={() => run('new_session')} shortcut="Ctrl+N" />
            <MenuSep />
            <MenuItem label="Settings…" onClick={() => run('settings')} shortcut="Ctrl+," />
            <MenuItem label="Memory" onClick={() => run('memory')} />
            <MenuItem label="Skills" onClick={() => run('skills')} />
            <MenuItem label="Help / Owner's Manual…" onClick={() => run('help')} shortcut="F1" />
            {isTauri() && (
              <MenuItem
                label="Switch to WebUI…"
                onClick={() => run('switch_web_ui')}
              />
            )}
            <MenuSep />
            {updateAvailable ? (
              <MenuItem
                label="Install update…"
                onClick={() => run('install_update')}
                accent
              />
            ) : (
              <MenuItem label="Check for updates…" onClick={() => run('check_updates')} />
            )}
            <MenuItem
              label={version ? `About Remedy (v${version})` : 'About Remedy'}
              onClick={() => run('about')}
            />
            <MenuSep />
            <MenuItem label="Quit Remedy" onClick={() => run('quit')} danger />
          </div>
        )}
      </div>

      {/* Dedicated drag strip only — double-click maximizes */}
      <div
        className="flex-1 min-w-0 h-full"
        data-tauri-drag-region
        onDoubleClick={() => onToggleMax()}
      >
        <span className="sr-only">{title}</span>
      </div>

      {/* Window controls — must stay outside drag region */}
      <div className="titlebar-controls titlebar-no-drag flex flex-shrink-0" style={{ zIndex: 70 }}>
        <button
          type="button"
          className="titlebar-btn"
          title="Minimize"
          aria-label="Minimize"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation()
            e.preventDefault()
            onMinimize()
          }}
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
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation()
            e.preventDefault()
            onToggleMax()
          }}
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
          title="Close (hides to tray when Always ready is on)"
          aria-label="Close"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation()
            e.preventDefault()
            onClose()
          }}
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

function MenuSep() {
  return (
    <div
      className="my-1 mx-2 h-px"
      style={{ background: 'var(--border)' }}
      role="separator"
    />
  )
}

function MenuItem({
  label,
  onClick,
  shortcut,
  accent,
  danger,
}: {
  label: string
  onClick: () => void
  shortcut?: string
  accent?: boolean
  danger?: boolean
}) {
  return (
    <button
      type="button"
      role="menuitem"
      className="w-full flex items-center justify-between gap-4 px-3 py-1.5 text-left text-xs"
      style={{
        background: 'transparent',
        color: danger ? 'var(--error)' : accent ? 'var(--accent)' : 'var(--text-primary)',
        border: 'none',
        cursor: 'pointer',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = 'var(--bg-tertiary)'
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = 'transparent'
      }}
      onClick={onClick}
    >
      <span>{label}</span>
      {shortcut && (
        <span style={{ color: 'var(--text-muted)', fontSize: '0.65rem' }}>{shortcut}</span>
      )}
    </button>
  )
}
