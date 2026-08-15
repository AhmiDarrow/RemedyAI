import { getServerUrl } from '../api/client'
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
import {
  coerceDemoModel,
  DEMO_DEFAULT_MODEL,
  demoModelOptions,
  isDemoModelAllowed,
} from '../utils/demoModels'

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

/** Hard fallbacks when connected catalog is empty (never demo ids on real providers). */
const PROVIDER_FALLBACK_MODELS: Record<string, { id: string; name: string }[]> = {
  deepseek: [
    { id: 'deepseek-v4-flash', name: 'DeepSeek V4 Flash' },
    { id: 'deepseek-v4-pro', name: 'DeepSeek V4 Pro' },
  ],
  xai: [
    { id: 'grok-4.5', name: 'Grok 4.5' },
    { id: 'grok-4.3', name: 'Grok 4.3' },
    { id: 'grok-4', name: 'Grok 4' },
  ],
  openai: [{ id: 'gpt-4o-mini', name: 'GPT-4o Mini' }],
  google: [{ id: 'gemini-2.5-flash', name: 'Gemini 2.5 Flash' }],
  groq: [{ id: 'llama-3.3-70b-versatile', name: 'Llama 3.3 70B' }],
  anthropic: [{ id: 'claude-3-5-sonnet-latest', name: 'Claude 3.5 Sonnet' }],
  mistral: [{ id: 'mistral-small-latest', name: 'Mistral Small' }],
  ollama: [{ id: 'llama3.2', name: 'Llama 3.2' }],
  poe: [
    { id: 'Claude-Sonnet-4.6', name: 'Claude Sonnet 4.6' },
    { id: 'Claude-Opus-4.7', name: 'Claude Opus 4.7' },
    { id: 'GPT-5.4', name: 'GPT-5.4' },
    { id: 'Gemini-3.1-Pro', name: 'Gemini 3.1 Pro' },
    { id: 'Grok-4', name: 'Grok 4' },
  ],
}

