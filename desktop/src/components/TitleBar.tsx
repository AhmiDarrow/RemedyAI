import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from 'react'
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
  version?: string
  updateAvailable?: boolean
  onMenuAction?: (action: AppMenuAction) => void
}

/**
 * Single in-app chrome bar: brand wordmark menu + drag region + window controls.
 * Requires `decorations: false` so OS chrome is not stacked above this bar.
 *
 * Windows + WebView2: do NOT use `data-tauri-drag-region` for the whole bar.
 * CSS drag regions poison hit-testing after move/maximize/restore. Drag is
 * started only via explicit `startDragging()` on the middle strip mousedown.
 * Logo + window controls are always `app-region: no-drag` with higher z-index.
 */
export function TitleBar({
  version,
  updateAvailable,
  onMenuAction,
}: TitleBarProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [maximized, setMaximized] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)

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

  const refreshMaximized = useCallback(() => {
    if (!isTauri()) return
    tauriInvoke<boolean>('is_main_window_maximized')
      .then((v) => setMaximized(Boolean(v)))
      .catch(() => {})
  }, [])

  /** Force WebView hit-test refresh after geometry changes (Windows drag-region bug). */
  const reflowChrome = useCallback(() => {
    const el = rootRef.current
    if (!el) return
    // Toggle a harmless property so layout/hit-test rebuilds without flicker.
    const prev = el.style.transform
    el.style.transform = 'translateZ(0)'
    void el.offsetHeight
    el.style.transform = prev
  }, [])

  const syncChrome = useCallback(() => {
    refreshMaximized()
    // Double rAF: wait until native resize/move settles.
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        reflowChrome()
        refreshMaximized()
      })
    })
  }, [refreshMaximized, reflowChrome])

  // Event-driven maximize state + hit-test resync (not a sticky CSS drag region).
  useEffect(() => {
    if (!isTauri()) return
    let cancelled = false
    const unsubs: Array<() => void> = []

    syncChrome()

    ;(async () => {
      try {
        const { getCurrentWindow } = await import('@tauri-apps/api/window')
        if (cancelled) return
        const win = getCurrentWindow()
        const onGeo = () => {
          if (!cancelled) syncChrome()
        }
        unsubs.push(await win.onResized(onGeo))
        unsubs.push(await win.onMoved(onGeo))
        unsubs.push(await win.onScaleChanged(onGeo))
        unsubs.push(
          await win.onFocusChanged(() => {
            if (!cancelled) syncChrome()
          }),
        )
      } catch {
        // Fall back to light polling if window events unavailable.
        const id = window.setInterval(() => {
          if (!cancelled) refreshMaximized()
        }, 2000)
        unsubs.push(() => window.clearInterval(id))
      }
    })()

    // Rare fallback poll (icon can desync if events missed).
    const pollId = window.setInterval(() => {
      if (!cancelled) refreshMaximized()
    }, 4000)

    return () => {
      cancelled = true
      window.clearInterval(pollId)
      for (const u of unsubs) {
        try {
          u()
        } catch {
          /* ignore */
        }
      }
    }
  }, [syncChrome, refreshMaximized])

  const run = (action: AppMenuAction) => {
    setMenuOpen(false)
    onMenuAction?.(action)
  }

  /** Explicit window drag — avoids CSS app-region sticky hit-test after move. */
  const onDragMouseDown = (e: ReactMouseEvent) => {
    if (!isTauri()) return
    if (e.button !== 0) return
    if ((e.target as HTMLElement).closest('button')) return
    e.preventDefault()
    void (async () => {
      try {
        const { getCurrentWindow } = await import('@tauri-apps/api/window')
        await getCurrentWindow().startDragging()
      } catch {
        // Fallback: custom command path if API import fails mid-session.
        try {
          await tauriInvoke('start_dragging_main_window')
        } catch {
          /* ignore */
        }
      }
      // After drag ends, OS may fire move; sync anyway.
      syncChrome()
    })()
  }

  const onDragDoubleClick = (e: ReactMouseEvent) => {
    if ((e.target as HTMLElement).closest('button')) return
    if (!isTauri()) return
    e.preventDefault()
    void tauriInvoke<boolean>('toggle_maximize_main_window')
      .then((v) => {
        setMaximized(Boolean(v))
        syncChrome()
      })
      .catch(() => {})
  }

  const winBtn = (label: string, onClick: () => void, hoverBg?: string) => (
    <button
      type="button"
      aria-label={label}
      title={label}
      className="titlebar-winbtn flex items-center justify-center flex-shrink-0"
      style={{
        width: 46,
        height: 36,
        background: 'transparent',
        border: 'none',
        color: 'var(--text-secondary)',
        cursor: 'pointer',
        fontSize: 12,
        lineHeight: 1,
      }}
      onMouseDown={(e) => {
        // Never let the drag strip steal the click.
        e.stopPropagation()
      }}
      onClick={(e) => {
        e.stopPropagation()
        e.preventDefault()
        onClick()
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = hoverBg || 'var(--bg-tertiary)'
        if (hoverBg) e.currentTarget.style.color = '#fff'
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = 'transparent'
        e.currentTarget.style.color = 'var(--text-secondary)'
      }}
    >
      {label === 'Minimize' && (
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
          <path d="M1 5h8" stroke="currentColor" strokeWidth="1.2" />
        </svg>
      )}
      {label === 'Maximize' && !maximized && (
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
          <rect x="1.5" y="1.5" width="7" height="7" fill="none" stroke="currentColor" strokeWidth="1.2" />
        </svg>
      )}
      {label === 'Maximize' && maximized && (
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
          <path
            d="M3 3h5v5H3V3zm-1.5 1.5V9.5H7"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.1"
          />
        </svg>
      )}
      {label === 'Close' && (
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
          <path d="M2 2l6 6M8 2L2 8" stroke="currentColor" strokeWidth="1.2" />
        </svg>
      )}
    </button>
  )

  return (
    <div
      ref={rootRef}
      className="titlebar flex items-center flex-shrink-0 select-none"
      style={{
        height: 36,
        background: 'var(--bg-secondary)',
        borderBottom: '1px solid var(--border)',
        color: 'var(--text-primary)',
        paddingLeft: 0,
        paddingRight: 0,
      }}
    >
      {/* Logo / app menu — never a drag region */}
      <div className="titlebar-logo relative flex-shrink-0 flex items-stretch h-full">
        <button
          ref={btnRef}
          type="button"
          className="flex items-center h-full pl-1 pr-1"
          style={{
            background: menuOpen ? 'var(--bg-tertiary)' : 'transparent',
            border: 'none',
            cursor: 'pointer',
            paddingTop: 0,
            paddingBottom: 0,
          }}
          title="Remedy menu"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label="Open Remedy menu"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation()
            setMenuOpen((o) => !o)
          }}
        >
          <img
            src="/logo.png"
            alt="Remedy"
            draggable={false}
            style={{
              height: '100%',
              width: 'auto',
              maxHeight: 36,
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
            className="absolute top-full left-0 mt-1 z-[80] min-w-[200px] rounded-lg py-1 shadow-xl"
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
              <MenuItem label="Switch to WebUI…" onClick={() => run('switch_web_ui')} />
            )}
            <MenuSep />
            {updateAvailable ? (
              <MenuItem label="Install update…" onClick={() => run('install_update')} accent />
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

      {/* Empty drag strip: explicit startDragging only (no data-tauri-drag-region). */}
      <div
        className="titlebar-drag flex-1 min-w-0 h-full"
        onMouseDown={onDragMouseDown}
        onDoubleClick={onDragDoubleClick}
      />

      {isTauri() && (
        <div className="titlebar-controls flex items-stretch flex-shrink-0 h-full">
          {winBtn('Minimize', () => {
            void tauriInvoke('minimize_main_window')
              .then(() => syncChrome())
              .catch(() => {})
          })}
          {winBtn('Maximize', () => {
            void tauriInvoke<boolean>('toggle_maximize_main_window')
              .then((v) => {
                setMaximized(Boolean(v))
                syncChrome()
              })
              .catch(() => {})
          })}
          {winBtn(
            'Close',
            () => {
              void tauriInvoke('request_close_main_window').catch(() => {})
            },
            'var(--error, #e81123)',
          )}
        </div>
      )}
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
