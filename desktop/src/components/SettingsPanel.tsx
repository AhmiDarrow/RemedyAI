import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { invoke } from '@tauri-apps/api/core'
import { getSettings, updateSettings, type Settings, type SettingsUpdate } from '../api/settings'
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
  FALLBACK_PROVIDERS,
  type ProviderInfo,
} from '../api/providers'
import type { ThemeId } from '../themes'
import type { UpdateInfo } from '../api/updates'
import { THEME_LIST } from '../themes'
import { ThemeColorDot } from './ThemeSwitcher'
import { HOTKEYS } from '../hotkeys'
import type { ModelInfo } from '../App'
import type { Density } from '../utils/chatPrefs'
import { normalizeToolProcess, TOOL_PROCESS_MODES, type ToolProcessMode } from '../utils/toolLabels'

/** Matches SetupWizard personas (style overlay). */
const PERSONAS = [
  { id: 'balanced', name: 'Balanced', description: 'Helpful and adaptable to the task' },
  { id: 'efficient', name: 'Efficient', description: 'Concise, code-first, minimal explanation' },
  { id: 'detailed', name: 'Detailed', description: 'Thorough explanations with context' },
  { id: 'playful', name: 'Playful', description: 'Casual tone with light humor' },
] as const

async function pickProjectFolder(): Promise<string | null> {
  try {
    const path = await invoke<string | null>('pick_folder')
    return path && path.trim() ? path.trim() : null
  } catch {
    return null
  }
}

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
  onCheckUpdates: () => void
  /** Opens the Ollama-style download → install → relaunch screen. */
  onInstallUpdate?: () => void
  models: ModelInfo[]
  onSettingsSaved?: () => void
  toolProcessMode?: ToolProcessMode
  onToolProcessChange?: (mode: ToolProcessMode) => void
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
  onCheckUpdates,
  onInstallUpdate,
  models,
  onSettingsSaved,
  toolProcessMode,
  onToolProcessChange,
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
  const [persona, setPersona] = useState('balanced')
  const [userName, setUserName] = useState('')
  const [agentName, setAgentName] = useState('Remedy')
  const [accessScope, setAccessScope] = useState('project')
  const [launchAtLogin, setLaunchAtLogin] = useState(false)
  const [startInTray, setStartInTray] = useState(false)
  const [closeToTray, setCloseToTray] = useState(false)
  const [harnessMode, setHarnessMode] = useState('auto')
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

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [s, providers] = await Promise.all([getSettings(), listProviders()])
      setCatalog(providers)
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
      setStartInTray(Boolean(s.start_in_tray))
      setCloseToTray(Boolean(s.close_to_tray))
      setHarnessMode(s.harness_mode || 'auto')
      {
        const tp = normalizeToolProcess(s.tool_process)
        setToolProcess(tp)
        onToolProcessChange?.(tp)
      }
      setApiKey('')
      try {
        const osLogin = await invoke<boolean>('get_launch_at_login')
        setLaunchAtLogin(Boolean(osLogin || s.launch_at_login))
      } catch {
        /* browser / missing permission */
      }
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
    } catch {
      // server not ready
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) {
      load()
      setSaved(false)
      setErrorMessage('')
      setXaiLoginMsg('')
      setXaiUserCode('')
      setXaiVerifyUrl('')
    } else {
      stopXaiPoll()
      setXaiLoginBusy(false)
    }
    return () => stopXaiPoll()
  }, [open, load, stopXaiPoll])

  const handleXaiSignIn = async () => {
    setXaiLoginBusy(true)
    setXaiLoginMsg('')
    setErrorMessage('')
    stopXaiPoll()
    try {
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
            // Ensure provider is xAI after OAuth
            setProvider('xai')
            setBaseUrl('https://api.x.ai/v1')
            if (!model.startsWith('grok')) setModel('grok-3-mini')
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
      persona,
      user_name: userName.trim(),
      name: agentName.trim() || 'Remedy',
      access_scope: accessScope,
      launch_at_login: launchAtLogin,
      start_in_tray: startInTray,
      close_to_tray: closeToTray,
      harness_mode: harnessMode,
      tool_process: toolProcess,
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
        })
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
      className="flex flex-col border-l"
      style={{
        width: 300,
        minWidth: 300,
        background: 'var(--bg-secondary)',
        borderColor: 'var(--border)',
      }}
    >
      <div
        className="flex items-center justify-between px-3 py-2 border-b text-xs font-medium"
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

      <div className="flex-1 overflow-y-auto p-3 text-xs space-y-4">
        {loading ? (
          <div style={{ color: 'var(--text-muted)' }}>Loading...</div>
        ) : (
          <>
            {/* Provider */}
            <section>
              <div className="font-semibold mb-2" style={{ color: 'var(--text-primary)', fontSize: '0.75rem' }}>
                Provider
              </div>
              <label className="block mb-1" style={{ color: 'var(--text-muted)' }}>Type</label>
              <select
                value={provider}
                onChange={(e) => handleProviderChange(e.target.value)}
                className="w-full rounded px-2 py-1 text-xs mb-2 outline-none"
                style={{
                  background: 'var(--bg-tertiary)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border)',
                }}
              >
                {primaryProviders.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
                {showAdvanced && advancedProviders.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
              {!showAdvanced && advancedProviders.length > 0 && (
                <button
                  type="button"
                  className="mb-2 text-[10px] underline"
                  style={{ color: 'var(--text-muted)' }}
                  onClick={() => setShowAdvanced(true)}
                >
                  Show advanced (custom endpoint)…
                </button>
              )}

              {showBaseUrl && (
                <Field label="Base URL" value={baseUrl} onChange={setBaseUrl} />
              )}
              <label className="block mb-0.5" style={{ color: 'var(--text-muted)' }}>Model</label>
              <select
                value={model}
                onChange={(e) => setModel(e.target.value)}
                className="w-full rounded px-2 py-1 text-xs mb-2 outline-none"
                style={{
                  background: 'var(--bg-tertiary)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border)',
                }}
              >
                {providerModels.length === 0 && <option value={model}>{model}</option>}
                {providerModels.every((m) => m.id !== model) && model && (
                  <option value={model}>{model} (current)</option>
                )}
                {providerModels.map((m) => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))}
              </select>
              <div className="mb-2 text-[10px]" style={{ color: 'var(--text-muted)' }}>
                Models for <strong>{activeMeta?.name || provider}</strong>
                {showBaseUrl ? ' · custom base URL enabled' : ''}.
              </div>

              {provider === 'xai' && (
                <div
                  className="mb-2 p-2 rounded space-y-2"
                  style={{ border: '1px solid var(--border)', background: 'var(--bg-tertiary)' }}
                >
                  <div className="font-medium" style={{ color: 'var(--text-primary)' }}>
                    Sign in with xAI
                  </div>
                  <div className="text-[10px] leading-snug" style={{ color: 'var(--text-muted)' }}>
                    Use your SuperGrok / X Premium+ account (recommended), or a console API key below.
                  </div>
                  {xaiAuth?.connected ? (
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[11px]" style={{ color: 'var(--success)' }}>
                        Connected ({xaiAuth.auth_method === 'oauth' ? 'OAuth' : 'API key'})
                      </span>
                      <button
                        type="button"
                        onClick={() => void handleXaiLogout()}
                        className="px-2 py-1 rounded text-[11px]"
                        style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
                      >
                        Sign out
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => void handleXaiSignIn()}
                      disabled={xaiLoginBusy}
                      className="w-full py-1.5 rounded text-xs font-semibold"
                      style={{
                        background: xaiLoginBusy ? 'var(--bg-secondary)' : 'var(--accent)',
                        color: '#fff',
                        cursor: xaiLoginBusy ? 'wait' : 'pointer',
                      }}
                    >
                      {xaiLoginBusy ? 'Waiting for approval…' : 'Sign in with xAI'}
                    </button>
                  )}
                  {xaiUserCode && (
                    <div className="text-[11px] space-y-1" style={{ color: 'var(--text-secondary)' }}>
                      <div>
                        Code: <code style={{ color: 'var(--accent)' }}>{xaiUserCode}</code>
                      </div>
                      {xaiVerifyUrl && (
                        <button
                          type="button"
                          className="underline text-left"
                          style={{ color: 'var(--accent)' }}
                          onClick={() => void openExternalUrl(xaiVerifyUrl)}
                        >
                          Open verification page
                        </button>
                      )}
                    </div>
                  )}
                  {xaiLoginMsg && (
                    <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                      {xaiLoginMsg}
                    </div>
                  )}
                </div>
              )}

              <Field
                label={
                  provider === 'xai'
                    ? apiKeySet
                      ? 'API key (optional — change?)'
                      : 'API key (optional)'
                    : apiKeySet
                      ? 'API Key (set - change?)'
                      : 'API Key'
                }
                value={apiKey}
                onChange={setApiKey}
                placeholder={
                  provider === 'xai'
                    ? apiKeySet
                      ? '(leave blank to keep current)'
                      : 'xai-… from console.x.ai'
                    : apiKeySet
                      ? '(leave blank to keep current)'
                      : 'sk-...'
                }
                password
              />
            </section>

            {/* You + Agent */}
            <section>
              <div className="font-semibold mb-2" style={{ color: 'var(--text-primary)', fontSize: '0.75rem' }}>
                You &amp; Agent
              </div>
              <Field
                label="Your name (what Remedy calls you)"
                value={userName}
                onChange={setUserName}
                placeholder="e.g. Alex"
              />
              <div className="text-[10px] mb-2 leading-snug" style={{ color: 'var(--text-muted)' }}>
                Saved to your profile so Remedy can address you naturally.
              </div>
              <Field
                label="Agent name"
                value={agentName}
                onChange={setAgentName}
                placeholder="Remedy"
              />
              <label className="block mb-1" style={{ color: 'var(--text-muted)' }}>
                Persona
              </label>
              <div className="space-y-1.5 mb-1">
                {PERSONAS.map((p) => (
                  <label
                    key={p.id}
                    className="flex items-start gap-2 px-2 py-1.5 rounded cursor-pointer"
                    style={{
                      background: persona === p.id ? 'var(--bg-tertiary)' : 'transparent',
                      border: persona === p.id ? '1px solid var(--accent)' : '1px solid var(--border)',
                    }}
                  >
                    <input
                      type="radio"
                      name="settings-persona"
                      value={p.id}
                      checked={persona === p.id}
                      onChange={() => setPersona(p.id)}
                      className="mt-0.5"
                      style={{ accentColor: 'var(--accent)' }}
                    />
                    <span>
                      <span className="block font-medium" style={{ color: 'var(--text-primary)' }}>
                        {p.name}
                      </span>
                      <span className="block text-[10px]" style={{ color: 'var(--text-muted)' }}>
                        {p.description}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
              <div className="text-[10px] leading-snug" style={{ color: 'var(--text-muted)' }}>
                Persona is a communication style. Identity stays your partner — change anytime.
              </div>
            </section>

            {/* Project */}
            <section>
              <div className="font-semibold mb-2" style={{ color: 'var(--text-primary)', fontSize: '0.75rem' }}>
                Project workspace
              </div>
              <label className="block mb-0.5" style={{ color: 'var(--text-muted)' }}>
                Default project folder
              </label>
              <div className="flex gap-1 mb-1">
                <input
                  type="text"
                  value={projectPath}
                  onChange={(e) => setProjectPath(e.target.value)}
                  placeholder="e.g. C:\Users\You\Projects\MyApp"
                  className="flex-1 min-w-0 rounded px-2 py-1 text-xs outline-none"
                  style={{
                    background: 'var(--bg-tertiary)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--border)',
                  }}
                />
                <button
                  type="button"
                  onClick={() => void handleBrowseProject()}
                  title="Browse for folder"
                  className="flex-shrink-0 px-2 rounded text-xs font-medium"
                  style={{
                    background: 'var(--bg-tertiary)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--border)',
                  }}
                  aria-label="Browse for project folder"
                >
                  <span className="inline-flex items-center gap-1">
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
                      <path
                        d="M1.5 3.5h4l1.5 1.5H14.5v8a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-9.5z"
                        stroke="currentColor"
                        strokeWidth="1.2"
                        fill="none"
                      />
                    </svg>
                    Browse
                  </span>
                </button>
              </div>
              <div className="text-[10px] leading-snug" style={{ color: 'var(--text-muted)' }}>
                Type a path or browse. Save reloads the workspace (file tools, shell cwd, @file search).
              </div>
            </section>

            {/* Access */}
            <section>
              <div className="font-semibold mb-2" style={{ color: 'var(--text-primary)', fontSize: '0.75rem' }}>
                Access &amp; permissions
              </div>
              <label className="block mb-0.5" style={{ color: 'var(--text-muted)' }}>
                Filesystem scope
              </label>
              <select
                value={accessScope}
                onChange={(e) => setAccessScope(e.target.value)}
                className="w-full rounded px-2 py-1 text-xs mb-1 outline-none"
                style={{
                  background: 'var(--bg-tertiary)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border)',
                }}
              >
                <option value="project">Project only (safest)</option>
                <option value="home">Project + home folder</option>
                <option value="full">Full user machine (you grant)</option>
              </select>
              <div className="text-[10px] leading-snug" style={{ color: 'var(--text-muted)' }}>
                Full scope still runs as your Windows user — no silent admin elevation.
                Prefer project-only unless Remedy needs files outside the workspace.
              </div>
            </section>

            {/* Always ready */}
            <section>
              <div className="font-semibold mb-2" style={{ color: 'var(--text-primary)', fontSize: '0.75rem' }}>
                Always ready
              </div>
              <label className="flex items-center gap-2 mb-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={launchAtLogin}
                  onChange={(e) => setLaunchAtLogin(e.target.checked)}
                  style={{ accentColor: 'var(--accent)' }}
                />
                <span style={{ color: 'var(--text-primary)' }}>Start with Windows</span>
              </label>
              <label className="flex items-center gap-2 mb-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={startInTray}
                  onChange={(e) => setStartInTray(e.target.checked)}
                  style={{ accentColor: 'var(--accent)' }}
                />
                <span style={{ color: 'var(--text-primary)' }}>Start in tray (when at login)</span>
              </label>
              <label className="flex items-center gap-2 mb-1 cursor-pointer">
                <input
                  type="checkbox"
                  checked={closeToTray}
                  onChange={(e) => setCloseToTray(e.target.checked)}
                  style={{ accentColor: 'var(--accent)' }}
                />
                <span style={{ color: 'var(--text-primary)' }}>Close window hides to tray</span>
              </label>
              <div className="text-[10px] leading-snug" style={{ color: 'var(--text-muted)' }}>
                Opt-in only. Uses the Windows <strong>Startup folder</strong> (Settings → Apps → Startup) —
                not the registry Run key. Change or disable anytime.
              </div>
            </section>

            {/* Tool process visibility */}
            <section>
              <div className="font-semibold mb-2" style={{ color: 'var(--text-primary)', fontSize: '0.75rem' }}>
                Tool process
              </div>
              <div className="text-[10px] leading-snug mb-2" style={{ color: 'var(--text-muted)' }}>
                How much of the provider tool trail to show in chat. Default is Off (minimal).
              </div>
              <div className="flex gap-1 mb-1">
                {TOOL_PROCESS_MODES.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => {
                      setToolProcess(m.id)
                      onToolProcessChange?.(m.id)
                    }}
                    className="flex-1 py-1.5 rounded text-xs font-medium"
                    title={m.hint}
                    style={{
                      background: toolProcess === m.id ? 'var(--accent)' : 'var(--bg-tertiary)',
                      color: toolProcess === m.id ? '#fff' : 'var(--text-secondary)',
                      border: '1px solid var(--border)',
                    }}
                  >
                    {m.label}
                  </button>
                ))}
              </div>
              <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                {TOOL_PROCESS_MODES.find((m) => m.id === toolProcess)?.hint}
              </div>
            </section>

            {/* Memory Harness */}
            <section>
              <div className="font-semibold mb-2" style={{ color: 'var(--text-primary)', fontSize: '0.75rem' }}>
                Memory Harness
              </div>
              <label className="block mb-0.5" style={{ color: 'var(--text-muted)' }}>
                Mode
              </label>
              <select
                value={harnessMode}
                onChange={(e) => setHarnessMode(e.target.value)}
                className="w-full rounded px-2 py-1 text-xs mb-1 outline-none"
                style={{
                  background: 'var(--bg-tertiary)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border)',
                }}
              >
                <option value="auto">Auto — prune + nudge compress</option>
                <option value="manual">Manual — /compact only</option>
                <option value="off">Off</option>
              </select>
              <div className="text-[10px] leading-snug" style={{ color: 'var(--text-muted)' }}>
                Keeps long chats lean without deleting your transcript. Use{' '}
                <code style={{ color: 'var(--accent)' }}>/compact</code> or{' '}
                <code style={{ color: 'var(--accent)' }}>/harness</code>.
              </div>
            </section>

            {/* Theme */}
            <section>
              <div className="font-semibold mb-2" style={{ color: 'var(--text-primary)', fontSize: '0.75rem' }}>
                Theme
              </div>
              <div className="flex flex-col gap-1">
                {THEME_LIST.map((t) => (
                  <button
                    key={t.id}
                    onClick={() => onThemeChange(t.id)}
                    className="flex items-center gap-2 px-2 py-1.5 rounded text-xs text-left transition-colors w-full"
                    style={{
                      background: t.id === themeId ? 'var(--bg-tertiary)' : 'transparent',
                      color: 'var(--text-primary)',
                    }}
                  >
                    <ThemeColorDot themeId={t.id} />
                    <span>
                      {t.name}
                      {t.id === 'system' ? (
                        <span style={{ color: 'var(--text-muted)' }}> · match OS</span>
                      ) : null}
                    </span>
                    {t.id === themeId && (
                      <svg width="12" height="12" viewBox="0 0 12 12" fill="none" className="ml-auto">
                        <path d="M2 6l3 3 5-5" stroke="var(--accent)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    )}
                  </button>
                ))}
              </div>

              <div className="mt-3 space-y-2">
                <label className="block text-[10px]" style={{ color: 'var(--text-muted)' }}>
                  Density
                </label>
                <div className="flex gap-1">
                  {(['cozy', 'compact'] as Density[]).map((d) => (
                    <button
                      key={d}
                      type="button"
                      onClick={() => onDensityChange?.(d)}
                      className="flex-1 py-1.5 rounded text-xs capitalize"
                      style={{
                        background: density === d ? 'var(--accent)' : 'var(--bg-tertiary)',
                        color: density === d ? '#fff' : 'var(--text-secondary)',
                        border: '1px solid var(--border)',
                      }}
                    >
                      {d}
                    </button>
                  ))}
                </div>
                <label className="block text-[10px] mt-2" style={{ color: 'var(--text-muted)' }}>
                  Custom accent (optional)
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="color"
                    value={customAccent && /^#/.test(customAccent) ? customAccent : '#8b5cf6'}
                    onChange={(e) => onCustomAccentChange?.(e.target.value)}
                    className="w-8 h-8 rounded cursor-pointer border-0 bg-transparent"
                    title="Pick accent color"
                  />
                  <input
                    type="text"
                    value={customAccent}
                    onChange={(e) => onCustomAccentChange?.(e.target.value)}
                    placeholder="#hex or empty"
                    className="flex-1 rounded px-2 py-1 text-xs outline-none font-mono"
                    style={{
                      background: 'var(--bg-primary)',
                      border: '1px solid var(--border)',
                      color: 'var(--text-primary)',
                    }}
                  />
                  {customAccent && (
                    <button
                      type="button"
                      className="text-[10px] px-1.5 py-1 rounded"
                      style={{ color: 'var(--text-muted)', border: '1px solid var(--border)' }}
                      onClick={() => onCustomAccentChange?.('')}
                    >
                      Reset
                    </button>
                  )}
                </div>
              </div>
            </section>

            {/* Help / Keyboard */}
            <section>
              <div className="font-semibold mb-2" style={{ color: 'var(--text-primary)', fontSize: '0.75rem' }}>
                Help &amp; shortcuts
              </div>
              <div className="text-xs mb-2" style={{ color: 'var(--text-muted)' }}>
                Enter sends · Shift+Enter new line · type /help in chat for commands
              </div>
              <div
                className="rounded-md overflow-hidden text-xs"
                style={{ border: '1px solid var(--border)' }}
              >
                {HOTKEYS.map((h) => (
                  <div
                    key={`${h.keys}-${h.action}`}
                    className="flex justify-between gap-2 px-2 py-1.5"
                    style={{
                      borderBottom: '1px solid var(--border)',
                      color: 'var(--text-secondary)',
                    }}
                  >
                    <code style={{ color: 'var(--accent)', whiteSpace: 'nowrap' }}>{h.keys}</code>
                    <span className="text-right" style={{ color: 'var(--text-primary)' }}>
                      {h.action}
                    </span>
                  </div>
                ))}
              </div>
              <div className="text-xs mt-2 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                Connect a provider above to chat. Plan mode explores without editing files;
                Build mode can change your project. Data stays in ~/.remedy on this machine.
              </div>
            </section>

            {/* About */}
            <section>
              <div className="font-semibold mb-2" style={{ color: 'var(--text-primary)', fontSize: '0.75rem' }}>
                About
              </div>
              <div className="space-y-1" style={{ color: 'var(--text-secondary)' }}>
                <div className="flex justify-between">
                  <span style={{ color: 'var(--text-muted)' }}>Version</span>
                  <span>
                    v
                    {updateInfo?.current_version
                      || settings?.version
                      || '—'}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span style={{ color: 'var(--text-muted)' }}>Config</span>
                  <span style={{ fontFamily: 'monospace', fontSize: '0.65rem' }}>~/.remedy/config.toml</span>
                </div>
              </div>

              <div className="mt-3 pt-3 border-t" style={{ borderColor: 'var(--border)' }}>
                <button
                  type="button"
                  onClick={() => {
                    void onCheckUpdates()
                  }}
                  disabled={checkingUpdates}
                  className="w-full py-1.5 rounded text-xs font-medium transition-colors"
                  style={{
                    background: checkingUpdates ? 'var(--bg-secondary)' : 'var(--bg-tertiary)',
                    color: checkingUpdates ? 'var(--text-muted)' : 'var(--text-primary)',
                    border: '1px solid var(--border)',
                    cursor: checkingUpdates ? 'wait' : 'pointer',
                  }}
                >
                  {checkingUpdates ? 'Checking for updates…' : 'Check for Updates'}
                </button>
                {/* Always reserve status area so a check never looks like a no-op */}
                <div className="mt-2 space-y-1 min-h-[2.5rem]">
                  {checkingUpdates && !updateInfo && (
                    <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
                      Contacting GitHub releases…
                    </div>
                  )}
                  {updateInfo && (
                    <>
                      <div className="flex justify-between text-xs">
                        <span style={{ color: 'var(--text-muted)' }}>Current</span>
                        <span style={{ color: 'var(--text-primary)' }}>
                          {updateInfo.current_version}
                        </span>
                      </div>
                      {(updateInfo.latest_desktop || updateInfo.latest_python) && (
                        <div className="flex justify-between text-xs">
                          <span style={{ color: 'var(--text-muted)' }}>Latest</span>
                          <span
                            style={{
                              color: updateInfo.update_available
                                ? 'var(--accent)'
                                : 'var(--text-primary)',
                            }}
                          >
                            {updateInfo.latest_desktop || updateInfo.latest_python}
                          </span>
                        </div>
                      )}
                      {updateInfo.update_available && (
                        <button
                          type="button"
                          onClick={() => onInstallUpdate?.()}
                          className="w-full mt-2 py-2 rounded text-xs font-semibold"
                          style={{ background: 'var(--accent)', color: '#fff' }}
                        >
                          Update & Relaunch
                        </button>
                      )}
                      {updateInfo.update_available && (
                        <div className="mt-1 text-[0.65rem]" style={{ color: 'var(--text-muted)' }}>
                          Downloads the installer, updates Remedy, and restarts — like Ollama.
                        </div>
                      )}
                      {!updateInfo.update_available && !updateInfo.error && (
                        <div className="mt-1 text-xs" style={{ color: 'var(--success)' }}>
                          You&apos;re up to date
                        </div>
                      )}
                      {updateInfo.error && (
                        <div
                          className="mt-1 text-xs break-words"
                          style={{ color: 'var(--error, #ef4444)' }}
                          title={updateInfo.error}
                        >
                          {updateInfo.error}
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            </section>
          </>
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

function Field({
  label,
  value,
  onChange,
  placeholder,
  password,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  password?: boolean
}) {
  return (
    <div className="mb-2">
      <label className="block mb-0.5" style={{ color: 'var(--text-muted)' }}>{label}</label>
      <input
        type={password ? 'password' : 'text'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded px-2 py-1 text-xs outline-none"
        style={{
          background: 'var(--bg-tertiary)',
          color: 'var(--text-primary)',
          border: '1px solid var(--border)',
        }}
        onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
        onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
      />
    </div>
  )
}
