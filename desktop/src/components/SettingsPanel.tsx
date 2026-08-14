import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { invoke } from '@tauri-apps/api/core'
import {
  getSettings,
  updateSettings,
  type MessengerInfo,
  type Settings,
  type SettingsUpdate,
} from '../api/settings'
import {
  getVisionStatus,
  type VisionStatus,
} from '../api/vision'
import { getRmbStatus, type RmbStatus } from '../api/rmb'
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
import type { Density, FontScale } from '../utils/chatPrefs'
import {
  normalizeToolProcess,

  type ToolProcessMode,
} from '../utils/toolLabels'
import { demoModelOptions } from '../utils/demoModels'
import { PERSONAS, pickProjectFolder } from './settings/shared'
import { SettingsFormSections } from './settings/FormSections'
import {
  draftsFromMessengers,
  messengersUpdateFromDrafts,
  type MessengerDraftMap,
} from '../utils/messengerDrafts'
import type { AssistantDraft } from './settings/AssistantSection'
import { useSettingsPanelState } from '../hooks/useSettingsPanelState'

interface SettingsPanelProps {
  open: boolean
  onClose: () => void
  themeId: ThemeId
  onThemeChange: (id: ThemeId) => void
  density?: Density
  onDensityChange?: (d: Density) => void
  customAccent?: string
  onCustomAccentChange?: (hex: string) => void
  fontScale?: FontScale
  onFontScaleChange?: (s: FontScale) => void
  reduceMotion?: boolean
  onReduceMotionChange?: (on: boolean) => void
  highContrast?: boolean
  onHighContrastChange?: (on: boolean) => void
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
  fontScale = 'md',
  onFontScaleChange,
  reduceMotion = false,
  onReduceMotionChange,
  highContrast = false,
  onHighContrastChange,
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
  const [saveToast, setSaveToast] = useState<{ kind: 'ok' | 'err'; text: string } | null>(
    null,
  )
  const scrollBodyRef = useRef<HTMLDivElement>(null)
  /** Monotonic load id — ignore stale GET responses after re-open/save. */
  const loadGenRef = useRef(0)
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
  const [privacyShield, setPrivacyShield] = useState<{
    enabled: boolean
    ready: boolean
    message: string
    attribution: string
  } | null>(null)
  const [privacyShieldBusy, setPrivacyShieldBusy] = useState(false)
  const [persona, setPersona] = useState('balanced')
  const [userName, setUserName] = useState('')
  const [agentName, setAgentName] = useState('Remedy')
  const [agentGender, setAgentGender] = useState('female')
  const [accessScope, setAccessScope] = useState('project')
  const [launchAtLogin, setLaunchAtLogin] = useState(false)
  const [startInTray, setStartInTray] = useState(false)
  const [skipQuitWarn, setSkipQuitWarn] = useState(false)
  const [webToolsEnabled, setWebToolsEnabled] = useState(false)
  // Desktop default is off (IPC-only); GET /settings overwrites with effective value.
  const [httpBootstrap, setHttpBootstrap] = useState(false)
  const [privacyMode, setPrivacyMode] = useState(false)
  /** Route cloud LLM traffic through local Sleev gateway (token compression). */
  const [sleevEnabled, setSleevEnabled] = useState(false)
  const [sleevGatewayUrl, setSleevGatewayUrl] = useState('')
  const [sleevAllowRemoteGateway, setSleevAllowRemoteGateway] = useState(false)
  const [sleevStatus, setSleevStatus] = useState<Settings['sleev'] | null>(null)
  /** Soul Field personhood — default on (matches server maturity default). */
  const [soulFieldEnabled, setSoulFieldEnabled] = useState(true)
  const [approvalMode, setApprovalMode] = useState<'ask' | 'auto'>('ask')
  const [harnessMode, setHarnessMode] = useState('auto')
  const [harnessMinPct, setHarnessMinPct] = useState(0.75)
  const [harnessMaxPct, setHarnessMaxPct] = useState(0.92)
  const [thinkingLevel, setThinkingLevel] = useState<'off' | 'low' | 'medium' | 'high'>('high')
  const [allowSkillCreation, setAllowSkillCreation] = useState(true)
  const [autoApproveThreshold, setAutoApproveThreshold] = useState(0.8)
  const [logLevel, setLogLevel] = useState('INFO')
  const [sarcasmMode, setSarcasmMode] = useState(false)
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

