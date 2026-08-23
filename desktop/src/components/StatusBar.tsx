import { getServerUrl } from '../api/client'
import { useState, useEffect, useMemo } from 'react'
import { getLatestCheckpoint, getPartnerStatus } from '../api/partner'
import { getVisionStatus, type VisionStatus } from '../api/vision'
import {
  getCoordinationPresence,
  type CoordinationBeacon,
} from '../api/coordination'
import type { ConnectedProvider } from '../api/providers'
import { ThemeSwitcher } from './ThemeSwitcher'
import { FormSelect } from './settings/formUi'
import type { ThemeId, Theme } from '../themes'
import type { ModelInfo } from '../App'
import {
  isFullProcessMode,
  TOOL_PROCESS_CYCLE,
  type ToolProcessMode,
} from '../utils/toolLabels'
import type { UiMode } from '../utils/uiMode'
import { mergeModelOptions, modelOptionLabel, type ModelOption } from '../api/modelDiscovery'
import { isLinuxDesktop } from '../utils/platform'

export type ThinkingLevel = 'off' | 'low' | 'medium' | 'high'
export type ApprovalMode = 'ask' | 'auto' | 'full'

export const APPROVAL_CYCLE: ApprovalMode[] = ['ask', 'auto', 'full']

export function normalizeApprovalMode(raw: unknown): ApprovalMode {
  const am = String(raw || 'ask').toLowerCase()
  if (am === 'auto' || am === 'full') return am
  return 'ask'
}

export function nextApprovalMode(mode: ApprovalMode): ApprovalMode {
  const i = APPROVAL_CYCLE.indexOf(mode)
  return APPROVAL_CYCLE[(i >= 0 ? i + 1 : 0) % APPROVAL_CYCLE.length]!
}

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
  /** Last model-list failure (network or endpoint discovery) — shown as a hint. */
  modelsError?: string | null
  onProviderModelChange?: (provider: string, model: string) => void
  thinkingLevel: ThinkingLevel
  onThinkingLevelChange?: (level: ThinkingLevel) => void
  approvalMode: ApprovalMode
  onApprovalModeChange?: (mode: ApprovalMode) => void
  /** Opt-in privacy: tighter tool egress to the LLM (default off = fast). */
  privacyMode?: boolean
  onPrivacyModeChange?: (on: boolean) => void
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
  /** Switch back to the Grove partner surface (default home). */
  onOpenGrove?: () => void
  /** Which surface the bar sits under; on Grove the surface button offers Studio. */
  surface?: 'grove' | 'studio'
  /** Switch to the Studio workbench (shown on Grove). */
  onOpenStudio?: () => void
  /** Speak replies aloud (same setting as Grove). */
  speakReplies?: boolean
  speaking?: boolean
  onToggleSpeak?: () => void
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

/**
 * Models for the active provider: live GET /models rows (tagged with this
 * provider) first, then the connected/session catalog. No client-side model
 * tables — when both are empty the picker is empty and the bar shows why.
 */
function modelOptionsForProvider(
  provider: string | undefined,
  connected: ConnectedProvider[],
  models: ModelInfo[],
): ModelOption[] {
  const pid = (provider || '').trim()
  if (!pid) return []
  const live: ModelOption[] = models
    .filter((m) => m.provider === pid)
    .map((m) => ({ id: m.id, name: m.name || m.id, source: m.source }))
  const fromConn: ModelOption[] = (connected.find((p) => p.id === pid)?.models || []).map(
    (m) => ({ id: m.id, name: m.name || m.id }),
  )
  // RMB: prefer connected/discovered GGUFs first (live /models is often a full path)
  const merged =
    pid === 'rmb' ? mergeModelOptions(fromConn, live) : mergeModelOptions(live, fromConn)
  if (pid !== 'rmb') return merged
  // Normalize RMB full paths → stem ids for a clean picker
  return mergeModelOptions(
    merged.map((m) => {
      const id = m.id
      if (!(id.includes('\\') || id.includes('/') || id.toLowerCase().endsWith('.gguf'))) return m
      const base = id.replace(/^.*[\\/]/, '').replace(/\.gguf$/i, '')
      return {
        ...m,
        id: base,
        name: m.name?.includes('.gguf') ? m.name.replace(/^.*[\\/]/, '') : base,
      }
    }),
  )
}

