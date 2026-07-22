import { useState } from 'react'
import type { ThemeId, Theme } from '../themes'
import { THEME_LIST } from '../themes'

interface ThemeSwitcherProps {
  currentId: ThemeId
  currentTheme: Theme
  onChange: (id: ThemeId) => void
}

export function ThemeSwitcher({ currentId, onChange }: ThemeSwitcherProps) {
  const [open, setOpen] = useState(false)

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 px-2 py-0.5 rounded text-xs transition-colors"
        style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)' }}
        title="Change theme"
      >
        <ColorDot color={THEME_LIST.find((t) => t.id === currentId)?.colors['--accent'] ?? '#888'} />
        Theme
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div
            className="absolute bottom-full mb-1 right-0 z-20 rounded-lg p-1.5 flex flex-col gap-0.5 min-w-[140px]"
            style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)', boxShadow: '0 4px 24px rgba(0,0,0,0.4)' }}
          >
            {THEME_LIST.map((t) => (
              <button
                key={t.id}
                onClick={() => {
                  onChange(t.id)
                  setOpen(false)
                }}
                className="flex items-center gap-2 px-3 py-1.5 rounded text-xs text-left transition-colors"
                style={{
                  background: t.id === currentId ? 'var(--bg-tertiary)' : 'transparent',
                  color: 'var(--text-primary)',
                }}
              >
                <ColorDot color={t.colors['--accent']} />
                <span className="flex-1">{t.name}</span>
                {t.id === currentId && <Checkmark />}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function ColorDot({ color }: { color: string }) {
  return (
    <span
      className="inline-block w-3 h-3 rounded-full border"
      style={{ background: color, borderColor: 'var(--border)' }}
    />
  )
}

function Checkmark() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
      <path d="M2 6l3 3 5-5" stroke="var(--accent)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
