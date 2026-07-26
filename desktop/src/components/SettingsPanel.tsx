import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { getSettings, updateSettings, type Settings, type SettingsUpdate } from '../api/settings'
import {
  getVisionStatus,
  getNanoSwarmStatus,
  type VisionStatus,
  type NanoSwarmStatus,
} from '../api/vision'
import {
  getXaiAuthStatus,
  startXaiLogin,
  pollXaiLogin,
  logoutXai,
  openExternalUrl,
  type XaiAuthStatus,
} from '../api/auth'
import {
  listProviders,
  listConnectedProviders,
  FALLBACK_PROVIDERS,
  type ProviderInfo,
  type ConnectedProvider,
} from '../api/providers'
import type { ThemeId } from '../themes'
import type { UpdateInfo } from '../api/updates'
import type { ModelInfo } from '../App'
import type { Density } from '../utils/chatPrefs'
import {
  normalizeToolProcess,
  showsAdvancedDiagnostics,
  type ToolProcessMode,
} from '../utils/toolLabels'
import { sectionMatchesSearch } from './SettingsSection'
import {
  SETTINGS_SECTION_META,
  loadLastSettingsSection,
  saveLastSettingsSection,
  type SettingsSectionId,
} from '../utils/settingsSearch'
import { PERSONAS, pickProjectFolder } from './settings/shared'
import { SettingsFormSections } from './settings/FormSections'

interface SettingsPanelProps {
  open: boolean
  onClose: () => void
  themeId: ThemeId
  onThemeChange: (id: ThemeId) => void
  density?: Density
  onDensityChange?: (d: Density) => void
  customAccent?: string
  onCustomAccentChange?: (hex: string) => void
  updateInfo: UpdateInfo | null
  checkingUpdates: boolean
  /** Live status line from the last check (e.g. "Up to date — v0.10.25"). */
  updateStatus?: string | null
  onCheckUpdates: () => void
  /** Opens the Ollama-style download → install → relaunch screen. */
  onInstallUpdate?: () => void
  models: ModelInfo[]
  onSettingsSaved?: () => void
  toolProcessMode?: ToolProcessMode
  onToolProcessChange?: (mode: ToolProcessMode) => void
  /** Open Help wiki, optionally on a specific article id. */
  onOpenHelp?: (articleId?: string) => void
  /** Fill parent slide (no fixed 300px column / side border). */
  embedded?: boolean
}