function pickModelForProvider(
  provider: string | undefined,
  preferred: string | undefined,
  connected: ConnectedProvider[],
  models: ModelInfo[],
): string {
  const opts = modelOptionsForProvider(provider, connected, models)
  const pref = (preferred || '').trim()
  if (!pref) return opts[0]?.id || ''
  // Exact match
  if (opts.some((m) => m.id === pref)) return pref
  // RMB: match stem if preferred is a path or longer name
  const stem = pref.replace(/^.*[\\/]/, '').replace(/\.gguf$/i, '')
  if (opts.some((m) => m.id === stem)) return stem
  // Always keep preferred visible even if not in list yet
  return pref
}

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
  // Idle / ready / setup-pending are not worth a permanent label; the dock
  // only speaks while something is installing or has gone wrong.
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
  modelsError = null,
  onProviderModelChange,
  thinkingLevel,
  onThinkingLevelChange,
  approvalMode,
  onApprovalModeChange,
  privacyMode = false,
  onPrivacyModeChange,
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
  onOpenGrove,
  surface = 'studio',
  onOpenStudio,
  speakReplies = false,
  speaking = false,
  onToggleSpeak,
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
  // Body coordination: the OTHER live muscles (sibling sessions) building now.
  const [siblings, setSiblings] = useState<CoordinationBeacon[]>([])

  // The parent owns provider identity; the client no longer second-guesses it
  // from model ids (the demo allowlist lives in the backend only).
  const effectiveProvider = (provider || '').trim()

  const modelOpts = useMemo(
    () => modelOptionsForProvider(effectiveProvider, connectedProviders, models),
    [effectiveProvider, connectedProviders, models],
  )
  // Display-only coerce so the <select> always has a valid option. Do not write
  // this back in an effect — that fought live discovery + session/global LLM.
  const safeModel = useMemo(
    () =>
      pickModelForProvider(
        effectiveProvider,
        model,
        connectedProviders,
        models,
      ),
    [effectiveProvider, model, connectedProviders, models],
  )

  // effectiveProvider handles display-only coercion to 'demo' when the model
  // overlaps the demo allowlist.  Do NOT mutate parent state from a side effect
  // — that fires before connectedProviders loads (empty array on first render)
  // and permanently overwrites a real connected provider to 'demo' even after
  // the live provider list arrives.
  const [hasCheckpoint, setHasCheckpoint] = useState(false)

  useEffect(() => {
    let cancelled = false
    let failStreak = 0
    async function check() {
      try {
        let p: Awaited<ReturnType<typeof getPartnerStatus>> | null = null
        try {
          p = await getPartnerStatus(sessionId)
        } catch {
          p = null
        }
        if (cancelled) return
        if (!p) {
          try {
            const ping = await fetch(getServerUrl() + '/api/ping', {
              signal: AbortSignal.timeout(2500),
              headers: { Accept: 'application/json' },
            })
            if (ping.ok) {
              failStreak = 0
              setStatus('connected')
              return
            }
          } catch {
            /* offline */
          }
        }
        if (p) {
          failStreak = 0
          setStatus('connected')
          if (p.version) setVersion(String(p.version))
          try {
            if (cancelled) return
            const bits: string[] = []
            if (p.pending_approvals > 0) bits.push(`${p.pending_approvals} approve`)
            if (p.active_goal) {
              const t = String(p.active_goal)
              bits.push(t.length > 28 ? `${t.slice(0, 26)}…` : t)
            } else if (p.open_goals > 0) {
              bits.push(`${p.open_goals} life`)
            }
            // Only things the owner can act on live here. Memory counts,
            // organism mood (◆ Focused) and metabolism tiers (L1, EU·DU)
            // are internals — they read as warnings and confuse people.
            setAlerts(bits.join(' · '))
            setAccessScope(String(p.access_scope || ''))
            // Tray tooltip mirrors organism mood when running under Tauri
            const soma = p.soma
            if (soma?.tray_tooltip) {
              try {
                const { invoke } = await import('@tauri-apps/api/core')
                await invoke('set_tray_tooltip', { tooltip: soma.tray_tooltip })
              } catch {
                /* webui / no tray */
              }
            }
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
  }, [sessionId])

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

  // Poll body-coordination presence: which sibling sessions are building and
  // what they hold. Quiet when alone (slow poll); livelier when the body is
  // busy so holds/goals stay fresh in the tooltip.
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    async function tick() {
      let nextMs = 30_000
      try {
        const p = await getCoordinationPresence(sessionId)
        if (!cancelled) {
          const others = (p.beacons || []).filter((b) => !b.you)
          setSiblings(others)
          nextMs = others.length > 0 ? 8_000 : 30_000
        }
      } catch {
        if (!cancelled) setSiblings([])
        nextMs = 45_000
      }
      if (!cancelled) timer = setTimeout(() => void tick(), nextMs)
    }
    void tick()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [sessionId])

  const bodyTip = useMemo(() => {
    if (!siblings.length) return ''
    const lines = siblings.map((b) => {
      const who = b.muscle || 'another session'
      const where = b.project || '?'
      const goal = b.goal ? ` — ${b.goal}` : ''
      const held = b.held_files.length
        ? `\n   holding: ${b.held_files.slice(0, 6).join(', ')}${b.held_count > 6 ? ` (+${b.held_count - 6})` : ''}`
        : ''
      return `• ${who} · ${where}${goal} (${b.phase})${held}`
    })
    return `Other Remedy sessions at work — their held files are protected from overwrites:\n${lines.join('\n')}`
  }, [siblings])

  const dotColor =
    status === 'connected' ? 'var(--success)' : status === 'checking' ? 'var(--warning)' : 'var(--error)'
  const autoApprove = approvalMode === 'auto'
  const fullControl = approvalMode === 'full'

  const dockVision = useMemo(() => {
    const line = visionLine(vision)
    const pct = visionPct(vision)
    const phase = (vision?.progress?.phase || '').toLowerCase()
    const busy = visionIsBusy(vision)
    const show = busy || phase === 'error' || phase === 'cancelled'
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
      window.open(getServerUrl() + '/', '_blank', 'noopener,noreferrer')
    })()
  }

  return (
    <div
      data-remedy-status-bar
      className="status-ctrl-row flex items-center px-3 gap-2 text-xs"
      style={{
        color: 'var(--text-muted)',
      }}
    >
      {/* Server / vision / alerts — same row as the action buttons so
          maximize on WSLg does not hide the control strip under the taskbar. */}
      <div className="flex items-center gap-2 flex-shrink-0 min-w-0">
        <div
          className="flex items-center gap-1.5 flex-shrink-0"
          title={status === 'connected' ? `Remedy ${version || ''}`.trim() : 'Server offline'}
        >
          <span
            className={`inline-block w-2 h-2 rounded-full${
              status === 'disconnected' ? ' status-offline-dot' : ''
            }`}
            style={{ background: dotColor }}
            aria-hidden
          />
          <span className="font-medium" style={{ color: 'var(--text-secondary)' }}>
            {status === 'connected'
              ? 'Connected'
              : status === 'checking'
                ? 'Connecting…'
                : 'Offline'}
          </span>
        </div>

        {streaming && (
          <span
            className="status-streaming-pill px-1.5 py-0.5 rounded font-medium flex-shrink-0 inline-flex items-center gap-1.5"
            style={{
              color: 'var(--accent)',
              background: 'color-mix(in srgb, var(--accent) 12%, transparent)',
            }}
            title="Agent is generating a reply"
          >
            <span className="live-stream-dot" aria-hidden />
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
            {alerts}
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

        {siblings.length > 0 && (
          <span
            className="px-1.5 py-0.5 rounded flex-shrink-0 text-[10px] font-medium inline-flex items-center gap-1"
            style={{
              color: 'var(--accent)',
              background: 'color-mix(in srgb, var(--accent) 12%, transparent)',
              border: '1px solid var(--border)',
              cursor: 'help',
            }}
            title={bodyTip}
            aria-label={`${siblings.length} other Remedy session${siblings.length > 1 ? 's' : ''} working`}
          >
            {siblings.length === 1
              ? `1 other session · ${siblings[0].muscle || siblings[0].project || 'working'}`
              : `${siblings.length} other sessions working`}
          </span>
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
        ) : null}

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

      <div className="flex items-center justify-between gap-2 min-w-0 flex-1">
        <div className="flex items-center gap-1.5 min-w-0 flex-nowrap overflow-x-auto">
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
            title="Settings — provider, project, appearance"
          >
            Settings
          </SegButton>
          {onPrivacyModeChange && (
            <SegButton
              active={privacyMode}
              onClick={() => onPrivacyModeChange(!privacyMode)}
              title={
                privacyMode
                  ? 'Privacy mode ON — tighter tool caps + email/phone scrub before the cloud model. Click to turn off (faster).'
                  : 'Privacy mode OFF (default) — lightning path with secret scrub. Click for tighter privacy to the model.'
              }
            >
              {privacyMode ? 'Privacy · on' : 'Privacy'}
            </SegButton>
          )}
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
              title={
                isLinuxDesktop()
                  ? 'Minimize the desktop and open the WebUI chat in your browser'
                  : 'Hide desktop to tray and open the WebUI chat in your browser'
              }
            >
              WebUI
            </SegButton>
          )}

          {updateAvailable && !isLinuxDesktop() && (
            <button
              onClick={() => (onInstallUpdate ? onInstallUpdate() : onCheckUpdates())}
              className="px-2 py-0.5 rounded text-xs font-medium"
              style={{ background: 'var(--accent)', color: '#fff' }}
            >
              Update
            </button>
          )}
        </div>

        <div className="flex items-center gap-1.5 flex-shrink-0 flex-nowrap">
          {onToggleSpeak && (
            <button
              type="button"
              className={`seg-btn${speakReplies ? ' is-active' : ''}${speaking ? ' speaking' : ''}`}
              title={
                speakReplies
                  ? 'Speaking replies aloud — click to go quiet'
                  : 'Speak replies aloud'
              }
              aria-pressed={speakReplies}
              onClick={onToggleSpeak}
            >
              {speakReplies ? '🔊' : '🔇'}
            </button>
          )}
          {surface === 'grove' && onOpenStudio ? (
            <button
              type="button"
              className="seg-btn"
              title="Studio — the full workbench (files, shell, browser rail)"
              onClick={onOpenStudio}
            >
              ▣ Studio
            </button>
          ) : onOpenGrove ? (
            <button
              type="button"
              className="seg-btn"
              title="Grove — your partner home (goals, live actions, story)"
              onClick={onOpenGrove}
            >
              ✦ Grove
            </button>
          ) : null}
          {onUiModeChange && (
            <button
              type="button"
              className={`seg-btn${advanced ? ' is-active' : ''}`}
              title={
                advanced
                  ? 'Advanced UI (full chrome) — click for Simple'
                  : 'Simple UI (calm chrome) — click for Advanced'
              }
              onClick={() => onUiModeChange(advanced ? 'simple' : 'advanced')}
            >
              {advanced ? 'Advanced' : 'Simple'}
            </button>
          )}
          {advanced && onOpenUsage && (
            <button
              type="button"
              className="seg-btn"
              title="Usage & Continuity dashboard"
              onClick={onOpenUsage}
            >
              Usage
            </button>
          )}
          {connectedProviders.length > 0 && onProviderModelChange ? (
            <>
              <FormSelect
                size="sm"
                className="mb-0 max-w-[110px]"
                disabled={streaming}
                title={streaming ? 'Stop generation to switch provider' : 'Active provider'}
                value={
                  // Prefer exact provider; only fall back if missing from list (keep label stable).
                  connectedProviders.some((p) => p.id === effectiveProvider)
                    ? effectiveProvider
                    : connectedProviders.some((p) => p.id === (provider || ''))
                      ? (provider || '')
                      : effectiveProvider || connectedProviders[0]?.id || ''
                }
                onChange={(pid) => {
                  const p = connectedProviders.find((x) => x.id === pid)
                  // Never carry the previous provider's model across; prefer the
                  // provider's remembered model, then the backend catalog default.
                  const preferred = p?.last_model || p?.default_model || undefined
                  const nextModel = pickModelForProvider(
                    pid,
                    preferred,
                    connectedProviders,
                    models,
                  )
                  onProviderModelChange(pid, nextModel || '')
                }}
                options={[
                  ...(effectiveProvider === 'demo'
                    && !connectedProviders.some((p) => p.id === 'demo')
                    ? [{ value: 'demo', label: 'Demo (Free)' }]
                    : []),
                  ...connectedProviders.map((p) => ({ value: p.id, label: p.name })),
                ]}
              />
              <FormSelect
                size="sm"
                className="mb-0 max-w-[200px]"
                disabled={streaming}
                title={streaming ? 'Stop generation to switch model' : 'Active model'}
                value={safeModel || model}
                onChange={(id) =>
                  onProviderModelChange(effectiveProvider || provider || '', id)
                }
                options={[
                  ...(safeModel && !modelOpts.some((m) => m.id === safeModel)
                    ? [{ value: safeModel, label: safeModel }]
                    : []),
                  ...modelOpts.map((m) => ({ value: m.id, label: modelOptionLabel(m) })),
                ]}
              />
              {modelsError && (
                <span
                  className="px-1 text-[10px] flex-shrink-0"
                  style={{ color: 'var(--warning)', cursor: 'help' }}
                  title={modelsError}
                >
                  ⚠ models
                </span>
              )}
            </>
          ) : models.length > 0 && onModelChange ? (
            <FormSelect
              size="sm"
              className="mb-0 max-w-[140px]"
              disabled={streaming}
              title={streaming ? 'Stop generation to switch model' : 'Active model'}
              value={model}
              onChange={(id) => onModelChange(id)}
              options={models.map((m) => ({ value: m.id, label: modelOptionLabel(m) }))}
            />
          ) : (
            <span className="truncate max-w-[8rem]" title={model}>
              {model}
            </span>
          )}

          {advanced && (
            <>
              <FormSelect
                size="sm"
                className="mb-0 max-w-[7.5rem]"
                title="Thinking level"
                value={thinkingLevel}
                onChange={(id) => onThinkingLevelChange?.(id as ThinkingLevel)}
                options={THINKING_OPTIONS.map((o) => ({
                  value: o.id,
                  label: `Think ${o.label}`,
                }))}
              />

              <button
                type="button"
                onClick={() => onApprovalModeChange?.(nextApprovalMode(approvalMode))}
                className="status-chip flex items-center justify-center rounded px-1.5 text-[10px] font-semibold uppercase tracking-wide transition-colors"
                title={
                  fullControl
                    ? 'Full (warn) — write jail off except auth. Click for Ask.'
                    : autoApprove
                      ? 'Auto (in-project) — build/write without prompts. Jail stays outside the folder. Click for Full.'
                      : 'Ask before risky tools (safe default). Click for Auto.'
                }
                aria-label={
                  fullControl
                    ? 'Full control with warnings'
                    : autoApprove
                      ? 'Auto-approve in project'
                      : 'Ask before risky actions'
                }
                aria-pressed={autoApprove || fullControl}
                style={{
                  background: fullControl
                    ? 'color-mix(in srgb, var(--warning, #e6a23c) 32%, var(--bg-tertiary))'
                    : autoApprove
                      ? 'color-mix(in srgb, var(--success) 28%, var(--bg-tertiary))'
                      : 'var(--bg-tertiary)',
                  color: fullControl
                    ? 'var(--warning, #e6a23c)'
                    : autoApprove
                      ? 'var(--success)'
                      : 'var(--text-secondary)',
                  border: `1px solid ${
                    fullControl
                      ? 'var(--warning, #e6a23c)'
                      : autoApprove
                        ? 'var(--success)'
                        : 'var(--border)'
                  }`,
                  minWidth: 36,
                }}
              >
                {fullControl ? 'Full' : autoApprove ? 'Auto' : 'Ask'}
              </button>

              <button
                type="button"
                onClick={() => {
                  const i = TOOL_PROCESS_CYCLE.indexOf(toolProcessMode)
                  const next =
                    TOOL_PROCESS_CYCLE[(i >= 0 ? i + 1 : 0) % TOOL_PROCESS_CYCLE.length]!
                  onToolProcessChange?.(next)
                }}
                className="status-chip px-1.5 rounded text-[10px] font-semibold uppercase tracking-wide"
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
      aria-pressed={active}
      className={`seg-btn${active ? ' is-active' : ''}`}
    >
      {children}
    </button>
  )
}
