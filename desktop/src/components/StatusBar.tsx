import { useState, useEffect } from 'react'
import { getPartnerStatus } from '../api/partner'
import { ThemeSwitcher } from './ThemeSwitcher'
import type { ThemeId, Theme } from '../themes'
import type { ModelInfo } from '../App'
import type { ToolProcessMode } from '../utils/toolLabels'

export type ThinkingLevel = 'off' | 'low' | 'medium' | 'high'
export type ApprovalMode = 'ask' | 'auto'

interface StatusBarProps {
  sessionId: string | null
  streaming: boolean
  model: string
  models?: ModelInfo[]
  onModelChange?: (id: string) => void
  thinkingLevel: ThinkingLevel
  onThinkingLevelChange?: (level: ThinkingLevel) => void
  approvalMode: ApprovalMode
  onApprovalModeChange?: (mode: ApprovalMode) => void
  toolProcessMode?: ToolProcessMode
  onToolProcessChange?: (mode: ToolProcessMode) => void
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

const THINKING_OPTIONS: { id: ThinkingLevel; label: string }[] = [
  { id: 'off', label: 'Off' },
  { id: 'low', label: 'Low' },
  { id: 'medium', label: 'Med' },
  { id: 'high', label: 'High' },
]

export function StatusBar({
  streaming,
  model,
  models = [],
  onModelChange,
  thinkingLevel,
  onThinkingLevelChange,
  approvalMode,
  onApprovalModeChange,
  toolProcessMode = 'off',
  onToolProcessChange,
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
  const [alerts, setAlerts] = useState('')

  useEffect(() => {
    let cancelled = false
    async function check() {
      try {
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
            /* */
          }
          try {
            const p = await getPartnerStatus()
            if (cancelled) return
            const bits: string[] = []
            if (p.pending_approvals > 0) bits.push(`${p.pending_approvals} approve`)
            if (p.open_goals > 0) bits.push(`${p.open_goals} goals`)
            setAlerts(bits.join(' · '))
          } catch {
            if (!cancelled) setAlerts('')
          }
        } else {
          setStatus('disconnected')
        }
      } catch {
        if (!cancelled) setStatus('disconnected')
      }
    }

    check()
    const interval = setInterval(check, 30000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  const dotColor =
    status === 'connected' ? 'var(--success)' : status === 'checking' ? 'var(--warning)' : 'var(--error)'
  const autoApprove = approvalMode === 'auto'

  return (
    <div
      className="flex items-center justify-between px-3 py-1 text-xs border-t gap-2"
      style={{
        background: 'var(--bg-secondary)',
        borderColor: 'var(--border)',
        color: 'var(--text-muted)',
      }}
    >
      {/* Left: status + mode + panels */}
      <div className="flex items-center gap-1.5 min-w-0 flex-wrap">
        <div
          className="flex items-center gap-1.5 px-1.5 py-0.5 rounded"
          title={status === 'connected' ? `Remedy ${version || ''}`.trim() : 'Server offline'}
          style={{ background: 'var(--bg-tertiary)' }}
        >
          <span className="inline-block w-1.5 h-1.5 rounded-full" style={{ background: dotColor }} />
          <span className="truncate max-w-[7rem]">
            {status === 'connected' ? (version ? `v${version}` : 'Online') : status === 'checking' ? '…' : 'Offline'}
          </span>
        </div>

        {streaming && (
          <span className="px-1.5 py-0.5 rounded font-medium" style={{ color: 'var(--accent)', background: 'color-mix(in srgb, var(--accent) 12%, transparent)' }}>
            Streaming
          </span>
        )}

        {alerts && (
          <span className="px-1.5 py-0.5 rounded truncate max-w-[10rem]" style={{ color: 'var(--warning)' }} title={alerts}>
            ⚠ {alerts}
          </span>
        )}

        <SegButton active={planMode} onClick={onTogglePlanMode} title="Plan mode (Ctrl+B)">
          {planMode ? 'Plan' : 'Build'}
        </SegButton>

        {/* Separate labeled buttons — not MemSkillsSet jammed together */}
        <SegButton
          active={panel === 'memory'}
          onClick={() => onTogglePanel('memory')}
          title="Memory panel"
        >
          Memory
        </SegButton>
        <SegButton
          active={panel === 'skills'}
          onClick={() => onTogglePanel('skills')}
          title="Skills (agent skill packs)"
        >
          Skills
        </SegButton>
        <SegButton
          active={panel === 'settings'}
          onClick={() => onTogglePanel('settings')}
          title="Settings — provider, project, theme, account"
        >
          Settings
        </SegButton>

        {updateAvailable && (
          <button
            onClick={() => (onInstallUpdate ? onInstallUpdate() : onCheckUpdates())}
            className="px-2 py-0.5 rounded text-xs font-medium"
            style={{ background: 'var(--accent)', color: '#fff' }}
          >
            Update
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

      {/* Right: model · think · approve · theme */}
      <div className="flex items-center gap-1.5 flex-shrink-0">
        {models.length > 0 && onModelChange ? (
          <select
            value={model}
            onChange={(e) => onModelChange(e.target.value)}
            className="text-xs rounded px-1.5 py-0.5 outline-none"
            title="Active model"
            style={{
              background: 'var(--bg-tertiary)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border)',
              maxWidth: 140,
            }}
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        ) : (
          <span className="truncate max-w-[8rem]" title={model}>
            {model}
          </span>
        )}

        <select
          value={thinkingLevel}
          onChange={(e) => onThinkingLevelChange?.(e.target.value as ThinkingLevel)}
          className="text-xs rounded px-1.5 py-0.5 outline-none"
          title="Thinking level"
          style={{
            background: 'var(--bg-tertiary)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border)',
          }}
        >
          {THINKING_OPTIONS.map((o) => (
            <option key={o.id} value={o.id}>
              Think {o.label}
            </option>
          ))}
        </select>

        <button
          type="button"
          onClick={() => onApprovalModeChange?.(autoApprove ? 'ask' : 'auto')}
          className="flex items-center justify-center rounded px-1.5 py-0.5 text-sm"
          title={
            autoApprove
              ? 'Auto-approve on — click for Ask'
              : 'Ask before risky tools — click for Auto'
          }
          aria-label={autoApprove ? 'Auto-approve' : 'Ask before risky actions'}
          style={{
            background: autoApprove
              ? 'color-mix(in srgb, var(--success) 25%, var(--bg-tertiary))'
              : 'var(--bg-tertiary)',
            color: autoApprove ? 'var(--success)' : 'var(--text-secondary)',
            border: `1px solid ${autoApprove ? 'var(--success)' : 'var(--border)'}`,
            minWidth: 28,
          }}
        >
          {autoApprove ? '👍' : '👎'}
        </button>

        {/* Tool process: cycle off → medium → full */}
        <button
          type="button"
          onClick={() => {
            const order: ToolProcessMode[] = ['off', 'medium', 'full']
            const i = order.indexOf(toolProcessMode)
            const next = order[(i + 1) % order.length]!
            onToolProcessChange?.(next)
          }}
          className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide"
          title={
            toolProcessMode === 'off'
              ? 'Tool process: Off (minimal) — click for Medium'
              : toolProcessMode === 'medium'
                ? 'Tool process: Medium — click for Full'
                : 'Tool process: Full — click for Off'
          }
          aria-label={`Tool process ${toolProcessMode}`}
          style={{
            background:
              toolProcessMode === 'off'
                ? 'var(--bg-tertiary)'
                : toolProcessMode === 'medium'
                  ? 'color-mix(in srgb, var(--accent) 20%, var(--bg-tertiary))'
                  : 'var(--accent)',
            color: toolProcessMode === 'full' ? '#fff' : 'var(--text-secondary)',
            border: `1px solid ${toolProcessMode === 'off' ? 'var(--border)' : 'var(--accent)'}`,
            minWidth: 36,
          }}
        >
          {toolProcessMode === 'off' ? 'Proc' : toolProcessMode === 'medium' ? 'Med' : 'Full'}
        </button>

        <ThemeSwitcher currentId={themeId} currentTheme={theme} onChange={onThemeChange} />
      </div>
    </div>
  )
}

function SegButton({
  children,
  active,
  onClick,
  title,
}: {
  children: React.ReactNode
  active: boolean
  onClick: () => void
  title?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className="px-2 py-0.5 rounded text-xs font-medium"
      style={{
        background: active ? 'var(--accent)' : 'var(--bg-tertiary)',
        color: active ? '#fff' : 'var(--text-secondary)',
      }}
    >
      {children}
    </button>
  )
}
