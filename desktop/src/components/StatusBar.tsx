import { useState, useEffect, useMemo } from 'react'
import { getLatestCheckpoint, getPartnerStatus } from '../api/partner'
import { getVisionStatus, type VisionStatus } from '../api/vision'
import type { ConnectedProvider } from '../api/providers'
import { ThemeSwitcher } from './ThemeSwitcher'
import type { ThemeId, Theme } from '../themes'
import type { ModelInfo } from '../App'
import {
  isFullProcessMode,
  TOOL_PROCESS_CYCLE,
  type ToolProcessMode,
} from '../utils/toolLabels'
import type { UiMode } from '../utils/uiMode'

export type ThinkingLevel = 'off' | 'low' | 'medium' | 'high'
export type ApprovalMode = 'ask' | 'auto'

interface StatusBarProps {
  sessionId: string | null
  streaming: boolean
  model: string
  models?: ModelInfo[]
  onModelChange?: (id: string) => void
  /** Active LLM provider id */
  provider?: string
  /** Connected+enabled providers for the main-screen picker */
  connectedProviders?: ConnectedProvider[]
  onProviderModelChange?: (provider: string, model: string) => void
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
  /** Open offline Help wiki (owner's manual). */
  onOpenHelp?: () => void
  updateAvailable: boolean
  onCheckUpdates: () => void
  onInstallUpdate?: () => void
  /** Toggle interactive Time Travel timeline panel. */
  timeTravelOpen?: boolean
  onToggleTimeTravel?: () => void
  /** Open multiprovider Usage & Continuity dashboard */
  onOpenUsage?: () => void
  /** Main chrome density: Simple hides power-user controls */
  uiMode?: UiMode
  onUiModeChange?: (mode: UiMode) => void
}

const THINKING_OPTIONS: { id: ThinkingLevel; label: string }[] = [
  { id: 'off', label: 'Off' },
  { id: 'low', label: 'Low' },
  { id: 'medium', label: 'Med' },
  { id: 'high', label: 'High' },
]

const INSTALL_PHASES = new Set([
  'downloading',
  'download',
  'extracting',
  'extract',
  'installing',
  'install',
  'starting',
  'testing',
  'verifying',
  'unpacking',
  'preparing',
])

function visionIsBusy(vs: VisionStatus | null): boolean {
  if (!vs) return false
  const phase = (vs.progress?.phase || '').toLowerCase()
  if (INSTALL_PHASES.has(phase)) return true
  if (phase === 'error' || phase === 'cancelled') return true
  // Active download with partial bytes
  const done = vs.progress?.bytes_done || 0
  const total = vs.progress?.bytes_total || 0
  if (total > 0 && done < total && phase !== 'ready' && phase !== 'idle') return true
  return false
}

function visionPct(vs: VisionStatus | null): number | null {
  if (!vs?.progress) return null
  const done = vs.progress.bytes_done || 0
  const total = vs.progress.bytes_total || 0
  if (total > 0) return Math.min(100, Math.round((done / total) * 100))
  return null
}

function visionLine(vs: VisionStatus | null): string {
  if (!vs) return ''
  const phase = (vs.progress?.phase || '').toLowerCase()
  const msg = (vs.progress?.message || '').trim()
  const pct = visionPct(vs)

  if (phase === 'error') {
    return msg || vs.progress?.error || 'Local model install failed'
  }
  if (phase === 'cancelled') {
    return msg || 'Local model install cancelled'
  }
  if (INSTALL_PHASES.has(phase) || visionIsBusy(vs)) {
    const label = msg || `Local model ${phase || 'installing'}…`
    return pct != null ? `${label} ${pct}%` : label
  }
  if (vs.enabled && vs.running) return 'Vision ready'
  if (vs.enabled && vs.installed && !vs.running) {
    return 'Vision idle'
  }
  if (vs.enabled && !vs.installed) return 'Vision setup pending'
  if (phase === 'ready' && vs.ready) return 'Vision ready'
  return ''
}

