import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import type { ThemeId, Theme } from '../themes'
import { THEME_LIST, themeSwatch, systemThemeSwatch } from '../themes'
import { browserStackHold } from '../utils/browserStack'

interface ThemeSwitcherProps {
  currentId: ThemeId
  currentTheme: Theme
  onChange: (id: ThemeId) => void
}

export function ThemeSwitcher({ currentId, onChange }: ThemeSwitcherProps) {
  const [open, setOpen] = useState(false)
  const [focusIdx, setFocusIdx] = useState(0)
  const listRef = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const idx = Math.max(0, THEME_LIST.findIndex((t) => t.id === currentId))
    setFocusIdx(idx)
    requestAnimationFrame(() => listRef.current?.focus())
  }, [open, currentId])

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node
      if (listRef.current?.contains(t) || btnRef.current?.contains(t)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  // Theme menu paints under the native Browser HWND unless we suppress it.
  useEffect(() => {
    if (!open) return
    return browserStackHold('theme-menu')
  }, [open])

  const onListKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      setOpen(false)
      btnRef.current?.focus()
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setFocusIdx((i) => (i + 1) % THEME_LIST.length)
      return
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      setFocusIdx((i) => (i - 1 + THEME_LIST.length) % THEME_LIST.length)
      return
    }
    if (e.key === 'Home') {
      e.preventDefault()
      setFocusIdx(0)
      return
    }
    if (e.key === 'End') {
      e.preventDefault()
      setFocusIdx(THEME_LIST.length - 1)
      return
    }
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      const t = THEME_LIST[focusIdx]
      if (t) {
        onChange(t.id)
        setOpen(false)
        btnRef.current?.focus()
      }
    }
  }

  return (
    <div className="relative">
      <button
        ref={btnRef}
        type="button"
        onClick={() => setOpen(!open)}
        className="seg-btn flex items-center gap-1.5"
        title="Change theme"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <ThemeColorDot themeId={currentId} />
        Theme
      </button>

      {open && (
        <div
          ref={listRef}
          role="listbox"
          tabIndex={0}
          aria-label="Themes"
          aria-activedescendant={`theme-opt-${THEME_LIST[focusIdx]?.id}`}
          onKeyDown={onListKey}
          className="absolute bottom-full mb-1.5 right-0 z-20 rounded-xl p-1.5 flex flex-col gap-0.5 min-w-[180px] max-h-[min(70vh,420px)] overflow-y-auto outline-none"
          style={{
            background: 'color-mix(in srgb, var(--bg-secondary) 96%, var(--bg-primary))',
            border: '1px solid color-mix(in srgb, var(--border) 85%, transparent)',
            boxShadow:
              '0 12px 32px rgba(0,0,0,0.35), 0 0 0 1px color-mix(in srgb, var(--accent) 6%, transparent)',
            backdropFilter: 'blur(12px)',
          }}
        >
          {THEME_LIST.map((t, i) => (
            <button
              key={t.id}
              type="button"
              id={`theme-opt-${t.id}`}
              role="option"
              aria-selected={t.id === currentId}
              onClick={() => {
                onChange(t.id)
                setOpen(false)
                btnRef.current?.focus()
              }}
              onMouseEnter={() => setFocusIdx(i)}
              className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-xs text-left transition-colors"
              style={{
                background:
                  i === focusIdx || t.id === currentId
                    ? 'color-mix(in srgb, var(--accent) 12%, var(--bg-tertiary))'
                    : 'transparent',
                color: 'var(--text-primary)',
                outline:
                  i === focusIdx
                    ? '1px solid color-mix(in srgb, var(--accent) 45%, transparent)'
                    : 'none',
              }}
            >
              <ThemeColorDot themeId={t.id} />
              <span className="flex-1">
                {t.name}
                {t.id === 'system' ? (
                  <span style={{ color: 'var(--text-muted)' }}> · OS</span>
                ) : null}
              </span>
              {t.id === currentId && <Checkmark />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * Dual-tone swatch from the real theme palette:
 * left = bg-primary, right = accent (system = dark | light split).
 */
export function ThemeColorDot({
  themeId,
  size = 12,
}: {
  themeId: ThemeId
  size?: number
}) {
  if (themeId === 'system') {
    const { dark, light } = systemThemeSwatch()
    return (
      <span
        className="inline-block rounded-full flex-shrink-0 overflow-hidden relative"
        style={{
          width: size,
          height: size,
          border: '1px solid rgba(128,128,128,0.45)',
          background: `linear-gradient(90deg, ${dark.bg} 0 50%, ${light.bg} 50% 100%)`,
          boxShadow: `inset 2px 0 0 0 ${dark.accent}, inset -2px 0 0 0 ${light.accent}`,
        }}
        title="System (dark / light)"
        aria-hidden
      />
    )
  }

  const s = themeSwatch(themeId)
  return (
    <span
      className="inline-block rounded-full flex-shrink-0"
      style={{
        width: size,
        height: size,
        border: `1px solid ${s.border}`,
        background: `linear-gradient(135deg, ${s.bg} 0 48%, ${s.accent} 52% 100%)`,
        boxShadow: `inset 0 0 0 1px ${s.surface}33`,
      }}
      title={themeId}
      aria-hidden
    />
  )
}

function Checkmark() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden>
      <path
        d="M2 6l3 3 5-5"
        stroke="var(--accent)"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