  const [visionBusy, setVisionBusy] = useState(false)
  const [visionMsg, setVisionMsg] = useState('')
  const visionPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [rmb, setRmb] = useState<RmbStatus | null>(null)
  const [rmbBusy, setRmbBusy] = useState(false)
  const [rmbMsg, setRmbMsg] = useState('')
  const [connectedList, setConnectedList] = useState<ConnectedProvider[]>([])
  const [providerSearch, setProviderSearch] = useState('')
  const [enabledProviders, setEnabledProviders] = useState<string[] | null>(null)
  const [enabledModels, setEnabledModels] = useState<Record<string, string[]>>({})
  const [catalogExpand, setCatalogExpand] = useState<string | null>(null)
  const [skillsBudget, setSkillsBudget] = useState(80)
  const [messengers, setMessengers] = useState<MessengerInfo[]>([])
  const [messengerDrafts, setMessengerDrafts] = useState<MessengerDraftMap>({})
  const [assistantDraft, setAssistantDraft] = useState<AssistantDraft>({})
  const {
    settingsSearch,
    setSettingsSearch,
    visionSectionOpen,
    rmbSectionOpen,
    settingsMode,
    setSettingsMode,
    sectionProps,
    onPanelOpenChange,
  } = useSettingsPanelState()
  const [customName, setCustomName] = useState('')

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
  // Demo: ALWAYS full curated set — never partial/live llm7 dumps.
  const providerModels = useMemo(() => {
    if (provider === 'demo') {
      return demoModelOptions(activeMeta?.models || []).map((m) => ({
        ...m,
        provider: 'demo' as const,
      }))
    }
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

  // Snap invalid Demo selection once (seedance/deepseek junk) — never fight user picks.
  useEffect(() => {
    if (provider !== 'demo') return
    if (!model) {
      setModel(providerModels[0]?.id || 'codestral-latest')
      return
    }
    const allowed = new Set(providerModels.map((m) => m.id))
    if (!allowed.has(model)) {
      setModel(providerModels[0]?.id || 'codestral-latest')
    }
    // Intentionally only re-run when provider changes or model becomes invalid —
    // not when providerModels array identity changes every parent render.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- stable snap, avoid stutter loops
  }, [provider, model])

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

  const refreshRmb = useCallback(async () => {
    try {
      const st = await getRmbStatus()
      setRmb(st)
      return st
    } catch {
      setRmb(null)
      return null
    }
  }, [])

  const refreshVision = useCallback(async () => {
    try {
      const vs = await getVisionStatus({ full: true })
      setVision(vs)
      return vs
    } catch {
      return null
    }
  }, [])

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
            setVisionMsg('Visual decoder ready — SmolVLM2 2.2B')
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
    const gen = ++loadGenRef.current
    setLoading(true)
    const t0 = performance.now()
    try {
      // Critical path first — do not wait on vision (was multi-second freezes).
      const [s, providers, connected] = await Promise.all([
        getSettings(),
        listProviders(),
        listConnectedProviders().catch(() => null),
      ])
      if (gen !== loadGenRef.current) return
      setCatalog(providers)
      if (connected?.providers) setConnectedList(connected.providers)
      if (s.enabled_providers !== undefined) {
        setEnabledProviders(s.enabled_providers)
      }
      // Always apply (including {}) so clearing the allowlist on the server
      // is visible after reload / multi-client saves.
      setEnabledModels(
        s.enabled_models && typeof s.enabled_models === 'object'
          ? (s.enabled_models as Record<string, string[]>)
          : {},
      )
      if (s.skills_active_budget) setSkillsBudget(s.skills_active_budget)
      setSettings(s)
      const prov = s.llm_provider || 'openai'
      setProvider(prov)
      setModel(s.llm_model || 'gpt-4o-mini')
      setBaseUrl(s.llm_base_url || 'https://api.openai.com/v1')
      setCustomName((s.custom_llm_name || '').trim())
      setApiKeySet(s.llm_api_key_set)
      setProjectPath(s.project_path || '.')
      const p = (s.persona || 'balanced').toLowerCase()
      setPersona(
        PERSONAS.some((x) => x.id === p) ? p : p === 'default' ? 'balanced' : 'balanced',
      )
      setUserName((s.user_name || '').trim())
      setAgentName(s.name || 'Remedy')
      {
        const g = (s.agent_gender || 'female').toLowerCase()
        setAgentGender(
          g === 'male' || g === 'neutral' || g === 'female' ? g : 'female',
        )
      }
      setAccessScope(s.access_scope || 'project')
      setLaunchAtLogin(Boolean(s.launch_at_login))
      // Prefer shell desktop.json for tray prefs (authoritative at launch).
      setStartInTray(Boolean(s.start_in_tray))
      setBrowserHomeUrl(
        (s.browser_home_url || '').trim() || 'https://github.com/AhmiDarrow/RemedyAI',
      )
      // Desktop-only Privacy Shield (not available in plain web UI)
      try {
        const ps = await invoke<{
          enabled: boolean
          ready: boolean
          message: string
          attribution: string
        }>('privacy_shield_status')
        setPrivacyShield({
          enabled: Boolean(ps.enabled),
          ready: Boolean(ps.ready),
          message: ps.message || '',
          attribution: ps.attribution || '',
        })
      } catch {
        setPrivacyShield(null)
      }
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
      setPrivacyMode(Boolean(s.privacy_mode))
      setSleevEnabled(Boolean(s.sleev_enabled))
      setSleevGatewayUrl(String(s.sleev_gateway_url || ''))
      setSleevAllowRemoteGateway(Boolean(s.sleev_allow_remote_gateway))
      setSleevStatus(s.sleev ?? null)
      setSoulFieldEnabled(
        s.soul_field_enabled === undefined ? true : Boolean(s.soul_field_enabled),
      )
      {
        const list = Array.isArray(s.messengers) ? s.messengers : []
        setMessengers(list)
        setMessengerDrafts(draftsFromMessengers(list))
      }
      // Reset draft so checkboxes reflect server status after load/save.
      setAssistantDraft({})
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
      // Merge shell prefs before unlocking the form so a fast Save cannot
      // persist pre-secondary tray/login values.
      console.debug(
        `[remedy:settings] core loaded in ${Math.round(performance.now() - t0)}ms`,
      )
      await Promise.allSettled([
        (async () => {
          try {
            const prefs = await invoke<{
              skip_quit_server_warning?: boolean
              start_in_tray?: boolean
              close_to_tray?: boolean
            }>('get_desktop_prefs')
            if (gen !== loadGenRef.current) return
            setSkipQuitWarn(Boolean(prefs?.skip_quit_server_warning))
            // Shell prefs win for visibility toggles (match what launch uses).
            if (typeof prefs?.start_in_tray === 'boolean') {
              setStartInTray(prefs.start_in_tray)
            }
            // close_to_tray is product-forced true on save; ignore shell opt-out.
          } catch {
            if (gen !== loadGenRef.current) return
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
            if (gen !== loadGenRef.current) return
            setLaunchAtLogin(Boolean(osLogin || s.launch_at_login))
          } catch {
            /* browser / missing permission */
          }
        })(),
      ])
      if (gen !== loadGenRef.current) return
      console.debug(
        `[remedy:settings] secondary loaded in ${Math.round(performance.now() - t0)}ms`,
      )
      setLoading(false)
    } catch (e) {
      console.warn('[remedy:settings] load failed', e)
      // server not ready
      if (gen === loadGenRef.current) setLoading(false)
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
      onPanelOpenChange(true)
    } else {
      stopXaiPoll()
      setXaiLoginBusy(false)
      onPanelOpenChange(false)
    }
    return () => stopXaiPoll()
  }, [open, load, stopXaiPoll, onPanelOpenChange])

