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
  logoutXai,
  type XaiAuthStatus,
} from '../api/auth'
import {
  beginXaiOAuth,
  resumeXaiOAuthPoll,
  subscribeXaiOAuth,
  xaiModelAfterOauth,
} from '../api/xaiOAuth'
import {
  listProviders,
  listConnectedProviders,
  probeProvider,
  OFFLINE_PROVIDERS,
  type ProviderInfo,
  type ConnectedProvider,
} from '../api/providers'
import {
  allowsFreeTextModel,
  createRequestGeneration,
  discoveryHint,
  fetchModels,
  mergeModelOptions,
  pickDefaultModel,
  showsBaseUrl,
  type DiscoveryStatus,
  type ModelOption,
} from '../api/modelDiscovery'
import type { ThemeId } from '../themes'
import type { UpdateInfo } from '../api/updates'
import type { ModelInfo } from '../App'
import type { Density, FontScale } from '../utils/chatPrefs'
import {
  normalizeToolProcess,

  type ToolProcessMode,
} from '../utils/toolLabels'
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
  const loadedLlmRef = useRef({ provider: '', model: '', baseUrl: '' })
  const [saved, setSaved] = useState(false)
  const [errorMessage, setErrorMessage] = useState('')

  // Unknown until GET /settings answers — never a seeded provider/model.
  const [provider, setProvider] = useState('')
  const [model, setModel] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
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
  const [approvalMode, setApprovalMode] = useState<'ask' | 'auto' | 'full'>('ask')
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
  const [catalog, setCatalog] = useState<ProviderInfo[]>(OFFLINE_PROVIDERS)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [xaiAuth, setXaiAuth] = useState<XaiAuthStatus | null>(null)
  const [xaiLoginBusy, setXaiLoginBusy] = useState(false)
  const [xaiUserCode, setXaiUserCode] = useState('')
  const [xaiVerifyUrl, setXaiVerifyUrl] = useState('')
  const [xaiLoginMsg, setXaiLoginMsg] = useState('')
  const [vision, setVision] = useState<VisionStatus | null>(null)

  const [visionBusy, setVisionBusy] = useState(false)
  const [visionMsg, setVisionMsg] = useState('')
  const visionPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [rmb, setRmb] = useState<RmbStatus | null>(null)
  const [rmbBusy, setRmbBusy] = useState(false)
  const [rmbMsg, setRmbMsg] = useState('')
  const [connectedList, setConnectedList] = useState<ConnectedProvider[]>([])
  const [providerKeysSet, setProviderKeysSet] = useState<Record<string, boolean>>({})
  const [testBusy, setTestBusy] = useState(false)
  const [testMsg, setTestMsg] = useState<string | null>(null)
  const [testOk, setTestOk] = useState<boolean | null>(null)
  const [liveModelsByProvider, setLiveModelsByProvider] = useState<
    Record<string, ModelOption[]>
  >({})
  /** Result of the last GET /models discovery for the active provider. */
  const [discovery, setDiscovery] = useState<DiscoveryStatus | null>(null)
  const [discoveryError, setDiscoveryError] = useState<string | null>(null)
  const [discoveryBusy, setDiscoveryBusy] = useState(false)
  /** Stale-response guard: only the newest /models request may apply. */
  const discoveryGenRef = useRef(createRequestGeneration())
  /** Bumped after a successful Test to force a fresh discovery. */
  const [discoveryNonce, setDiscoveryNonce] = useState(0)
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
  const activeMeta = useMemo<ProviderInfo>(
    () =>
      catalog.find((p) => p.id === provider)
      || OFFLINE_PROVIDERS.find((p) => p.id === provider)
      || {
        id: provider,
        name: provider,
        base_url: '',
        models: [],
        default_model: '',
        auth: [],
        oauth: false,
        env_keys: [],
        show_base_url: true,
        advanced: false,
      },
    [catalog, provider],
  )
  const showBaseUrl = showsBaseUrl(provider, activeMeta)

  // Precedence: live discovery → session/app model list → backend catalog rows
  // (marked as catalog so the picker can label them).
  const providerModels = useMemo<ModelOption[]>(() => {
    if (!provider) return []
    const live = liveModelsByProvider[provider] || []
    const fromApi: ModelOption[] = models
      .filter((m) => m.provider === provider)
      .map((m) => ({ id: m.id, name: m.name, provider, source: m.source }))
    const fromCatalog: ModelOption[] = (activeMeta?.models || []).map((m) => ({
      id: m.id,
      name: m.name,
      provider,
      source: 'catalog' as const,
    }))
    return mergeModelOptions(live, fromApi, fromCatalog)
  }, [models, provider, activeMeta, liveModelsByProvider])
  const modelHint = useMemo(() => {
    if (discoveryError) return { kind: 'error' as const, text: discoveryError }
    return discoveryHint(
      discovery,
      (liveModelsByProvider[provider] || []).filter((m) => m.source !== 'catalog').length,
    )
  }, [discovery, discoveryError, liveModelsByProvider, provider])
  const modelFreeText = allowsFreeTextModel(provider, discovery) || Boolean(discoveryError)

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
      if (s.provider_keys_set && typeof s.provider_keys_set === 'object') {
        setProviderKeysSet(s.provider_keys_set)
      }
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
      const prov = s.llm_provider || ''
      const nextModel = s.llm_model || ''
      const nextBase = s.llm_base_url || ''
      setProvider(prov)
      setModel(nextModel)
      setBaseUrl(nextBase)
      loadedLlmRef.current = { provider: prov, model: nextModel, baseUrl: nextBase }
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
      setHttpBootstrap(s.http_bootstrap === true)
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
        setApprovalMode(am === 'auto' || am === 'full' ? am : 'ask')
      }
      {
        const tp = normalizeToolProcess(s.tool_process)
        setToolProcess(tp)
        onToolProcessChange?.(tp)
      }
      setApiKey('')
      const isAdvanced = providers.some((p) => p.id === prov && p.advanced)
      if (isAdvanced) setShowAdvanced(true)
      try {
        const xa = await getXaiAuthStatus()
        setXaiAuth(xa)
        if (xa.connected && prov === 'xai') setApiKeySet(true)
      } catch {
        const snap = s.xai_auth as XaiAuthStatus | undefined
        setXaiAuth(snap || null)
        if (snap?.connected && prov === 'xai') setApiKeySet(true)
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
      resumeXaiOAuthPoll()
      onPanelOpenChange(true)
    } else {
      onPanelOpenChange(false)
    }
  }, [open, load, onPanelOpenChange])

  useEffect(() => {
    return subscribeXaiOAuth((e) => {
      if (e.phase === 'started') {
        setXaiLoginBusy(true)
        setXaiUserCode(e.userCode || '')
        setXaiVerifyUrl(e.verifyUrl || '')
        setXaiLoginMsg(e.message || '')
        return
      }
      if (e.phase === 'connected') {
        setXaiLoginBusy(false)
        setXaiAuth(e.credentials || { provider: 'xai', auth_method: 'oauth', connected: true, has_api_key: false, has_oauth: true })
        setApiKeySet(true)
        setXaiLoginMsg(e.message || 'Signed in with xAI')
        setXaiUserCode('')
        setProvider('xai')
        setBaseUrl('https://api.x.ai/v1')
        setModel((prev) => {
          // Discovery refines this once /models answers; catalog default is the fallback.
          const nextModel = xaiModelAfterOauth(
            prev,
            catalog.find((x) => x.id === 'xai')?.default_model || '',
          )
          loadedLlmRef.current = {
            provider: 'xai',
            model: nextModel,
            baseUrl: 'https://api.x.ai/v1',
          }
          return nextModel
        })
        onSettingsSaved?.()
        void load()
        return
      }
      if (e.phase === 'error') {
        setXaiLoginBusy(false)
        setXaiLoginMsg(e.error || 'Sign-in failed or expired')
      }
    })
  }, [onSettingsSaved, load, catalog])

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
      await beginXaiOAuth({ keepSettings: true, llmModel: model })
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
      void load()
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
    const llmEdited =
      provider !== loadedLlmRef.current.provider
      || model !== loadedLlmRef.current.model
      || baseUrl !== loadedLlmRef.current.baseUrl
    const updates: SettingsUpdate = {
      ...(llmEdited
        ? {
            llm_provider: provider,
            llm_model: model,
            llm_base_url: baseUrl,
          }
        : {}),
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
      const allIds = catalog.map((p) => p.id)
      const ep = enabledProviders.includes('demo')
        ? enabledProviders
        : ['demo', ...enabledProviders]
      const coversAll = allIds.every((id) => ep.includes(id))
      // null = all on (clears a frozen connected-only list from older builds)
      updates.enabled_providers = coversAll ? null : ep
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
        const { isLinuxDesktop } = await import('../utils/platform')
        if (!isLinuxDesktop()) {
          await invoke('set_launch_at_login', { enabled: launchAtLogin })
        }
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

  // Live model discovery: refetch (debounced) whenever the provider, base URL or
  // API key changes, and after a successful Test. Unsaved form values ride along
  // so the backend can list models from the endpoint the user is about to save.
  useEffect(() => {
    const pid = provider
    if (!open || !pid) return
    const gen = discoveryGenRef.current.next()
    const timer = window.setTimeout(() => {
      setDiscoveryBusy(true)
      void fetchModels(pid, { base_url: baseUrl, api_key: apiKey })
        .then((data) => {
          if (!discoveryGenRef.current.isCurrent(gen)) return
          const rows: ModelOption[] = data.models.map((m) => ({
            id: m.id,
            name: m.name,
            provider: m.provider || pid,
            source: m.source,
          }))
          setLiveModelsByProvider((prev) => ({ ...prev, [pid]: rows }))
          setDiscovery(data.discovery || null)
          setDiscoveryError(null)
          if (data.discovery?.ok || (!data.discovery && rows.length)) {
            setModel((cur) => pickDefaultModel(cur, rows, data.default))
          } else {
            // Discovery failed: keep the user's pick; catalog default only if empty.
            setModel((cur) => cur || data.default || activeMeta?.default_model || '')
          }
        })
        .catch((e: unknown) => {
          if (!discoveryGenRef.current.isCurrent(gen)) return
          setDiscovery(null)
          setDiscoveryError(
            `Couldn't list models: ${e instanceof Error ? e.message : String(e)} — showing catalog defaults`,
          )
          // Last resort: the backend catalog default, only when nothing is selected.
          setModel((cur) => cur || activeMeta?.default_model || '')
        })
        .finally(() => {
          if (discoveryGenRef.current.isCurrent(gen)) setDiscoveryBusy(false)
        })
    }, 400)
    return () => window.clearTimeout(timer)
    // activeMeta only feeds the last-resort default; it must not retrigger fetches.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, provider, baseUrl, apiKey, discoveryNonce])

  const handleProviderChange = (p: string) => {
    const prev = provider
    setProvider(p)
    const preset = catalog.find((x) => x.id === p)
    const conn = connectedList.find((x) => x.id === p)
    if (preset) {
      const prevPreset = catalog.find((x) => x.id === prev)
      // Adopt the preset URL only when the current value is untouched (empty or
      // still the previous provider's default). Never clobber a base URL the
      // user typed — switching providers must not reset a custom base URL.
      const baseIsUntouched =
        !baseUrl || (prevPreset ? baseUrl === prevPreset.base_url : true)
      if (baseIsUntouched) setBaseUrl(preset.base_url)
    }
    // Model comes from discovery (see the /models effect); keep the provider's
    // remembered model meanwhile so the picker is never cross-wired.
    setModel(conn?.last_model || '')
    setDiscovery(null)
    setDiscoveryError(null)
    const stored = Boolean(providerKeysSet[p] || conn?.connected)
    if (p === 'xai') {
      setApiKeySet(Boolean(xaiAuth?.connected || stored))
    } else if (preset?.auth?.length && !preset.auth.includes('api_key')) {
      setApiKeySet(false)
    } else {
      setApiKeySet(stored)
    }
    setApiKey('')
    setTestMsg(null)
    setTestOk(null)
  }

  const handleTestConnection = async () => {
    setTestBusy(true)
    setTestMsg(null)
    setTestOk(null)
    try {
      const res = await probeProvider({
        provider,
        api_key: apiKey.trim() || undefined,
        base_url: baseUrl.trim() || undefined,
      })
      setTestOk(Boolean(res.ok))
      if (res.ok) {
        const seen: ModelOption[] = (res.model_list || [])
          .filter((m) => m && m.id)
          .map((m) => ({ id: m.id, name: m.name || m.id, provider, source: 'endpoint' as const }))
        if (seen.length) {
          // Populate the picker immediately; the discovery refetch below confirms.
          setLiveModelsByProvider((prev) => ({ ...prev, [provider]: seen }))
          setModel((cur) => pickDefaultModel(cur, seen, ''))
        }
        setDiscoveryNonce((n) => n + 1)
        const n = Number(res.models ?? seen.length)
        const ms = res.latency_ms != null ? ` · ${res.latency_ms} ms` : ''
        setTestMsg(
          n > 0
            ? `Connected — ${n} model${n === 1 ? '' : 's'}${ms}`
            : `Connected${ms}`,
        )
      } else {
        setTestMsg(res.error || 'Connection failed')
      }
    } catch (e: unknown) {
      setTestOk(false)
      setTestMsg(e instanceof Error ? e.message : 'Connection failed')
    } finally {
      setTestBusy(false)
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
        className="settings-chrome px-3 pt-2 pb-2 border-b shrink-0 space-y-2"
        style={{ borderColor: 'color-mix(in srgb, var(--border) 80%, transparent)' }}
      >
        <div
          className="settings-mode-tabs"
          role="tablist"
          aria-label="Settings detail"
          onKeyDown={(e) => {
            if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
            e.preventDefault()
            const next = settingsMode === 'simple' ? 'advanced' : 'simple'
            setSettingsMode(next)
            if (next === 'advanced') setShowAdvanced(true)
          }}
        >
          {(['simple', 'advanced'] as const).map((m) => (
            <button
              key={m}
              type="button"
              role="tab"
              id={`settings-tab-${m}`}
              aria-selected={settingsMode === m}
              tabIndex={settingsMode === m ? 0 : -1}
              title={
                m === 'simple'
                  ? 'Essentials — provider, you, project, appearance'
                  : 'Every section — messengers, vision, power tools, logs'
              }
              onClick={() => {
                setSettingsMode(m)
                if (m === 'advanced') setShowAdvanced(true)
              }}
            >
              {m === 'simple' ? 'Simple' : 'Advanced'}
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
                const all = root?.querySelectorAll<HTMLElement>('[data-section]')
                if (all) {
                  for (const el of all) {
                    if (el.offsetParent !== null) {
                      el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
                      break
                    }
                  }
                }
              })
            }
          }}
          placeholder="Search…"
          className="ui-input"
          aria-label="Search settings"
        />
      </div>

      <div ref={scrollBodyRef} className="flex-1 overflow-y-auto px-2.5 py-2.5 text-xs">
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
              providerKeysSet={providerKeysSet}
              onTestConnection={() => { void handleTestConnection() }}
              testBusy={testBusy}
              testMsg={testMsg}
              testOk={testOk}
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
              onSettingsSaved={() => {
                void load()
                onSettingsSaved?.()
              }}
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
              activeMeta={activeMeta}
              showBaseUrl={showBaseUrl}
              providerModels={providerModels}
              modelHint={modelHint}
              modelFreeText={modelFreeText}
              discoveryBusy={discoveryBusy}
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
            title={
              loading || !settings
                ? 'Wait for settings to finish loading'
                : undefined
            }
          >
            {saving ? 'Saving…' : loading ? 'Loading…' : 'Save'}
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

