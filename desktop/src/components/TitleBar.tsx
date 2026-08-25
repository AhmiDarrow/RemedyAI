import { useEffect, useRef, useState } from 'react'
import { isTauri, tauriInvoke } from '../api/tauri'
import { useI18n } from '../i18n'
import { browserStackHold } from '../utils/browserStack'
import { TitleBarDownload } from './TitleBarDownload'

/** Linux (and any undecorated) Tauri build: in-app window controls + drag. */
function useCustomWindowChrome(): boolean {
  if (typeof navigator === 'undefined') return false
  if (!isTauri()) return false
  const ua = navigator.userAgent || ''
  return /Linux|X11|Wayland/i.test(ua) && !/Android/i.test(ua)
}

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
  const [maximized, setMaximized] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const customChrome = useCustomWindowChrome()
  const { t } = useI18n()

  useEffect(() => {
    const root = document.documentElement
    if (customChrome) root.setAttribute('data-custom-chrome', '1')
    else root.removeAttribute('data-custom-chrome')
    return () => root.removeAttribute('data-custom-chrome')
  }, [customChrome])

  useEffect(() => {
    if (!customChrome) return
    let live = true
    const tick = () => {
      void tauriInvoke<boolean>('is_main_window_maximized')
        .then((v) => {
          if (live) setMaximized(Boolean(v))
        })
        .catch(() => {})
    }
    tick()
    const id = window.setInterval(tick, 700)
    return () => {
      live = false
      window.clearInterval(id)
    }
  }, [customChrome])

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
      className={`titlebar flex items-stretch flex-shrink-0 select-none${
        customChrome ? ' has-custom-chrome' : ''
      }`}
      style={{
        height: 36,
        background: 'var(--bg-secondary)',
        borderBottom: '1px solid var(--border)',
        color: 'var(--text-primary)',
        paddingLeft: 0,
        paddingRight: customChrome ? 0 : 8,
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
          title={t('menu.open')}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          aria-label={t('menu.open')}
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
            <MenuItem label={t('menu.newSession')} onClick={() => run('new_session')} shortcut="Ctrl+N" />
            <MenuSep />
            <MenuItem label={t('menu.settings')} onClick={() => run('settings')} shortcut="Ctrl+," />
            <MenuItem label={t('menu.memory')} onClick={() => run('memory')} />
            <MenuItem label={t('menu.skills')} onClick={() => run('skills')} />
            <MenuItem label={t('menu.diagnostics')} onClick={() => run('diagnostics')} />
            <MenuItem label={t('menu.help')} onClick={() => run('help')} shortcut="F1" />
            {isTauri() && (
              <MenuItem label={t('menu.openBrowser')} onClick={() => run('switch_web_ui')} />
            )}
            <MenuSep />
            {updateAvailable ? (
              <MenuItem label={t('menu.installUpdate')} onClick={() => run('install_update')} accent />
            ) : (
              <MenuItem label={t('menu.checkUpdates')} onClick={() => run('check_updates')} />
            )}
            <MenuItem
              label={version ? t('menu.aboutVersion', { version }) : t('menu.about')}
              onClick={() => run('about')}
            />
            <MenuSep />
            <MenuItem label={t('menu.quit')} onClick={() => run('quit')} danger />
          </div>
        )}
      </div>

      {/* Spacer — OS decorations on Windows; Linux uses this as the drag strip.
          Downloads render here (pointer-events: none) so the drag still works. */}
      <div
        className="flex-1 min-w-0 h-full relative"
        onMouseDown={(e) => {
          if (!customChrome || e.button !== 0) return
          void tauriInvoke('start_dragging_main_window')
        }}
        onDoubleClick={() => {
          if (!customChrome) return
          void tauriInvoke<boolean>('toggle_maximize_main_window')
            .then((v) => setMaximized(Boolean(v)))
            .catch(() => {})
        }}
      >
        <TitleBarDownload />
      </div>

      {version && (
        <span
          className="titlebar-version text-[10px] tabular-nums flex-shrink-0 self-center pr-2"
          style={{ color: 'var(--text-muted)' }}
          title={`Remedy v${version}`}
        >
          v{version}
        </span>
      )}

      {customChrome && (
        <div className="titlebar-win-btns flex items-stretch h-full flex-shrink-0">
          <button
            type="button"
            className="titlebar-win-btn"
            title={t('win.minimize')}
            aria-label="Minimize"
            onClick={() => void tauriInvoke('minimize_main_window')}
          >
            <WinIcon kind="min" />
          </button>
          <button
            type="button"
            className="titlebar-win-btn"
            title={maximized ? t('win.restore') : t('win.maximize')}
            aria-label={maximized ? 'Restore' : 'Maximize'}
            onClick={() => {
              void tauriInvoke<boolean>('toggle_maximize_main_window')
                .then((v) => setMaximized(Boolean(v)))
                .catch(() => {})
            }}
          >
            <WinIcon kind={maximized ? 'restore' : 'max'} />
          </button>
          <button
            type="button"
            className="titlebar-win-btn is-close"
            title={t('win.close')}
            aria-label={t('win.close')}
            onClick={() => void tauriInvoke('request_close_main_window')}
          >
            <WinIcon kind="close" />
          </button>
        </div>
      )}
    </div>
  )
}

function WinIcon({ kind }: { kind: 'min' | 'max' | 'restore' | 'close' }) {
  if (kind === 'min') {
    return (
      <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
        <path d="M1 5h8" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
      </svg>
    )
  }
  if (kind === 'restore') {
    return (
      <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
        <rect x="3.2" y="1.4" width="5.4" height="5.4" rx="0.4" fill="none" stroke="currentColor" strokeWidth="1.2" />
        <rect x="1.4" y="3.2" width="5.4" height="5.4" rx="0.4" fill="none" stroke="currentColor" strokeWidth="1.2" />
      </svg>
    )
  }
  if (kind === 'max') {
    return (
      <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
        <rect x="1.4" y="1.4" width="7.2" height="7.2" rx="0.6" fill="none" stroke="currentColor" strokeWidth="1.2" />
      </svg>
    )
  }
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
      <path d="M2 2l6 6M8 2L2 8" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  )
}

export function MenuSep() {
  return (
    <div
      className="my-1 mx-2 h-px"
      style={{ background: 'var(--border)' }}
      role="separator"
    />
  )
}

export function MenuItem({
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
      className={`titlebar-menu-item w-full flex items-center justify-between gap-4 mx-1 px-2.5 py-1.5 text-left text-xs rounded-md${
        accent ? ' is-accent' : ''
      }${danger ? ' is-danger' : ''}`}
      style={{
        width: 'calc(100% - 0.5rem)',
        border: 'none',
        cursor: 'pointer',
        fontWeight: accent || danger ? 600 : 500,
      }}
      onClick={onClick}
    >
      <span>{label}</span>
      {shortcut && (
        <span className="titlebar-menu-shortcut">
          {shortcut}
        </span>
      )}
    </button>
  )
}
