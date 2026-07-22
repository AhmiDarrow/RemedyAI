import { useState, useEffect } from 'react'
import { ThemeSwitcher } from './ThemeSwitcher'
import type { ThemeId, Theme } from '../themes'

interface StatusBarProps {
  sessionId: string | null
  streaming: boolean
  model: string
  themeId: ThemeId
  theme: Theme
  onThemeChange: (id: ThemeId) => void
  planMode: boolean
  onTogglePlanMode: () => void
  panel?: 'memory' | 'skills' | null
  onTogglePanel: (panel: 'memory' | 'skills') => void
}

export function StatusBar({
  sessionId,
  streaming,
  model,
  themeId,
  theme,
  onThemeChange,
  planMode,
  onTogglePlanMode,
  panel,
  onTogglePanel,
}: StatusBarProps) {
  const [version, setVersion] = useState('')
  const [status, setStatus] = useState<'connected' | 'disconnected' | 'checking'>('checking')

  useEffect(() => {
    let cancelled = false
    async function check() {
      try {
        const res = await fetch('/api/status')
        if (cancelled) return
        if (res.ok) {
          const data = await res.json()
          setStatus('connected')
          setVersion(data.version || '')
        } else {
          setStatus('disconnected')
        }
      } catch {
        if (!cancelled) setStatus('disconnected')
      }
    }

    check()
    const interval = setInterval(check, 15000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  const dotColor =
    status === 'connected' ? 'var(--success)' : status === 'checking' ? 'var(--warning)' : 'var(--error)'

  return (
    <div
      className="flex items-center justify-between px-4 py-1.5 text-xs border-t"
      style={{
        background: 'var(--bg-secondary)',
        borderColor: 'var(--border)',
        color: 'var(--text-muted)',
      }}
    >
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1.5">
          <span className="inline-block w-2 h-2 rounded-full" style={{ background: dotColor }} />
          <span>
            {status === 'connected' ? 'Connected' : status === 'checking' ? 'Checking...' : 'Disconnected'}
            {version && ` \u00B7 v${version}`}
          </span>
        </div>

        <button
          onClick={onTogglePlanMode}
          className="px-2 py-0.5 rounded text-xs font-medium transition-colors"
          title="Toggle plan mode (no tool execution)"
          style={{
            background: planMode ? 'var(--accent)' : 'var(--bg-tertiary)',
            color: planMode ? '#fff' : 'var(--text-secondary)',
          }}
        >
          {planMode ? 'Plan' : 'Build'}
        </button>

        <button
          onClick={() => onTogglePanel('memory')}
          className="px-2 py-0.5 rounded text-xs transition-colors"
          title="Memory panel"
          style={{
            background: panel === 'memory' ? 'var(--accent)' : 'var(--bg-tertiary)',
            color: panel === 'memory' ? '#fff' : 'var(--text-secondary)',
          }}
        >
          Memory
        </button>

        <button
          onClick={() => onTogglePanel('skills')}
          className="px-2 py-0.5 rounded text-xs transition-colors"
          title="Skills panel"
          style={{
            background: panel === 'skills' ? 'var(--accent)' : 'var(--bg-tertiary)',
            color: panel === 'skills' ? '#fff' : 'var(--text-secondary)',
          }}
        >
          Skills
        </button>

        {sessionId && <span style={{ color: 'var(--text-muted)' }}>{sessionId.slice(0, 8)}</span>}
        {streaming && <span style={{ color: 'var(--accent)' }}>Streaming...</span>}
      </div>

      <div className="flex items-center gap-3">
        <span>Model: {model}</span>
        <ThemeSwitcher currentId={themeId} currentTheme={theme} onChange={onThemeChange} />
      </div>
    </div>
  )
}
