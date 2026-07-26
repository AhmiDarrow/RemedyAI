import { useEffect, useRef, useState } from 'react'
import { isTauri } from '../api/tauri'

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

/**
 * In-app menu bar. Window min/max/close use **native OS decorations**
 * (tauri.conf decorations: true) because WebView2 custom chrome was unreliable.
 */
export function TitleBar({
  title = 'Remedy',
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
        paddingLeft: 6,
        paddingRight: 8,
      }}
    >
      <div className="relative flex-shrink-0 flex items-center">
        <button
          ref={btnRef}
          type="button"
          className="flex items-center px-1.5 rounded"
          style={{
            height: 30,
            background: menuOpen ? 'var(--bg-tertiary)' : 'transparent',
            border: 'none',
            cursor: 'pointer',
          }}
          title="Remedy menu"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label="Open Remedy menu"
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
              height: 22,
              width: 'auto',
              maxWidth: 140,
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

      <div className="flex-1 min-w-0 px-3 truncate text-[11px]" style={{ color: 'var(--text-muted)' }}>
        {title}
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