  // Lazy-load vision status only when Local vision is expanded (faster Settings open).
  useEffect(() => {
    if (!open || !visionSectionOpen) return
    void refreshVision()
  }, [open, visionSectionOpen, refreshVision])

  // Lazy-load RMB when section expanded (or always in simple mode when panel open briefly)
  useEffect(() => {
    if (!open || !rmbSectionOpen) return
    void refreshRmb()
  }, [open, rmbSectionOpen, refreshRmb])

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
    if (loading || !settings || saving) return
    setSaving(true)
    setSaved(false)
    setErrorMessage('')
    setStatusMessage('')
    const prevProject = (settings?.project_path || '').trim()
    const updates: SettingsUpdate = {
      llm_provider: provider,
      llm_model: model,
      llm_base_url: baseUrl,
      custom_llm_name: provider === 'custom' ? customName.trim() : undefined,
      project_path: projectPath,
      browser_home_url: browserHomeUrl.trim() || 'https://github.com/AhmiDarrow/RemedyAI',
      persona,
      user_name: userName.trim(),
      name: agentName.trim() || 'Remedy',
      agent_gender: agentGender || 'female',
      access_scope: accessScope,
      launch_at_login: launchAtLogin,
      start_in_tray: startInTray,
      // Title-bar ✕ always hides to tray (product rule).
      close_to_tray: true,
      harness_mode: harnessMode,
      harness_min_context_pct: harnessMinPct,
      harness_max_context_pct: Math.max(harnessMaxPct, harnessMinPct + 0.01),
      thinking_level: thinkingLevel,
      tool_process: toolProcess,
      skills_active_budget: skillsBudget,
      web_tools_enabled: webToolsEnabled,
      http_bootstrap: httpBootstrap,
      privacy_mode: privacyMode,
      sleev_enabled: sleevEnabled,
      sleev_gateway_url: sleevGatewayUrl.trim(),
      sleev_allow_remote_gateway: sleevAllowRemoteGateway,
      soul_field_enabled: soulFieldEnabled,
      approval_mode: approvalMode,
      allow_skill_creation: allowSkillCreation,
      auto_approve_threshold: autoApproveThreshold,
      log_level: logLevel,
      sarcasm_mode: sarcasmMode,
    }
    if (enabledProviders !== null) {
      // Demo must stay available for zero-setup; never save a list that drops it.
      const ep = enabledProviders.includes('demo')
        ? enabledProviders
        : ['demo', ...enabledProviders]
      updates.enabled_providers = ep
    }
    // Always send — empty object clears a prior per-provider allowlist so
    // re-enabling all models actually persists (not only when non-empty).
    updates.enabled_models = enabledModels
    if (apiKey) {
      updates.llm_api_key = apiKey
    }
    const msgBody = messengersUpdateFromDrafts(messengers, messengerDrafts)
    if (msgBody) updates.messengers = msgBody
    if (Object.keys(assistantDraft).length > 0) {
      updates.assistant = { ...assistantDraft }
    }
    try {
      try {
        await invoke('set_launch_at_login', { enabled: launchAtLogin })
      } catch (e) {
        console.warn('launch_at_login OS sync:', e)
      }
      try {
        await invoke('set_desktop_prefs', {
          close_to_tray: true,
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
      const okMsg = projectChanged
        ? 'Settings saved · Project loaded · Remedy reloaded'
        : 'Settings saved · Remedy reloaded'
      setStatusMessage(okMsg)
      setSaveToast({ kind: 'ok', text: okMsg })
      window.setTimeout(() => setSaveToast(null), 3200)
      await load()
      onSettingsSaved?.()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setSaved(false)
      setErrorMessage(msg || 'Failed to save settings')
      setSaveToast({ kind: 'err', text: msg || 'Failed to save settings' })
      window.setTimeout(() => setSaveToast(null), 4200)
      console.warn('Settings save failed:', msg)
    } finally {
      setSaving(false)
    }
  }

  const handleProviderChange = (p: string) => {
    const prev = provider
    setProvider(p)
    const preset = catalog.find((x) => x.id === p)
    if (preset) {
      const prevPreset = catalog.find((x) => x.id === prev)
      // Adopt the preset URL only when the current value is untouched (empty or
      // still the previous provider's default). Never clobber a base URL the
      // user typed — switching providers must not reset a custom base URL.
      const baseIsUntouched =
        !baseUrl || (prevPreset ? baseUrl === prevPreset.base_url : true)
      if (baseIsUntouched) setBaseUrl(preset.base_url)
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
        background: 'color-mix(in srgb, var(--bg-secondary) 96%, var(--bg-primary))',
        borderColor: 'color-mix(in srgb, var(--border) 85%, transparent)',
      }}
    >
      {/* Workspace slide already has a "Settings" header when embedded */}
      {!embedded && (
        <div
          className="flex items-center justify-between px-3 py-2.5 border-b text-xs font-semibold shrink-0 tracking-tight"
          style={{
            borderColor: 'color-mix(in srgb, var(--border) 80%, transparent)',
            color: 'var(--text-primary)',
          }}
        >
          <span>Settings</span>
          <button
            type="button"
            onClick={onClose}
            className="ui-btn ui-btn-ghost"
            style={{ padding: '0.15rem 0.4rem' }}
            aria-label="Close settings"
          >
            {'\u00D7'}
          </button>
        </div>
      )}

      <div
        className="px-3 pt-2.5 pb-1.5 border-b shrink-0 space-y-1.5"
        style={{ borderColor: 'color-mix(in srgb, var(--border) 80%, transparent)' }}
      >
        <div className="flex items-center gap-1" title="How many settings sections are listed">
          {(['simple', 'advanced'] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => {
                setSettingsMode(m)
                                if (m === 'advanced') setShowAdvanced(true)
              }}
              className="flex-1 rounded-lg px-2 py-1 text-[10px] font-semibold capitalize"
              style={{
                background:
                  settingsMode === m
                    ? 'color-mix(in srgb, var(--accent) 16%, transparent)'
                    : 'color-mix(in srgb, var(--bg-tertiary) 80%, transparent)',
                color: settingsMode === m ? 'var(--accent)' : 'var(--text-muted)',
                border: `1px solid ${
                  settingsMode === m
                    ? 'color-mix(in srgb, var(--accent) 45%, var(--border))'
                    : 'color-mix(in srgb, var(--border) 85%, transparent)'
                }`,
              }}
            >
              {m === 'simple' ? 'Simple settings' : 'Advanced settings'}
            </button>
          ))}
        </div>
        <input
          type="search"
          value={settingsSearch}
          onChange={(e) => {
            const q = e.target.value
            setSettingsSearch(q)
            // Jump to first matching section after expand paints.
            if (q.trim()) {
              window.requestAnimationFrame(() => {
                const root = scrollBodyRef.current
                const hit = root?.querySelector<HTMLElement>(
                  '[data-section]:not([hidden])',
                )
                // Prefer visible sections that aren't mode-hidden
                const all = root?.querySelectorAll<HTMLElement>('[data-section]')
                if (all) {
                  for (const el of all) {
                    if (el.offsetParent !== null) {
                      el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
                      break
                    }
                  }
                } else {
                  hit?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
                }
              })
            }
          }}
          placeholder="Search settings…"
          className="ui-input"
          aria-label="Search settings"
        />
        {settingsMode === 'simple' ? (
          <div className="text-[10px] leading-snug" style={{ color: 'var(--text-muted)' }}>
            Simple shows essentials. Switch to <strong>Advanced</strong> for messengers,
            vision, power tools, and logs.
          </div>
        ) : (
          <div className="text-[10px] leading-snug" style={{ color: 'var(--text-muted)' }}>
            Advanced lists every section. Use search to jump.
          </div>
        )}
      </div>