export function SettingsPanel({
  open,
  onClose,
  themeId,
  onThemeChange,
  density = 'cozy',
  onDensityChange,
  customAccent = '',
  onCustomAccentChange,
  updateInfo,
  checkingUpdates,
  updateStatus = null,
  onCheckUpdates,
  onInstallUpdate,
  models,
  onSettingsSaved,
  onOpenHelp,
  toolProcessMode,
  onToolProcessChange,
  embedded = false,
}: SettingsPanelProps) {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [errorMessage, setErrorMessage] = useState('')

  const [provider, setProvider] = useState('openai')
  const [model, setModel] = useState('gpt-4o-mini')
  const [baseUrl, setBaseUrl] = useState('https://api.openai.com/v1')
  const [apiKey, setApiKey] = useState('')
  const [apiKeySet, setApiKeySet] = useState(false)
  const [projectPath, setProjectPath] = useState('.')
  const [browserHomeUrl, setBrowserHomeUrl] = useState(
    'https://github.com/AhmiDarrow/RemedyAI',
  )
  const [persona, setPersona] = useState('balanced')
  const [userName, setUserName] = useState('')
  const [agentName, setAgentName] = useState('Remedy')
  const [accessScope, setAccessScope] = useState('project')
  const [launchAtLogin, setLaunchAtLogin] = useState(false)
  const [startInTray, setStartInTray] = useState(false)
  const [closeToTray, setCloseToTray] = useState(false)
  const [skipQuitWarn, setSkipQuitWarn] = useState(false)
  const [webToolsEnabled, setWebToolsEnabled] = useState(false)
  const [httpBootstrap, setHttpBootstrap] = useState(true)
  const [approvalMode, setApprovalMode] = useState<'ask' | 'auto'>('ask')
  const [harnessMode, setHarnessMode] = useState('auto')
  const [harnessMinPct, setHarnessMinPct] = useState(0.75)
  const [harnessMaxPct, setHarnessMaxPct] = useState(0.92)
  const [thinkingLevel, setThinkingLevel] = useState<'off' | 'low' | 'medium' | 'high'>('high')
  const [allowSkillCreation, setAllowSkillCreation] = useState(true)
  const [autoApproveThreshold, setAutoApproveThreshold] = useState(0.8)
  const [logLevel, setLogLevel] = useState('INFO')
  const [sarcasmMode, setSarcasmMode] = useState(false)
  const [settingsSearch, setSettingsSearch] = useState('')
  const [forceSection, setForceSection] = useState<string | null>(null)
  const [visionSectionOpen, setVisionSectionOpen] = useState(false)
  const [toolProcess, setToolProcess] = useState<ToolProcessMode>(
    () => toolProcessMode || 'off',
  )
  const [statusMessage, setStatusMessage] = useState('')
  const [catalog, setCatalog] = useState<ProviderInfo[]>(FALLBACK_PROVIDERS)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [xaiAuth, setXaiAuth] = useState<XaiAuthStatus | null>(null)
  const [xaiLoginBusy, setXaiLoginBusy] = useState(false)
  const [xaiUserCode, setXaiUserCode] = useState('')
  const [xaiVerifyUrl, setXaiVerifyUrl] = useState('')
  const [xaiLoginMsg, setXaiLoginMsg] = useState('')
  const xaiPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [vision, setVision] = useState<VisionStatus | null>(null)
  const [swarm, setSwarm] = useState<NanoSwarmStatus | null>(null)
  const [visionBusy, setVisionBusy] = useState(false)
  const [visionMsg, setVisionMsg] = useState('')
  const visionPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [connectedList, setConnectedList] = useState<ConnectedProvider[]>([])
  const [providerSearch, setProviderSearch] = useState('')
  const [enabledProviders, setEnabledProviders] = useState<string[] | null>(null)
  const [enabledModels, setEnabledModels] = useState<Record<string, string[]>>({})
  const [catalogExpand, setCatalogExpand] = useState<string | null>(null)
  const [skillsBudget, setSkillsBudget] = useState(80)

  const primaryProviders = useMemo(
    () => catalog.filter((p) => !p.advanced),
    [catalog],
  )
  const advancedProviders = useMemo(
    () => catalog.filter((p) => p.advanced),
    [catalog],
  )
  const activeMeta = catalog.find((p) => p.id === provider) || FALLBACK_PROVIDERS[0]
  const showBaseUrl = Boolean(activeMeta?.show_base_url || provider === 'custom')

  // Prefer live discovered models; fall back to catalog models for this provider.
  const providerModels = useMemo(() => {
    const fromApi = models.filter(
      (m) =>
        !m.provider
        || m.provider === provider
        || provider === 'openrouter'
        || provider === 'custom',
    )
    if (fromApi.length > 0) return fromApi
    return (activeMeta?.models || []).map((m) => ({
      id: m.id,
      name: m.name,
      provider,
    }))
  }, [models, provider, activeMeta])

  const stopXaiPoll = useCallback(() => {
    if (xaiPollRef.current) {
      clearInterval(xaiPollRef.current)
      xaiPollRef.current = null
    }
  }, [])

  const stopVisionPoll = useCallback(() => {
    if (visionPollRef.current) {
      clearInterval(visionPollRef.current)
      visionPollRef.current = null
    }
  }, [])

  const refreshVision = useCallback(async () => {
    try {
      const vs = await getVisionStatus()
      setVision(vs)
      // Internal continuity metrics only when Tool process is Full+
      if (showsAdvancedDiagnostics(toolProcess)) {
        const sw = await getNanoSwarmStatus().catch(() => null)
        if (sw) setSwarm(sw)
      } else {
        setSwarm(null)
      }
      return vs
    } catch {
      return null
    }
  }, [toolProcess])

  const startVisionInstallPoll = useCallback(() => {
    stopVisionPoll()
    visionPollRef.current = setInterval(() => {
      void (async () => {
        const vs = await refreshVision()
        const phase = vs?.progress?.phase || ''
        if (phase === 'ready' || phase === 'error' || phase === 'idle' || phase === 'cancelled') {
          stopVisionPoll()
          setVisionBusy(false)
          if (phase === 'ready') {
            setVisionMsg('Visual decoder ready — Qwen2.5-VL 3B')
          } else if (phase === 'error') {
            setVisionMsg(vs?.progress?.error || 'Install failed')
          } else if (phase === 'cancelled') {
            setVisionMsg(
              vs?.progress?.message
                || 'Install cancelled — click Install again to resume partial downloads.',
            )
          }
        }
      })()
    }, 1500)
  }, [refreshVision, stopVisionPoll])

  useEffect(() => () => stopVisionPoll(), [stopVisionPoll])

  const load = useCallback(async () => {
    setLoading(true)
    const t0 = performance.now()
    try {
      // Critical path first — do not wait on vision (was multi-second freezes).
      const [s, providers, connected] = await Promise.all([
        getSettings(),
        listProviders(),
        listConnectedProviders().catch(() => null),
      ])
      setCatalog(providers)
      if (connected?.providers) setConnectedList(connected.providers)
      if (s.enabled_providers !== undefined) {
        setEnabledProviders(s.enabled_providers)
      }
      if (s.enabled_models && typeof s.enabled_models === 'object') {
        setEnabledModels(s.enabled_models as Record<string, string[]>)
      }
      if (s.skills_active_budget) setSkillsBudget(s.skills_active_budget)
      setSettings(s)
      const prov = s.llm_provider || 'openai'
      setProvider(prov)
      setModel(s.llm_model || 'gpt-4o-mini')
      setBaseUrl(s.llm_base_url || 'https://api.openai.com/v1')
      setApiKeySet(s.llm_api_key_set)
      setProjectPath(s.project_path || '.')
      const p = (s.persona || 'balanced').toLowerCase()
      setPersona(
        PERSONAS.some((x) => x.id === p) ? p : p === 'default' ? 'balanced' : 'balanced',
      )
      setUserName((s.user_name || '').trim())
      setAgentName(s.name || 'Remedy')
      setAccessScope(s.access_scope || 'project')
      setLaunchAtLogin(Boolean(s.launch_at_login))
      // Prefer shell desktop.json for tray prefs (authoritative at launch).
      setStartInTray(Boolean(s.start_in_tray))
      setCloseToTray(Boolean(s.close_to_tray))
      setBrowserHomeUrl(
        (s.browser_home_url || '').trim() || 'https://github.com/AhmiDarrow/RemedyAI',
      )
      setHarnessMode(s.harness_mode || 'auto')
      {
        const mn = Number(s.harness_min_context_pct)
        setHarnessMinPct(Number.isFinite(mn) ? Math.min(0.95, Math.max(0.05, mn)) : 0.75)
        const mx = Number(s.harness_max_context_pct)
        setHarnessMaxPct(Number.isFinite(mx) ? Math.min(0.99, Math.max(0.1, mx)) : 0.92)
      }
      {
        const tl = String(s.thinking_level || 'high').toLowerCase()
        setThinkingLevel(
          tl === 'off' || tl === 'low' || tl === 'medium' || tl === 'high' ? tl : 'high',
        )
      }
      setAllowSkillCreation(s.allow_skill_creation !== false)
      {
        const th = Number(s.auto_approve_threshold)
        setAutoApproveThreshold(Number.isFinite(th) ? Math.min(1, Math.max(0, th)) : 0.8)
      }
      setLogLevel(String(s.log_level || 'INFO').toUpperCase())
      setSarcasmMode(Boolean(s.sarcasm_mode))
      setWebToolsEnabled(Boolean(s.web_tools_enabled))
      setHttpBootstrap(s.http_bootstrap !== false)
      {
        const am = String(s.approval_mode || 'ask').toLowerCase()
        setApprovalMode(am === 'auto' ? 'auto' : 'ask')
      }
      {
        const tp = normalizeToolProcess(s.tool_process)
        setToolProcess(tp)
        onToolProcessChange?.(tp)
      }
      setApiKey('')
      const isAdvanced = providers.some((p) => p.id === prov && p.advanced)
      if (isAdvanced) setShowAdvanced(true)
      if (prov === 'xai' || s.xai_auth) {
        try {
          const xa = s.xai_auth
            ? (s.xai_auth as XaiAuthStatus)
            : await getXaiAuthStatus()
          setXaiAuth(xa)
          if (xa.connected) setApiKeySet(true)
        } catch {
          setXaiAuth(null)
        }
      } else {
        setXaiAuth(null)
      }
      // Show the form immediately after settings — secondary work in parallel.
      setLoading(false)
      console.debug(
        `[remedy:settings] core loaded in ${Math.round(performance.now() - t0)}ms`,
      )

      void Promise.allSettled([
        (async () => {
          try {
            const prefs = await invoke<{
              skip_quit_server_warning?: boolean
              start_in_tray?: boolean
              close_to_tray?: boolean
            }>('get_desktop_prefs')
            setSkipQuitWarn(Boolean(prefs?.skip_quit_server_warning))
            // Shell prefs win for visibility toggles (match what launch uses).
            if (typeof prefs?.start_in_tray === 'boolean') {
              setStartInTray(prefs.start_in_tray)
            }
            if (typeof prefs?.close_to_tray === 'boolean') {
              setCloseToTray(prefs.close_to_tray)
            }
          } catch {
            try {
              setSkipQuitWarn(localStorage.getItem('remedy.skipQuitServerWarning') === '1')
            } catch {
              setSkipQuitWarn(false)
            }
          }
        })(),
        (async () => {
          try {
            const osLogin = await invoke<boolean>('get_launch_at_login')
            setLaunchAtLogin(Boolean(osLogin || s.launch_at_login))
          } catch {
            /* browser / missing permission */
          }
        })(),
      ]).then(() => {
        console.debug(
          `[remedy:settings] secondary loaded in ${Math.round(performance.now() - t0)}ms`,
        )
      })
    } catch (e) {
      console.warn('[remedy:settings] load failed', e)
      // server not ready
      setLoading(false)
    }
    // Intentionally stable: one load function; parent callbacks read from latest render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (open) {
      load()
      setSaved(false)
      setErrorMessage('')
      setXaiLoginMsg('')
      setXaiUserCode('')
      setXaiVerifyUrl('')
      // Deep-link / remember last section
      const last = loadLastSettingsSection()
      if (last) setForceSection(last)
    } else {
      stopXaiPoll()
      setXaiLoginBusy(false)
      setVisionSectionOpen(false)
      setSettingsSearch('')
    }
    return () => stopXaiPoll()
  }, [open, load, stopXaiPoll])

  // Lazy-load vision status only when Local vision is expanded (faster Settings open).
  useEffect(() => {
    if (!open || !visionSectionOpen) return
    void refreshVision()
  }, [open, visionSectionOpen, refreshVision])

  const matchSec = useCallback(
    (id: SettingsSectionId) => {
      const meta = SETTINGS_SECTION_META[id]
      return sectionMatchesSearch(
        settingsSearch,
        meta.title,
        meta.summary,
        meta.keywords,
      )
    },
    [settingsSearch],
  )

  const sectionProps = useCallback(
    (id: SettingsSectionId) => ({
      id,
      title: SETTINGS_SECTION_META[id].title,
      summary: SETTINGS_SECTION_META[id].summary,
      keywords: SETTINGS_SECTION_META[id].keywords,
      forceOpen: forceSection === id || (settingsSearch.trim().length > 0 && matchSec(id)),
      hidden: settingsSearch.trim().length > 0 && !matchSec(id),
      onOpenChange: (isOpen: boolean) => {
        if (isOpen) {
          setForceSection(id)
          saveLastSettingsSection(id)
          if (id === 'vision') setVisionSectionOpen(true)
        }
      },
    }),
    [forceSection, matchSec, settingsSearch],
  )

  const handleXaiSignIn = async () => {
    setXaiLoginBusy(true)
    setXaiLoginMsg('')
    setErrorMessage('')
    stopXaiPoll()
    try {
      // Same first-run/wipe bootstrap as SetupWizard — avoid 401 / dead-server races.
      try {
        const {
          clearApiToken,
          ensureApiToken,
          waitForLocalApi,
        } = await import('../api/client')
        const up = await waitForLocalApi(15000)
        if (!up) {
          throw new Error(
            'Local Remedy server is not responding. Retry from the tray or restart the app.',
          )
        }
        clearApiToken()
        await ensureApiToken()
      } catch (pre: unknown) {
        if (pre instanceof Error && pre.message.includes('not responding')) {
          throw pre
        }
        /* apiFetch will retry token */
      }
      const start = await startXaiLogin()
      setXaiUserCode(start.user_code)
      setXaiVerifyUrl(start.verification_uri_complete || start.verification_uri)
      setXaiLoginMsg(start.message || `Enter code ${start.user_code} at xAI`)
      void openExternalUrl(start.verification_uri_complete || start.verification_uri)
      const sessionId = start.session_id
      const intervalMs = Math.max(3, start.interval || 5) * 1000
      xaiPollRef.current = setInterval(async () => {
        try {
          const poll = await pollXaiLogin(sessionId)
          const st = poll.session?.status
          if (st === 'connected') {
            stopXaiPoll()
            setXaiLoginBusy(false)
            setXaiAuth(poll.credentials)
            setApiKeySet(true)
            setXaiLoginMsg('Signed in with xAI')
            setXaiUserCode('')
            // Ensure provider is xAI after OAuth — persist so chat switches now.
            const nextModel = model.startsWith('grok') ? model : 'grok-3-mini'
            setProvider('xai')
            setBaseUrl('https://api.x.ai/v1')
            setModel(nextModel)
            try {
              await updateSettings({
                llm_provider: 'xai',
                llm_model: nextModel,
                llm_base_url: 'https://api.x.ai/v1',
              })
            } catch (err) {
              console.warn('persist xAI after OAuth:', err)
            }
            onSettingsSaved?.()
          } else if (st === 'error') {
            stopXaiPoll()
            setXaiLoginBusy(false)
            setXaiLoginMsg(poll.session?.error || 'Sign-in failed or expired')
          }
        } catch {
          // keep polling
        }
      }, intervalMs)
    } catch (e: unknown) {
      setXaiLoginBusy(false)
      const msg = e instanceof Error ? e.message : String(e)
      setErrorMessage(msg || 'Could not start xAI sign-in')
    }
  }

  const handleXaiLogout = async () => {
    try {
      await logoutXai()
      setXaiAuth(null)
      setApiKeySet(false)
      setXaiLoginMsg('Signed out of xAI')
      onSettingsSaved?.()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setErrorMessage(msg || 'Logout failed')
    }
  }

  const handleBrowseProject = async () => {
    setErrorMessage('')
    const path = await pickProjectFolder()
    if (path) {
      setProjectPath(path)
      setStatusMessage('Folder selected — click Save to load project and reload Remedy')
    }
  }

  const handleSave = async () => {
    setSaving(true)
    setSaved(false)
    setErrorMessage('')
    setStatusMessage('')
    const prevProject = (settings?.project_path || '').trim()
    const updates: SettingsUpdate = {
      llm_provider: provider,
      llm_model: model,
      llm_base_url: baseUrl,
      project_path: projectPath,
      browser_home_url: browserHomeUrl.trim() || 'https://github.com/AhmiDarrow/RemedyAI',
      persona,
      user_name: userName.trim(),
      name: agentName.trim() || 'Remedy',
      access_scope: accessScope,
      launch_at_login: launchAtLogin,
      start_in_tray: startInTray,
      close_to_tray: closeToTray,
      harness_mode: harnessMode,
      harness_min_context_pct: harnessMinPct,
      harness_max_context_pct: Math.max(harnessMaxPct, harnessMinPct + 0.01),
      thinking_level: thinkingLevel,
      tool_process: toolProcess,
      skills_active_budget: skillsBudget,
      web_tools_enabled: webToolsEnabled,
      http_bootstrap: httpBootstrap,
      approval_mode: approvalMode,
      allow_skill_creation: allowSkillCreation,
      auto_approve_threshold: autoApproveThreshold,
      log_level: logLevel,
      sarcasm_mode: sarcasmMode,
    }
    if (enabledProviders !== null) {
      updates.enabled_providers = enabledProviders
    }
    if (Object.keys(enabledModels).length > 0) {
      updates.enabled_models = enabledModels
    }
    if (apiKey) {
      updates.llm_api_key = apiKey
    }
    try {
      try {
        await invoke('set_launch_at_login', { enabled: launchAtLogin })
      } catch (e) {
        console.warn('launch_at_login OS sync:', e)
      }
      try {
        await invoke('set_desktop_prefs', {
          close_to_tray: closeToTray,
          start_in_tray: startInTray,
          skip_quit_server_warning: skipQuitWarn,
        })
        try {
          if (skipQuitWarn) localStorage.setItem('remedy.skipQuitServerWarning', '1')
          else localStorage.removeItem('remedy.skipQuitServerWarning')
        } catch {
          /* */
        }
      } catch (e) {
        console.warn('desktop prefs OS sync:', e)
      }
      const result = await updateSettings(updates)
      // Server may normalize provider/model/url — reflect that immediately.
      if (result && typeof result === 'object') {
        const r = result as SettingsUpdate & {
          llm_provider?: string
          llm_model?: string
          llm_base_url?: string
          project_path?: string
          persona?: string
        }
        if (r.llm_provider) setProvider(r.llm_provider)
        if (r.llm_model) setModel(r.llm_model)
        if (r.llm_base_url) setBaseUrl(r.llm_base_url)
        if (r.project_path !== undefined) setProjectPath(r.project_path || projectPath)
        if (r.persona) setPersona(r.persona)
      }
      setSaved(true)
      setApiKey('')
      setApiKeySet(apiKey ? true : apiKeySet)
      const projectChanged =
        (projectPath || '').trim() !== prevProject
          && (projectPath || '').trim() !== ''
      setStatusMessage(
        projectChanged
          ? 'Settings saved · Project loaded · Remedy reloaded'
          : 'Settings saved · Remedy reloaded',
      )
      await load()
      onSettingsSaved?.()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setSaved(false)
      setErrorMessage(msg || 'Failed to save settings')
      console.warn('Settings save failed:', msg)
    } finally {
      setSaving(false)
    }
  }

  const handleProviderChange = (p: string) => {
    setProvider(p)
    const preset = catalog.find((x) => x.id === p)
    if (preset) {
      setBaseUrl(preset.base_url)
      setModel(preset.default_model)
    }
  }

  if (!open) return null

  return (
    <div
      className={`flex flex-col h-full min-h-0 ${embedded ? '' : 'border-l'}`}
      style={{
        width: embedded ? '100%' : 300,
        minWidth: embedded ? 0 : 300,
        maxWidth: embedded ? '100%' : 300,
        background: 'var(--bg-secondary)',
        borderColor: 'var(--border)',
      }}
    >
      {/* Workspace slide already has a "Settings" header when embedded */}
      {!embedded && (
        <div
          className="flex items-center justify-between px-3 py-2 border-b text-xs font-medium shrink-0"
          style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
        >
          <span>Settings</span>
          <button
            onClick={onClose}
            className="px-1 rounded"
            style={{ color: 'var(--text-muted)' }}
          >
            {'\u00D7'}
          </button>
        </div>
      )}

      <div className="px-3 pt-2 pb-1 border-b shrink-0" style={{ borderColor: 'var(--border)' }}>
        <input
          type="search"
          value={settingsSearch}
          onChange={(e) => setSettingsSearch(e.target.value)}
          placeholder="Search settings…"
          className="w-full rounded px-2 py-1 text-xs outline-none"
          style={{
            background: 'var(--bg-tertiary)',
            color: 'var(--text-primary)',
            border: '1px solid var(--border)',
          }}
          aria-label="Search settings"
        />
      </div>

      <div className="flex-1 overflow-y-auto p-3 text-xs space-y-4">
        {loading ? (
          <div style={{ color: 'var(--text-muted)' }}>Loading...</div>
        ) : (
            <SettingsFormSections
              sectionProps={sectionProps}
              provider={provider}
              setProvider={setProvider}
              model={model}
              setModel={setModel}
              baseUrl={baseUrl}
              setBaseUrl={setBaseUrl}
              apiKey={apiKey}
              setApiKey={setApiKey}
              apiKeySet={apiKeySet}
              projectPath={projectPath}
              setProjectPath={setProjectPath}
              browserHomeUrl={browserHomeUrl}
              setBrowserHomeUrl={setBrowserHomeUrl}
              persona={persona}
              setPersona={setPersona}
              userName={userName}
              setUserName={setUserName}
              agentName={agentName}
              setAgentName={setAgentName}
              accessScope={accessScope}
              setAccessScope={setAccessScope}
              launchAtLogin={launchAtLogin}
              setLaunchAtLogin={setLaunchAtLogin}
              startInTray={startInTray}
              setStartInTray={setStartInTray}
              closeToTray={closeToTray}
              setCloseToTray={setCloseToTray}
              skipQuitWarn={skipQuitWarn}
              setSkipQuitWarn={setSkipQuitWarn}
              webToolsEnabled={webToolsEnabled}
              setWebToolsEnabled={setWebToolsEnabled}
              httpBootstrap={httpBootstrap}
              setHttpBootstrap={setHttpBootstrap}
              approvalMode={approvalMode}
              setApprovalMode={setApprovalMode}
              harnessMode={harnessMode}
              setHarnessMode={setHarnessMode}
              harnessMinPct={harnessMinPct}
              setHarnessMinPct={setHarnessMinPct}
              harnessMaxPct={harnessMaxPct}
              setHarnessMaxPct={setHarnessMaxPct}
              thinkingLevel={thinkingLevel}
              setThinkingLevel={setThinkingLevel}
              allowSkillCreation={allowSkillCreation}
              setAllowSkillCreation={setAllowSkillCreation}
              autoApproveThreshold={autoApproveThreshold}
              setAutoApproveThreshold={setAutoApproveThreshold}
              logLevel={logLevel}
              setLogLevel={setLogLevel}
              sarcasmMode={sarcasmMode}
              setSarcasmMode={setSarcasmMode}
              toolProcess={toolProcess}
              setToolProcess={setToolProcess}
              onToolProcessChange={onToolProcessChange}
              catalog={catalog}
              showAdvanced={showAdvanced}
              setShowAdvanced={setShowAdvanced}
              xaiAuth={xaiAuth}
              xaiLoginBusy={xaiLoginBusy}
              xaiUserCode={xaiUserCode}
              xaiVerifyUrl={xaiVerifyUrl}
              xaiLoginMsg={xaiLoginMsg}
              handleXaiSignIn={() => { void handleXaiSignIn() }}
              handleXaiLogout={() => { void handleXaiLogout() }}
              vision={vision}
              swarm={swarm}
              visionBusy={visionBusy}
              setVisionBusy={setVisionBusy}
              visionMsg={visionMsg}
              setVisionMsg={setVisionMsg}
              refreshVision={refreshVision}
              startVisionInstallPoll={startVisionInstallPoll}
              connectedList={connectedList}
              providerSearch={providerSearch}
              setProviderSearch={setProviderSearch}
              enabledProviders={enabledProviders}
              setEnabledProviders={setEnabledProviders}
              enabledModels={enabledModels}
              setEnabledModels={setEnabledModels}
              catalogExpand={catalogExpand}
              setCatalogExpand={setCatalogExpand}
              skillsBudget={skillsBudget}
              setSkillsBudget={setSkillsBudget}
              primaryProviders={primaryProviders}
              advancedProviders={advancedProviders}
              activeMeta={activeMeta ?? FALLBACK_PROVIDERS[0]}
              showBaseUrl={showBaseUrl}
              providerModels={providerModels}
              handleProviderChange={handleProviderChange}
              handleBrowseProject={() => { void handleBrowseProject() }}
              themeId={themeId}
              onThemeChange={onThemeChange}
              density={density}
              onDensityChange={onDensityChange}
              customAccent={customAccent}
              onCustomAccentChange={onCustomAccentChange}
              updateInfo={updateInfo}
              checkingUpdates={checkingUpdates}
              updateStatus={updateStatus}
              onCheckUpdates={onCheckUpdates}
              onInstallUpdate={onInstallUpdate}
              models={models}
              onOpenHelp={onOpenHelp}
              settings={settings}
            />
        )}
      </div>

      {/* Save */}
      <div
        className="px-3 py-2 border-t flex flex-col gap-2"
        style={{ borderColor: 'var(--border)' }}
      >
        {errorMessage && (
          <div
            className="px-2 py-1.5 rounded text-xs"
            style={{
              background: 'var(--error-bg, rgba(239,68,68,0.1))',
              color: 'var(--error)',
              border: '1px solid var(--error)',
            }}
          >
            {errorMessage}
          </div>
        )}
        {statusMessage && !errorMessage && (
          <div
            className="px-2 py-1.5 rounded text-xs"
            style={{
              background: 'var(--bg-tertiary)',
              color: 'var(--success)',
              border: '1px solid var(--border)',
            }}
          >
            {statusMessage}
          </div>
        )}
        <div className="flex items-center gap-2">
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex-1 py-1.5 rounded text-xs font-medium transition-colors"
            style={{
              background: saving ? 'var(--bg-tertiary)' : 'var(--accent)',
              color: saving ? 'var(--text-muted)' : '#fff',
            }}
          >
            {saving ? 'Saving & reloading…' : 'Save Settings'}
          </button>
          {saved && !errorMessage && !statusMessage && (
            <span className="text-xs" style={{ color: 'var(--success)' }}>
              Saved
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

