import { useEffect, useRef, useState } from 'react'
import { isTauri } from '../api/tauri'
import { browserStackHold } from '../utils/browserStack'

export type AppMenuAction =
  | 'settings'
  | 'memory'
  | 'skills'
  | 'help'
  | 'diagnostics'
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
 * In-app menu strip (logo → app menu). Window min/max/close are **OS decorations**
 * so WebView2 never owns those hit-tests (avoids the recurring sticky-drag bug).
 *
 * Requires `decorations: true` in tauri.conf.json.
 */
export function TitleBar({
  version,
  updateAvailable,
  onMenuAction,
}: TitleBarProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)

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

  // App menu must not sit under the native Browser embed HWND.
  useEffect(() => {
    if (!menuOpen) return
    return browserStackHold('titlebar-menu')
  }, [menuOpen])

  const run = (action: AppMenuAction) => {
    setMenuOpen(false)
    onMenuAction?.(action)
  }

  return (
    <div
      className="titlebar flex items-center flex-shrink-0 select-none"
      style={{
        height: 36,
        background: 'var(--bg-secondary)',
        borderBottom: '1px solid var(--border)',
        color: 'var(--text-primary)',
        paddingLeft: 0,
        paddingRight: 8,
      }}
    >
      {/* Logo / app menu */}
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
          onClick={() => setMenuOpen((o) => !o)}
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
            className="absolute top-full left-0 mt-1 z-[80] min-w-[210px] rounded-xl py-1.5"
            style={{
              background: 'color-mix(in srgb, var(--bg-secondary) 96%, var(--bg-primary))',
              border: '1px solid color-mix(in srgb, var(--border) 85%, transparent)',
              boxShadow:
                '0 12px 36px rgba(0,0,0,0.32), 0 0 0 1px color-mix(in srgb, var(--accent) 6%, transparent)',
              backdropFilter: 'blur(12px)',
            }}
          >
            <MenuItem label="New session" onClick={() => run('new_session')} shortcut="Ctrl+N" />
            <MenuSep />
            <MenuItem label="Settings…" onClick={() => run('settings')} shortcut="Ctrl+," />
            <MenuItem label="Memory" onClick={() => run('memory')} />
            <MenuItem label="Skills" onClick={() => run('skills')} />
            <MenuItem label="Health Diagnostics…" onClick={() => run('diagnostics')} />
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

      {/* Spacer — window min/max/close live in the OS title bar (decorations: true). */}
      <div className="flex-1 min-w-0 h-full" aria-hidden />

      {version && (
        <span
          className="text-[10px] tabular-nums flex-shrink-0 pr-1"
          style={{ color: 'var(--text-muted)' }}
          title={`Remedy v${version}`}
        >
          v{version}
        </span>
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
      className="w-full flex items-center justify-between gap-4 mx-1 px-2.5 py-1.5 text-left text-xs rounded-md"
      style={{
        width: 'calc(100% - 0.5rem)',
        background: 'transparent',
        color: danger ? 'var(--error)' : accent ? 'var(--accent)' : 'var(--text-primary)',
        border: 'none',
        cursor: 'pointer',
        fontWeight: accent || danger ? 600 : 500,
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = 'color-mix(in srgb, var(--bg-tertiary) 90%, transparent)'
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = 'transparent'
      }}
      onClick={onClick}
    >
      <span>{label}</span>
      {shortcut && (
        <span
          style={{
            color: 'var(--text-muted)',
            fontSize: '0.62rem',
            fontFamily: 'ui-monospace, Cascadia Code, monospace',
          }}
        >
          {shortcut}
        </span>
      )}
    </button>
  )
}