/** Models for the active provider: live endpoint list first, catalog as fallback. */
function modelOptionsForProvider(
  provider: string | undefined,
  connected: ConnectedProvider[],
  models: ModelInfo[],
): { id: string; name: string }[] {
  const pid = (provider || '').trim() || 'openai'
  if (pid === 'demo') {
    return demoModelOptions(
      connected.find((p) => p.id === 'demo')?.models
        || models.filter((m) => m.provider === 'demo'),
    )
  }

  // Prefer live GET /models (tagged with this provider) — intelligent endpoint discovery.
  const live = models
    .filter((m) => m.provider === pid)
    .map((m) => ({ id: m.id, name: m.name || m.id }))

  const fromConn = (connected.find((p) => p.id === pid)?.models || []).map((m) => ({
    id: m.id,
    name: m.name || m.id,
  }))

  const seen = new Set<string>()
  let list: { id: string; name: string }[] = []
  // RMB: prefer connected/discovered GGUFs first (live /models is often a full path)
  const ordered =
    pid === 'rmb' ? [...fromConn, ...live] : [...live, ...fromConn]
  for (const m of ordered) {
    if (!m.id || seen.has(m.id)) continue
    // Drop guest-demo ids that leaked into non-demo providers
    // Drop demo-tagged names that leaked onto non-demo providers (parens or suffix).
    if (isDemoModelAllowed(m.id) || /\(demo\)|\bdemo\s*$/i.test(m.name || '')) continue
    // Normalize RMB full paths → stem ids for a clean picker
    let id = m.id
    let name = m.name || m.id
    if (pid === 'rmb' && (id.includes('\\') || id.includes('/') || id.toLowerCase().endsWith('.gguf'))) {
      const base = id.replace(/^.*[\\/]/, '').replace(/\.gguf$/i, '')
      id = base
      name = m.name?.includes('.gguf') ? m.name.replace(/^.*[\\/]/, '') : base
    }
    if (seen.has(id)) continue
    seen.add(id)
    list.push({ id, name })
  }
  if (list.length === 0) {
    list = PROVIDER_FALLBACK_MODELS[pid] || []
  }
  return list
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

  // Display provider: only treat as Demo when the *model id* is a curated guest id
  // AND the parent still says another provider (cross-wire). Never flip a real
  // connected provider (e.g. custom OpenAI-compatible) just because its model
  // name happens to match the demo allowlist.
  const effectiveProvider = useMemo(() => {
    const p = (provider || '').trim()
    if (!p || p === 'demo') return p
    if (!isDemoModelAllowed(model)) return p
    // If the provider is a real connected provider, keep its name — don't
    // override to 'demo' just because the model id overlaps the allowlist.
    const isConnected = connectedProviders.some((cp) => cp.id === p)
    if (isConnected) return p
    return 'demo'
  }, [provider, model, connectedProviders])

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
            const casN = Number(p.organism?.cas_count || p.cas?.count || 0)
            if (casN > 0) bits.push(`${casN} mem`)
            // Somatic / organism mood (Soul Field) + lean metabolism
            const soma = p.soma
            if (soma?.label) {
              const emoji = soma.emoji ? `${soma.emoji} ` : ''
              bits.push(`${emoji}${soma.label}`)
            }
            const meta = p.metabolism
            if (meta && typeof meta === 'object') {
              const eu = Number((meta as { evidence_units?: number }).evidence_units || 0)
              const du = Number((meta as { decision_units?: number }).decision_units || 0)
              const tier = (meta as { tier_label?: string; tier?: number }).tier_label
                || ((meta as { tier?: number }).tier != null
                  ? `L${(meta as { tier?: number }).tier}`
                  : '')
              if (tier) bits.push(String(tier))
              if (eu > 0 || du > 0) bits.push(`EU ${eu}·DU ${du}`)
            }
            setAlerts(bits.join(' · '))
            setAccessScope(String(p.access_scope || ''))
            // Tray tooltip mirrors organism mood when running under Tauri
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
      window.open(getServerUrl() + '/', '_blank', 'noopener,noreferrer')
    })()
  }

  return (
    <div
      data-remedy-status-bar
      className="flex flex-col"
      style={{
        color: 'var(--text-muted)',
      }}
    >
      {/* Status dock — vision progress, server, alerts */}
      <div
        className="flex items-center gap-3 px-3 py-1.5 text-xs border-b"
        style={{
          borderColor: 'color-mix(in srgb, var(--border) 80%, transparent)',
          background: 'color-mix(in srgb, var(--bg-tertiary) 70%, transparent)',
          minHeight: 32,
        }}
      >
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
              <select
                value={
                  // Prefer exact provider; only fall back if missing from list (keep label stable).
                  connectedProviders.some((p) => p.id === effectiveProvider)
                    ? effectiveProvider
                    : connectedProviders.some((p) => p.id === (provider || ''))
                      ? (provider || '')
                      : effectiveProvider || connectedProviders[0]?.id || ''
                }
                disabled={streaming}
                onChange={(e) => {
                  const pid = e.target.value
                  const p = connectedProviders.find((x) => x.id === pid)
                  // Never keep a demo model when switching to DeepSeek/xAI (etc.).
                  const preferred =
                    pid === 'demo'
                      ? (isDemoModelAllowed(model) ? model : p?.last_model || DEMO_DEFAULT_MODEL)
                      : p?.last_model || p?.default_model || undefined
                  const nextModel = pickModelForProvider(
                    pid,
                    preferred,
                    connectedProviders,
                    models,
                  )
                  onProviderModelChange(
                    pid,
                    nextModel || (pid === 'demo' ? DEMO_DEFAULT_MODEL : ''),
                  )
                }}
                className="ui-select"
                title={streaming ? 'Stop generation to switch provider' : 'Active provider'}
                style={{
                  background: 'var(--bg-tertiary)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border)',
                  maxWidth: 110,
                  opacity: streaming ? 0.6 : 1,
                }}
              >
                {/* Ensure Demo appears even if connected list lagged */}
                {effectiveProvider === 'demo'
                  && !connectedProviders.some((p) => p.id === 'demo') && (
                    <option value="demo">Demo (Free)</option>
                  )}
                {connectedProviders.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              <select
                value={safeModel || model}
                disabled={streaming}
                onChange={(e) =>
                  onProviderModelChange(effectiveProvider || provider || '', e.target.value)
                }
                className="ui-select"
                title={streaming ? 'Stop generation to switch model' : 'Active model'}
                style={{
                  background: 'var(--bg-tertiary)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border)',
                  maxWidth: 200,
                  opacity: streaming ? 0.6 : 1,
                }}
              >
                {/* Keep current selection visible even if list is still loading */}
                {safeModel
                  && !modelOpts.some((m) => m.id === safeModel)
                  && (
                    <option value={safeModel}>{safeModel}</option>
                  )}
                {modelOpts.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </>
          ) : models.length > 0 && onModelChange ? (
            <select
              value={
                provider === 'demo' && !isDemoModelAllowed(model)
                  ? DEMO_DEFAULT_MODEL
                  : model
              }
              disabled={streaming}
              onChange={(e) => onModelChange(
                provider === 'demo' ? coerceDemoModel(e.target.value) : e.target.value,
              )}
              className="ui-select"
              title={streaming ? 'Stop generation to switch model' : 'Active model'}
              style={{
                background: 'var(--bg-tertiary)',
                color: 'var(--text-primary)',
                border: '1px solid var(--border)',
                maxWidth: 140,
                opacity: streaming ? 0.6 : 1,
              }}
            >
              {models
                .filter((m) => provider !== 'demo' || isDemoModelAllowed(m.id))
                .map((m) => (
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
                className="ui-select"
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
                className="flex items-center justify-center rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide transition-colors"
                title={
                  autoApprove
                    ? 'Auto-approve on — shell/write run without prompts. Click for Ask.'
                    : 'Ask before risky tools (safe default). Click for Auto.'
                }
                aria-label={autoApprove ? 'Auto-approve on' : 'Ask before risky actions'}
                aria-pressed={autoApprove}
                style={{
                  background: autoApprove
                    ? 'color-mix(in srgb, var(--success) 28%, var(--bg-tertiary))'
                    : 'var(--bg-tertiary)',
                  color: autoApprove ? 'var(--success)' : 'var(--text-secondary)',
                  border: `1px solid ${autoApprove ? 'var(--success)' : 'var(--border)'}`,
                  minWidth: 36,
                }}
              >
                {autoApprove ? 'Auto' : 'Ask'}
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
      aria-pressed={active}
      className={`seg-btn${active ? ' is-active' : ''}`}
    >
      {children}
    </button>
  )
}