export function StatusBar({
  sessionId,
  streaming,
  model,
  models = [],
  onModelChange,
  provider = '',
  connectedProviders = [],
  onProviderModelChange,
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
  onOpenHelp,
  updateAvailable,
  onCheckUpdates,
  onInstallUpdate,
  onOpenUsage,
  timeTravelOpen = false,
  onToggleTimeTravel,
  uiMode = 'simple',
  onUiModeChange,
}: StatusBarProps) {
  const advanced = uiMode === 'advanced'
  const [version, setVersion] = useState('')
  const [status, setStatus] = useState<'connected' | 'disconnected' | 'checking'>('checking')
  const [alerts, setAlerts] = useState('')
  const [providerHealthTip, setProviderHealthTip] = useState<string | null>(null)
  const [accessScope, setAccessScope] = useState<string>('')
  const [vision, setVision] = useState<VisionStatus | null>(null)
  const [hasCheckpoint, setHasCheckpoint] = useState(false)

  useEffect(() => {
    let cancelled = false
    let failStreak = 0
    async function check() {
      try {
        // Prefer /api/ping (sub-ms). Fall back to /api/status for version + older builds.
        let ok = false
        let ver = ''
        try {
          const ping = await fetch('http://127.0.0.1:7400/api/ping', {
            signal: AbortSignal.timeout(2500),
            headers: { Accept: 'application/json' },
          })
          if (ping.ok) {
            ok = true
            try {
              const data = (await ping.json()) as { version?: string }
              if (data?.version) ver = String(data.version)
            } catch {
              /* */
            }
          }
        } catch {
          /* try status */
        }
        if (!ok) {
          const res = await fetch('http://127.0.0.1:7400/api/status', {
            signal: AbortSignal.timeout(4000),
          })
          ok = res.ok
          if (res.ok) {
            try {
              const data = await res.json()
              if (data?.version) ver = String(data.version)
            } catch {
              /* */
            }
          }
        }
        if (cancelled) return
        if (ok) {
          failStreak = 0
          setStatus('connected')
          if (ver) setVersion(ver)
          try {
            const p = await getPartnerStatus()
            if (cancelled) return
            const bits: string[] = []
            if (p.pending_approvals > 0) bits.push(`${p.pending_approvals} approve`)
            if (p.open_goals > 0) bits.push(`${p.open_goals} goals`)
            setAlerts(bits.join(' · '))
            setAccessScope(String(p.access_scope || ''))
            const ph = p.provider_health
            if (ph?.flaky || ph?.suggest_switch) {
              setProviderHealthTip(
                ph.reason
                  || (ph.suggested_provider
                    ? `Provider flaky — try ${ph.suggested_provider}`
                    : 'Provider flaky — switch model or retry'),
              )
            } else {
              setProviderHealthTip(null)
            }
          } catch {
            if (!cancelled) {
              setAlerts('')
              setProviderHealthTip(null)
              setAccessScope('')
            }
          }
        } else {
          // Hysteresis: one blip must not flip the dock to "Server offline".
          failStreak += 1
          if (failStreak >= 2) setStatus('disconnected')
        }
      } catch {
        if (!cancelled) {
          failStreak += 1
          if (failStreak >= 2) setStatus('disconnected')
        }
      }
    }

    check()
    const interval = setInterval(check, 15000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  // Latest mid-task checkpoint (opens Memory panel → Checkpoint tab via Memory button)
  useEffect(() => {
    let cancelled = false
    async function tick() {
      if (!sessionId) {
        if (!cancelled) setHasCheckpoint(false)
        return
      }
      try {
        const d = await getLatestCheckpoint(sessionId)
        if (!cancelled) setHasCheckpoint(Boolean(d.checkpoint))
      } catch {
        if (!cancelled) setHasCheckpoint(false)
      }
    }
    void tick()
    // No session → no polling. Idle sessions: slow poll; streaming: faster.
    if (!sessionId) {
      return () => {
        cancelled = true
      }
    }
    const interval = setInterval(() => void tick(), streaming ? 4000 : 20000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [sessionId, streaming])

  // Poll visual decoder so setup opt-in progress is visible in the dock (faster while busy).
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let idleMisses = 0
    async function tick() {
      let nextMs = 12_000
      try {
        const vs = await getVisionStatus()
        if (!cancelled) setVision(vs)
        if (visionIsBusy(vs)) {
          idleMisses = 0
          nextMs = 1500
        } else if (!vs?.enabled && !vs?.installed) {
          // Not opted in — barely poll.
          idleMisses = 0
          nextMs = 45_000
        } else if (!vs?.running) {
          idleMisses += 1
          nextMs = idleMisses >= 3 ? 30_000 : 12_000
        } else {
          idleMisses = 0
          nextMs = 15_000
        }
      } catch {
        if (!cancelled) setVision(null)
        idleMisses += 1
        nextMs = idleMisses >= 2 ? 45_000 : 15_000
      }
      if (!cancelled) {
        timer = setTimeout(() => void tick(), nextMs)
      }
    }
    void tick()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [])

  const dotColor =
    status === 'connected' ? 'var(--success)' : status === 'checking' ? 'var(--warning)' : 'var(--error)'
  const autoApprove = approvalMode === 'auto'

  const dockVision = useMemo(() => {
    const line = visionLine(vision)
    const pct = visionPct(vision)
    const phase = (vision?.progress?.phase || '').toLowerCase()
    const busy = visionIsBusy(vision)
    const show =
      busy
      || phase === 'error'
      || phase === 'cancelled'
      || (vision?.enabled && !vision.ready)
      || (vision?.enabled && vision.installed) // show idle or running
      || (vision?.enabled && vision.running)
      || (phase === 'ready' && vision?.ready)
    return { line, pct, phase, busy, show }
  }, [vision])

  const openWebUi = () => {
    void (async () => {
      try {
        const { isTauri, tauriInvoke } = await import('../api/tauri')
        if (isTauri()) {
          await tauriInvoke('switch_to_web_ui')
          return
        }
      } catch (e) {
        console.warn('switch_to_web_ui:', e)
      }
      window.open('http://127.0.0.1:7400/', '_blank', 'noopener,noreferrer')
    })()
  }

  return (
    <div
      className="flex flex-col border-t"
      style={{
        background: 'var(--bg-secondary)',
        borderColor: 'var(--border)',
        color: 'var(--text-muted)',
      }}
    >
      {/* Status dock — vision progress, server, alerts */}
      <div
        className="flex items-center gap-3 px-3 py-1.5 text-xs border-b"
        style={{
          borderColor: 'var(--border)',
          background: 'var(--bg-tertiary)',
          minHeight: 32,
        }}
      >
        <div
          className="flex items-center gap-1.5 flex-shrink-0"
          title={status === 'connected' ? `Remedy ${version || ''}`.trim() : 'Server offline'}
        >
          <span className="inline-block w-2 h-2 rounded-full" style={{ background: dotColor }} />
          <span className="font-medium" style={{ color: 'var(--text-secondary)' }}>
            {status === 'connected'
              ? version
                ? `Server v${version}`
                : 'Server online'
              : status === 'checking'
                ? 'Server…'
                : 'Server offline'}
          </span>
        </div>

        {streaming && (
          <span
            className="px-1.5 py-0.5 rounded font-medium flex-shrink-0"
            style={{
              color: 'var(--accent)',
              background: 'color-mix(in srgb, var(--accent) 12%, transparent)',
            }}
          >
            Streaming
          </span>
        )}

        {onToggleTimeTravel && (
          <button
            type="button"
            onClick={onToggleTimeTravel}
            className="px-1.5 py-0.5 rounded font-medium flex-shrink-0"
            style={{
              color: timeTravelOpen ? 'var(--accent)' : 'var(--text-secondary)',
              background: timeTravelOpen
                ? 'color-mix(in srgb, var(--accent) 14%, transparent)'
                : 'transparent',
              border: '1px solid var(--border)',
            }}
            title="Time Travel — restore chat & files to an earlier step"
          >
            ⏱ Time travel
          </button>
        )}

        {(accessScope === 'full' || accessScope === 'untrusted') && (
          <button
            type="button"
            className="px-1.5 py-0.5 rounded flex-shrink-0 text-[10px] font-medium"
            style={{
              color: accessScope === 'full' ? '#92400e' : 'var(--warning)',
              background:
                accessScope === 'full'
                  ? 'color-mix(in srgb, #f59e0b 22%, transparent)'
                  : 'transparent',
              border: '1px solid var(--border)',
            }}
            title={
              accessScope === 'full'
                ? 'No project folder — tools can reach your user home / full scope. Pick a project for a safer jail.'
                : 'Untrusted scope — project-only tools + always ask.'
            }
            onClick={() => onTogglePanel('settings')}
          >
            {accessScope === 'full' ? 'Full access' : 'Untrusted'}
          </button>
        )}

        {alerts && (
          <span
            className="px-1.5 py-0.5 rounded truncate max-w-[10rem] flex-shrink-0"
            style={{ color: 'var(--warning)' }}
            title={alerts}
          >
            ⚠ {alerts}
          </span>
        )}

        {providerHealthTip && (
          <button
            type="button"
            className="px-1.5 py-0.5 rounded truncate max-w-[12rem] flex-shrink-0 text-left text-[10px]"
            style={{
              color: 'var(--warning)',
              border: '1px solid var(--border)',
              background: 'transparent',
            }}
            title={providerHealthTip}
            onClick={() => onOpenUsage?.()}
          >
            Provider: flaky
          </button>
        )}

        {dockVision.show && dockVision.line ? (
          <div className="flex items-center gap-2 min-w-0 flex-1">
            <span
              className="truncate font-medium"
              style={{
                color:
                  dockVision.phase === 'error'
                    ? 'var(--error)'
                    : dockVision.busy
                      ? 'var(--accent)'
                      : 'var(--text-secondary)',
              }}
              title={dockVision.line}
            >
              {dockVision.line}
            </span>
            {dockVision.pct != null && dockVision.busy ? (
              <div
                className="flex-shrink-0 rounded-full overflow-hidden"
                style={{
                  width: 72,
                  height: 6,
                  background: 'var(--bg-secondary)',
                  border: '1px solid var(--border)',
                }}
                aria-label={`Vision download ${dockVision.pct}%`}
              >
                <div
                  className="h-full rounded-full transition-all duration-300"
                  style={{
                    width: `${dockVision.pct}%`,
                    background: 'var(--accent)',
                  }}
                />
              </div>
            ) : null}
          </div>
        ) : (
          <div className="flex-1 min-w-0" />
        )}

        {status === 'disconnected' && (
          <button
            onClick={() => window.location.reload()}
            className="px-2 py-0.5 rounded text-xs flex-shrink-0"
            style={{ background: 'var(--error)', color: '#fff' }}
          >
            Reconnect
          </button>
        )}
      </div>

      {/* Controls row */}
      <div className="flex items-center justify-between px-3 py-1 text-xs gap-2">
        <div className="flex items-center gap-1.5 min-w-0 flex-wrap">
          <SegButton active={planMode} onClick={onTogglePlanMode} title="Plan mode (Ctrl+B)">
            {planMode ? 'Plan' : 'Build'}
          </SegButton>

          {advanced && (
            <>
              <SegButton
                active={panel === 'memory'}
                onClick={() => onTogglePanel('memory')}
                title={
                  hasCheckpoint
                    ? 'Memory, checkpoints & plans (checkpoint available)'
                    : 'Memory, checkpoints & plans'
                }
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
            </>
          )}
          <SegButton
            active={panel === 'settings'}
            onClick={() => onTogglePanel('settings')}
            title="Settings — provider, project, theme, account"
          >
            Settings
          </SegButton>
          {onOpenHelp && (
            <SegButton
              active={false}
              onClick={() => onOpenHelp()}
              title="Help wiki — owner's manual (F1)"
            >
              Help
            </SegButton>
          )}
          {advanced && (
            <SegButton
              active={false}
              onClick={openWebUi}
              title="Hide desktop to tray and open the WebUI chat in your browser"
            >
              WebUI
            </SegButton>
          )}

          {updateAvailable && (
            <button
              onClick={() => (onInstallUpdate ? onInstallUpdate() : onCheckUpdates())}
              className="px-2 py-0.5 rounded text-xs font-medium"
              style={{ background: 'var(--accent)', color: '#fff' }}
            >
              Update
            </button>
          )}
        </div>

        <div className="flex items-center gap-1.5 flex-shrink-0">
          {onUiModeChange && (
            <button
              type="button"
              className="text-[10px] px-1.5 py-0.5 rounded font-medium"
              title={
                advanced
                  ? 'Advanced UI (full chrome) — click for Simple UI'
                  : 'Simple UI (calm chrome) — click for Advanced UI'
              }
              onClick={() => onUiModeChange(advanced ? 'simple' : 'advanced')}
              style={{
                background: advanced
                  ? 'color-mix(in srgb, var(--accent) 22%, transparent)'
                  : 'var(--bg-tertiary)',
                color: advanced ? 'var(--accent)' : 'var(--text-muted)',
                border: `1px solid ${advanced ? 'var(--accent)' : 'var(--border)'}`,
              }}
            >
              {advanced ? 'Advanced UI' : 'Simple UI'}
            </button>
          )}
          {advanced && onOpenUsage && (
            <button
              type="button"
              className="text-xs px-1.5 py-0.5 rounded"
              title="Usage & Continuity dashboard"
              onClick={onOpenUsage}
              style={{
                background: 'var(--bg-tertiary)',
                color: 'var(--text-muted)',
                border: '1px solid var(--border)',
              }}
            >
              Usage
            </button>
          )}
          {connectedProviders.length > 0 && onProviderModelChange ? (
            <>
              <select
                value={provider}
                disabled={streaming}
                onChange={(e) => {
                  const pid = e.target.value
                  const p = connectedProviders.find((x) => x.id === pid)
                  const nextModel =
                    p?.last_model
                    || p?.default_model
                    || p?.models?.[0]?.id
                    || model
                  onProviderModelChange(pid, nextModel)
                }}
                className="text-xs rounded px-1.5 py-0.5 outline-none"
                title={streaming ? 'Stop generation to switch provider' : 'Active provider'}
                style={{
                  background: 'var(--bg-tertiary)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border)',
                  maxWidth: 110,
                  opacity: streaming ? 0.6 : 1,
                }}
              >
                {connectedProviders.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              <select
                value={model}
                disabled={streaming}
                onChange={(e) => onProviderModelChange(provider, e.target.value)}
                className="text-xs rounded px-1.5 py-0.5 outline-none"
                title={streaming ? 'Stop generation to switch model' : 'Active model'}
                style={{
                  background: 'var(--bg-tertiary)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border)',
                  maxWidth: 140,
                  opacity: streaming ? 0.6 : 1,
                }}
              >
                {(
                  connectedProviders.find((p) => p.id === provider)?.models
                  || models.map((m) => ({ id: m.id, name: m.name }))
                ).map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </>
          ) : models.length > 0 && onModelChange ? (
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

          {advanced && (
            <>
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

              <button
                type="button"
                onClick={() => {
                  const i = TOOL_PROCESS_CYCLE.indexOf(toolProcessMode)
                  const next =
                    TOOL_PROCESS_CYCLE[(i >= 0 ? i + 1 : 0) % TOOL_PROCESS_CYCLE.length]!
                  onToolProcessChange?.(next)
                }}
                className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide"
                title={
                  toolProcessMode === 'off'
                    ? 'Min — step names only (click → Med)'
                    : toolProcessMode === 'medium'
                      ? 'Med — path/command + short results (click → Full)'
                      : 'Full — complete process output (click → Min)'
                }
                aria-label={`Tool process ${toolProcessMode}`}
                style={{
                  background:
                    toolProcessMode === 'off'
                      ? 'var(--bg-tertiary)'
                      : toolProcessMode === 'medium'
                        ? 'color-mix(in srgb, var(--accent) 22%, var(--bg-tertiary))'
                        : 'var(--accent)',
                  color: isFullProcessMode(toolProcessMode) ? '#fff' : 'var(--text-secondary)',
                  border: `1px solid ${toolProcessMode === 'off' ? 'var(--border)' : 'var(--accent)'}`,
                  minWidth: 40,
                }}
              >
                {toolProcessMode === 'off'
                  ? 'Min'
                  : toolProcessMode === 'medium'
                    ? 'Med'
                    : 'Full'}
              </button>
            </>
          )}

          <ThemeSwitcher currentId={themeId} currentTheme={theme} onChange={onThemeChange} />
        </div>
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
