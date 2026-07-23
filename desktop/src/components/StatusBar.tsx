import { useState, useEffect } from 'react'
import { ThemeSwitcher } from './ThemeSwitcher'
import type { ThemeId, Theme } from '../themes'
import type { ModelInfo } from '../App'

interface StatusBarProps {
  sessionId: string | null
  streaming: boolean
  model: string
  models?: ModelInfo[]
  onModelChange?: (id: string) => void
  themeId: ThemeId
  theme: Theme
  onThemeChange: (id: ThemeId) => void
  planMode: boolean
  onTogglePlanMode: () => void
  panel?: 'memory' | 'skills' | 'settings' | null
  onTogglePanel: (panel: 'memory' | 'skills' | 'settings') => void
  updateAvailable: boolean
  onCheckUpdates: () => void
  onInstallUpdate?: () => void
}

export function StatusBar({
  sessionId,
  streaming,
  model,
  models = [],
  onModelChange,
  themeId,
  theme,
  onThemeChange,
  planMode,
  onTogglePlanMode,
  panel,
  onTogglePanel,
  updateAvailable,
  onCheckUpdates,
  onInstallUpdate,
}: StatusBarProps) {
  const [version, setVersion] = useState('')
  const [status, setStatus] = useState<'connected' | 'disconnected' | 'checking'>('checking')

  useEffect(() => {
    let cancelled = false
    async function check() {
      // Don't flash "checking" on routine polls — only first paint.
      try {
        // One request for health + version (was healthCheck + status = double spam).
        const res = await fetch('http://127.0.0.1:7400/api/status', {
          signal: AbortSignal.timeout(3000),
        })
        if (cancelled) return
        if (res.ok) {
          setStatus('connected')
          try {
            const data = await res.json()
            if (data?.version) setVersion(String(data.version))
          } catch {
            // body optional
          }
        } else {
          setStatus('disconnected')
        }
      } catch {
        if (!cancelled) setStatus('disconnected')
      }
    }

    check()
    // 30s is enough for a local sidecar; cuts access-log / CPU noise in half.
    const interval = setInterval(check, 30000)
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
          title={`Toggle plan mode (ctrl+b)`}
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

        <button
          onClick={() => onTogglePanel('settings')}
          className="px-2 py-0.5 rounded text-xs transition-colors"
          title="Settings panel"
          style={{
            background: panel === 'settings' ? 'var(--accent)' : 'var(--bg-tertiary)',
            color: panel === 'settings' ? '#fff' : 'var(--text-secondary)',
          }}
        >
          Settings
        </button>

        {sessionId && <span style={{ color: 'var(--text-muted)' }}>{sessionId.slice(0, 8)}</span>}
        {streaming && <span style={{ color: 'var(--accent)' }}>Streaming...</span>}

        {updateAvailable && (
          <button
            onClick={() => (onInstallUpdate ? onInstallUpdate() : onCheckUpdates())}
            className="px-2 py-0.5 rounded text-xs font-medium animate-pulse"
            title="Update available — download, install, and relaunch"
            style={{
              background: 'var(--accent)',
              color: '#fff',
            }}
          >
            Update Now
          </button>
        )}

        {status === 'disconnected' && (
          <button
            onClick={() => window.location.reload()}
            className="px-2 py-0.5 rounded text-xs"
            style={{ background: 'var(--error)', color: '#fff' }}
          >
            Reconnect
          </button>
        )}
      </div>

      <div className="flex items-center gap-3">
        {models.length > 0 && onModelChange ? (
          <select
            value={model}
            onChange={(e) => onModelChange(e.target.value)}
            className="text-xs rounded px-1.5 py-0.5 outline-none"
            style={{
              background: 'var(--bg-tertiary)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border)',
            }}
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        ) : (
          <span>Model: {model}</span>
        )}
        <ThemeSwitcher currentId={themeId} currentTheme={theme} onChange={onThemeChange} />
      </div>
    </div>
  )
}