      <div ref={scrollBodyRef} className="flex-1 overflow-y-auto p-3 text-xs space-y-4">
        {loading ? (
          <div style={{ color: 'var(--text-muted)' }}>Loading…</div>
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
              privacyShield={
                privacyShield
                  ? {
                      enabled: privacyShield.enabled,
                      ready: privacyShield.ready,
                      message: privacyShield.message,
                      attribution: privacyShield.attribution,
                      busy: privacyShieldBusy,
                      onToggle: (on) => {
                        setPrivacyShieldBusy(true)
                        void invoke<{
                          enabled: boolean
                          ready: boolean
                          message: string
                          attribution: string
                        }>('privacy_shield_set_enabled', { enabled: on })
                          .then((ps) => {
                            setPrivacyShield({
                              enabled: Boolean(ps.enabled),
                              ready: Boolean(ps.ready),
                              message: ps.message || '',
                              attribution: ps.attribution || '',
                            })
                          })
                          .catch((e) => {
                            setPrivacyShield((prev) =>
                              prev
                                ? {
                                    ...prev,
                                    message:
                                      e instanceof Error
                                        ? e.message
                                        : 'Privacy Shield update failed',
                                  }
                                : prev,
                            )
                          })
                          .finally(() => setPrivacyShieldBusy(false))
                      },
                      onRefresh: () => {
                        setPrivacyShieldBusy(true)
                        void invoke<{
                          enabled: boolean
                          ready: boolean
                          message: string
                          attribution: string
                        }>('privacy_shield_refresh_lists')
                          .then((ps) => {
                            setPrivacyShield({
                              enabled: Boolean(ps.enabled),
                              ready: Boolean(ps.ready),
                              message: ps.message || '',
                              attribution: ps.attribution || '',
                            })
                          })
                          .catch((e) => {
                            setPrivacyShield((prev) =>
                              prev
                                ? {
                                    ...prev,
                                    message:
                                      e instanceof Error
                                        ? e.message
                                        : 'Privacy Shield refresh failed',
                                  }
                                : prev,
                            )
                          })
                          .finally(() => setPrivacyShieldBusy(false))
                      },
                    }
                  : null
              }
              persona={persona}
              setPersona={setPersona}
              userName={userName}
              setUserName={setUserName}
              agentName={agentName}
              setAgentName={setAgentName}
              agentGender={agentGender}
              setAgentGender={setAgentGender}
              accessScope={accessScope}
              setAccessScope={setAccessScope}
              launchAtLogin={launchAtLogin}
              setLaunchAtLogin={setLaunchAtLogin}
              startInTray={startInTray}
              setStartInTray={setStartInTray}
              skipQuitWarn={skipQuitWarn}
              setSkipQuitWarn={setSkipQuitWarn}
              webToolsEnabled={webToolsEnabled}
              setWebToolsEnabled={setWebToolsEnabled}
              httpBootstrap={httpBootstrap}
              setHttpBootstrap={setHttpBootstrap}
              privacyMode={privacyMode}
              setPrivacyMode={setPrivacyMode}
              sleevEnabled={sleevEnabled}
              setSleevEnabled={setSleevEnabled}
              sleevGatewayUrl={sleevGatewayUrl}
              setSleevGatewayUrl={setSleevGatewayUrl}
              sleevAllowRemoteGateway={sleevAllowRemoteGateway}
              setSleevAllowRemoteGateway={setSleevAllowRemoteGateway}
              sleevStatus={sleevStatus}
              soulFieldEnabled={soulFieldEnabled}
              setSoulFieldEnabled={setSoulFieldEnabled}
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
              visionBusy={visionBusy}
              setVisionBusy={setVisionBusy}
              visionMsg={visionMsg}
              setVisionMsg={setVisionMsg}
              refreshVision={refreshVision}
              startVisionInstallPoll={startVisionInstallPoll}
              rmb={rmb}
              rmbBusy={rmbBusy}
              setRmbBusy={setRmbBusy}
              rmbMsg={rmbMsg}
              setRmbMsg={setRmbMsg}
              refreshRmb={refreshRmb}
              onSettingsSaved={onSettingsSaved}
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
              messengers={messengers}
              messengerDrafts={messengerDrafts}
              setMessengerDrafts={setMessengerDrafts}
              assistant={settings?.assistant}
              assistantDraft={assistantDraft}
              setAssistantDraft={setAssistantDraft}
              onAssistantAccountsChanged={() => {
                void load()
              }}
              settingsMode={settingsMode}
              primaryProviders={primaryProviders}
              advancedProviders={advancedProviders}
              activeMeta={activeMeta ?? FALLBACK_PROVIDERS[0]}
              showBaseUrl={showBaseUrl}
              providerModels={providerModels}
              customName={customName}
              setCustomName={setCustomName}
              handleProviderChange={handleProviderChange}
              handleBrowseProject={() => { void handleBrowseProject() }}
              themeId={themeId}
              onThemeChange={onThemeChange}
              density={density}
              onDensityChange={onDensityChange}
              customAccent={customAccent}
              onCustomAccentChange={onCustomAccentChange}
              fontScale={fontScale}
              onFontScaleChange={onFontScaleChange}
              reduceMotion={reduceMotion}
              onReduceMotionChange={onReduceMotionChange}
              highContrast={highContrast}
              onHighContrastChange={onHighContrastChange}
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

      {/* Sticky save bar — always reachable at bottom of settings */}
      <div className="settings-sticky-save flex flex-col gap-2">
        {errorMessage && (
          <div
            className="px-2 py-1.5 rounded-lg text-xs"
            style={{
              background: 'color-mix(in srgb, var(--error) 12%, var(--bg-secondary))',
              color: 'var(--error)',
              border: '1px solid color-mix(in srgb, var(--error) 40%, var(--border))',
            }}
          >
            {errorMessage}
          </div>
        )}
        {statusMessage && !errorMessage && (
          <div
            className="px-2 py-1.5 rounded-lg text-xs"
            style={{
              background: 'color-mix(in srgb, var(--success) 12%, var(--bg-secondary))',
              color: 'var(--success)',
              border: '1px solid color-mix(in srgb, var(--success) 35%, var(--border))',
            }}
          >
            {statusMessage}
          </div>
        )}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving || loading || !settings}
            className="ui-btn ui-btn-primary flex-1"
            style={{ padding: '0.55rem 0.75rem' }}
            title={
              loading || !settings
                ? 'Wait for settings to finish loading'
                : undefined
            }
          >
            {saving ? 'Saving & reloading…' : loading ? 'Loading…' : 'Save settings'}
          </button>
          {saved && !errorMessage && !statusMessage && (
            <span className="text-xs font-semibold" style={{ color: 'var(--success)' }}>
              Saved
            </span>
          )}
        </div>
      </div>
      {saveToast && (
        <div
          className={`ui-toast ${saveToast.kind === 'ok' ? 'ui-toast-success' : 'ui-toast-error'}`}
          role="status"
        >
          {saveToast.text}
        </div>
      )}
    </div>
  )
}

