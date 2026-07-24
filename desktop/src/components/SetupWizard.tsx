import { useState, useCallback, useEffect, useRef, useMemo } from 'react'
import { getSettings, updateSettings } from '../api/settings'
import {
  startXaiLogin,
  pollXaiLogin,
  openExternalUrl,
} from '../api/auth'
import {
  listProviders,
  listFreeProviders,
  detectOllama,
  FALLBACK_PROVIDERS,
  type ProviderInfo,
  type FreeProviderOption,
} from '../api/providers'
import {
  getVisionStatus,
  installVision,
  formatDownloadGb,
  type VisionStatus,
} from '../api/vision'

const PERSONAS = [
  { id: 'balanced', name: 'Balanced', description: 'Helpful and adaptable to the task' },
  { id: 'efficient', name: 'Efficient', description: 'Concise, code-first, minimal explanation' },
  { id: 'detailed', name: 'Detailed', description: 'Thorough explanations with context' },
  { id: 'playful', name: 'Playful', description: 'Casual tone with light humor' },
] as const

interface SetupWizardProps {
  open: boolean
  onComplete: () => void
}

type Step = 'welcome' | 'provider' | 'workspace' | 'persona' | 'vision' | 'finish'
const STEPS: Step[] = ['welcome', 'provider', 'workspace', 'persona', 'vision', 'finish']

export function SetupWizard({ open, onComplete }: SetupWizardProps) {
  const [step, setStep] = useState<Step>('welcome')
  const [catalog, setCatalog] = useState<ProviderInfo[]>(FALLBACK_PROVIDERS)
  const [freeOptions, setFreeOptions] = useState<FreeProviderOption[]>([])
  const [provider, setProvider] = useState('demo')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('gpt-4o-mini')
  const [baseUrl, setBaseUrl] = useState('https://api.openai.com/v1')
  const [projectPath, setProjectPath] = useState('')
  const [persona, setPersona] = useState('balanced')
  const [userName, setUserName] = useState('')
  const [launchAtLogin, setLaunchAtLogin] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [ollamaHint, setOllamaHint] = useState('')
  const [xaiConnected, setXaiConnected] = useState(false)
  const [xaiLoginBusy, setXaiLoginBusy] = useState(false)
  const [xaiUserCode, setXaiUserCode] = useState('')
  const [xaiVerifyUrl, setXaiVerifyUrl] = useState('')
  const [xaiLoginMsg, setXaiLoginMsg] = useState('')
  const xaiPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [enableVision, setEnableVision] = useState(false)
  const [visionStatus, setVisionStatus] = useState<VisionStatus | null>(null)
  const [visionInstallMsg, setVisionInstallMsg] = useState('')
  const [visionInstalling, setVisionInstalling] = useState(false)
  const visionPollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stepIndex = STEPS.indexOf(step)
  const primaryProviders = useMemo(() => catalog.filter((p) => !p.advanced), [catalog])
  const advancedProviders = useMemo(() => catalog.filter((p) => p.advanced), [catalog])
  const activeMeta = catalog.find((p) => p.id === provider) || FALLBACK_PROVIDERS[0]
  const showBaseUrl = Boolean(activeMeta?.show_base_url || provider === 'custom')
  const modelOptions = (activeMeta?.models || []).map((m) => m.id)

  const stopXaiPoll = useCallback(() => {
    if (xaiPollRef.current) {
      clearInterval(xaiPollRef.current)
      xaiPollRef.current = null
    }
  }, [])

  useEffect(() => () => stopXaiPoll(), [stopXaiPoll])

  useEffect(() => {
    return () => {
      if (visionPollRef.current) clearInterval(visionPollRef.current)
    }
  }, [])

  useEffect(() => {
    if (!open || step !== 'vision') return
    let cancelled = false
    void getVisionStatus()
      .then((s) => {
        if (!cancelled) {
          setVisionStatus(s)
          if (s.installed) setEnableVision(true)
        }
      })
      .catch(() => {
        /* offline */
      })
    return () => {
      cancelled = true
    }
  }, [open, step])

  // Load catalog + env bootstrap + Ollama detect when wizard opens.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    ;(async () => {
      const [providers, free] = await Promise.all([
        listProviders(),
        listFreeProviders(),
      ])
      if (cancelled) return
      setCatalog(providers)
      setFreeOptions(free)
      try {
        const s = await getSettings()
        if (cancelled) return
        if (s.llm_provider) {
          setProvider(s.llm_provider)
          const meta = providers.find((p) => p.id === s.llm_provider)
          if (meta?.advanced) setShowAdvanced(true)
          if (s.llm_model) setModel(s.llm_model)
          if (s.llm_base_url) setBaseUrl(s.llm_base_url)
        } else {
          // Default zero-setup demo when nothing configured
          const demo = providers.find((p) => p.id === 'demo')
          if (demo) {
            setProvider('demo')
            setModel(demo.default_model)
            setBaseUrl(demo.base_url)
          }
        }
      } catch {
        // offline — keep demo default
      }
      try {
        const ollama = await detectOllama()
        if (cancelled) return
        if (ollama.available) {
          const names = (ollama.models || []).slice(0, 4).join(', ')
          setOllamaHint(
            names
              ? `Ollama detected locally (${names}). You can pick Ollama without an API key.`
              : 'Ollama detected locally. You can pick Ollama without an API key.',
          )
        } else {
          setOllamaHint('')
        }
      } catch {
        setOllamaHint('')
      }
    })()
    return () => {
      cancelled = true
    }
  }, [open])

  const handleProviderChange = useCallback(
    (p: string) => {
      setProvider(p)
      const preset = catalog.find((x) => x.id === p)
      if (preset) {
        setBaseUrl(preset.base_url)
        setModel(preset.default_model)
      }
      setError('')
      setXaiLoginMsg('')
      setXaiUserCode('')
      if (p !== 'xai') {
        setXaiConnected(false)
        stopXaiPoll()
        setXaiLoginBusy(false)
      }
    },
    [stopXaiPoll, catalog],
  )

  const handleXaiSignIn = useCallback(async () => {
    setXaiLoginBusy(true)
    setXaiLoginMsg('')
    setError('')
    stopXaiPoll()
    try {
      // Ensure local API + Bearer are ready (first-run race with sidecar).
      try {
        const {
          clearApiToken,
          ensureApiToken,
          waitForLocalApi,
        } = await import('../api/client')
        const up = await waitForLocalApi(20000)
        if (!up) {
          throw new Error(
            'Local Remedy server is not responding on http://127.0.0.1:7400. '
              + 'Close setup, click Retry on the connection screen, then try Sign in again.',
          )
        }
        clearApiToken()
        await ensureApiToken()
      } catch (pre: unknown) {
        if (pre instanceof Error && pre.message.includes('Local Remedy server')) {
          throw pre
        }
        /* apiFetch will retry token */
      }
      const start = await startXaiLogin()
      setXaiUserCode(start.user_code)
      setXaiVerifyUrl(start.verification_uri_complete || start.verification_uri)
      setXaiLoginMsg(start.message || `Approve access with code ${start.user_code}`)
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
            setXaiConnected(true)
            setXaiLoginMsg('Signed in with xAI')
            setXaiUserCode('')
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
      setError(msg || 'Could not start xAI sign-in')
    }
  }, [stopXaiPoll])

  const handleNext = useCallback(() => {
    if (step === 'provider') {
      // Local / demo providers need no key.
      const noKeyOk =
        provider === 'ollama' ||
        provider === 'demo' ||
        provider === 'custom' ||
        /^(https?:\/\/)?(127\.0\.0\.1|localhost|\[::1\])/i.test(baseUrl)
      const xaiOk = provider === 'xai' && (xaiConnected || !!apiKey.trim())
      if (!noKeyOk && !apiKey.trim() && !xaiOk) {
        setError(
          provider === 'xai'
            ? 'Sign in with xAI or enter an API key. Or pick Demo / Ollama for free use.'
            : 'Enter an API key, or choose Demo (no signup) / Ollama for free use.',
        )
        return
      }
    }
    const idx = STEPS.indexOf(step)
    if (idx < STEPS.length - 1) {
      setStep(STEPS[idx + 1])
      setError('')
    }
  }, [step, provider, apiKey, baseUrl, xaiConnected])

  const handleBack = useCallback(() => {
    const idx = STEPS.indexOf(step)
    if (idx > 0) {
      setStep(STEPS[idx - 1])
      setError('')
    }
  }, [step])

  const handleFinish = useCallback(async () => {
    setSaving(true)
    setError('')
    try {
      if (launchAtLogin) {
        try {
          const { invoke } = await import('@tauri-apps/api/core')
          await invoke('set_launch_at_login', { enabled: true })
          await invoke('set_desktop_prefs', {
            close_to_tray: true,
            start_in_tray: true,
          })
        } catch {
          /* browser or missing command */
        }
      }
      // Re-bootstrap auth then save — corrupt/wiped installs often need a fresh token.
      try {
        const { clearApiToken, ensureApiToken } = await import('../api/client')
        clearApiToken()
        await ensureApiToken()
      } catch {
        /* updateSettings will surface the real error */
      }
      await updateSettings({
        llm_provider: provider,
        llm_model: model,
        llm_base_url: baseUrl,
        llm_api_key: apiKey || undefined,
        project_path: projectPath || undefined,
        persona: persona || undefined,
        user_name: userName.trim() || undefined,
        setup_completed: true,
        launch_at_login: launchAtLogin,
        start_in_tray: launchAtLogin,
        close_to_tray: launchAtLogin,
        vision_enabled: enableVision,
        vision_model_id: 'qwen2.5-vl-3b',
      })
      if (enableVision && !visionStatus?.installed) {
        try {
          setVisionInstalling(true)
          setVisionInstallMsg('Starting visual decoder download…')
          await installVision({ prefer_cuda: false })
          // Non-blocking: install continues in background after wizard closes
          setVisionInstallMsg('Download started — watch progress in the bottom status dock.')
        } catch (ve) {
          console.warn('Vision install start failed', ve)
        }
      }
      onComplete()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(
        msg && msg !== 'Failed to fetch'
          ? `Failed to save settings: ${msg}`
          : 'Failed to save settings. Is the local server running? Click Retry on the error screen, then try again.',
      )
    } finally {
      setSaving(false)
    }
  }, [
    apiKey,
    provider,
    model,
    baseUrl,
    projectPath,
    persona,
    userName,
    launchAtLogin,
    enableVision,
    visionStatus?.installed,
    onComplete,
  ])

  const handleSkip = useCallback(async () => {
    // Mark setup done so the wizard never blocks launch again.
    // User can configure the provider later in Settings.
    setSaving(true)
    setError('')
    try {
      try {
        const { clearApiToken, ensureApiToken } = await import('../api/client')
        clearApiToken()
        await ensureApiToken()
      } catch {
        /* best effort */
      }
      // Zero-setup: skip lands on Demo so chat works without a key.
      const demo = catalog.find((p) => p.id === 'demo') || FALLBACK_PROVIDERS.find((p) => p.id === 'demo')
      await updateSettings({
        setup_completed: true,
        llm_provider: 'demo',
        llm_model: demo?.default_model || 'codestral-latest',
        llm_base_url: demo?.base_url || 'https://api.llm7.io/v1',
      })
      onComplete()
    } catch (e: unknown) {
      // Still enter the app if the server briefly fails — avoid lockout.
      // Surface a soft notice so the user knows setup may reappear next launch.
      const msg = e instanceof Error ? e.message : String(e)
      console.warn('Skip setup save failed:', e)
      try {
        window.alert(
          `Could not save “setup complete” (${msg || 'server error'}). `
          + 'You can still use the app; the setup wizard may show again next launch. '
          + 'Use Settings or Retry if problems continue.',
        )
      } catch {
        /* headless */
      }
      onComplete()
    } finally {
      setSaving(false)
    }
  }, [onComplete, catalog])

  if (!open) return null

  const cardStyles = {
    background: 'var(--bg-secondary)',
    border: '1px solid var(--border)',
  } as const

  const inputStyles = {
    background: 'var(--bg-tertiary)',
    color: 'var(--text-primary)',
    border: '1px solid var(--border)',
  } as const

  const labelStyles = { color: 'var(--text-secondary)' } as const
  const mutedStyles = { color: 'var(--text-muted)' } as const

  const progressPct = ((stepIndex) / (STEPS.length - 1)) * 100

  const stepLabels: Record<Step, string> = {
    welcome: 'Welcome',
    provider: 'Provider',
    workspace: 'Folder',
    persona: 'Style',
    vision: 'Vision',
    finish: 'Ready',
  }

  return (
    <div
      className="flex items-center justify-center h-full p-4"
      style={{ background: 'var(--bg-primary)' }}
    >
      <div
        className="rounded-2xl shadow-2xl overflow-hidden w-full"
        style={{ maxWidth: 520, ...cardStyles }}
      >
        <div className="px-7 pt-7 pb-3 text-center">
          <div
            className="text-3xl font-bold tracking-tight mb-1"
            style={{ color: 'var(--accent)' }}
          >
            Remedy AI
          </div>
          <div className="text-sm" style={mutedStyles}>
            Local coding agent
          </div>
        </div>

        <div className="px-7 pb-2">
          <div
            className="h-1.5 rounded-full overflow-hidden"
            style={{ background: 'var(--bg-tertiary)' }}
          >
            <div
              className="h-full rounded-full transition-all duration-300"
              style={{
                width: `${progressPct}%`,
                background: 'var(--accent)',
              }}
            />
          </div>
          <div
            className="flex justify-between mt-2 text-[11px] font-medium tracking-wide"
            style={mutedStyles}
          >
            {STEPS.map((s, i) => (
              <span
                key={s}
                style={i <= stepIndex ? { color: 'var(--accent)' } : undefined}
              >
                {stepLabels[s]}
              </span>
            ))}
          </div>
        </div>

        <div className="px-7 pb-7 pt-4 space-y-4">

          {step === 'welcome' && (
            <div className="space-y-5">
              <p
                className="text-center text-base leading-snug"
                style={{ color: 'var(--text-primary)' }}
              >
                Set up a provider and start chatting. Takes about a minute.
              </p>
              <button
                onClick={handleNext}
                disabled={saving}
                className="w-full py-3 rounded-lg text-base font-semibold transition-colors"
                style={{ background: 'var(--accent)', color: '#fff' }}
              >
                Get Started
              </button>
              <button
                onClick={handleSkip}
                disabled={saving}
                className="w-full py-2 rounded text-sm transition-colors"
                style={{ background: 'transparent', color: 'var(--text-muted)' }}
                title="Skip setup for now — won't show again on next launch"
              >
                {saving ? 'Saving…' : 'Skip for now'}
              </button>
            </div>
          )}

          {step === 'provider' && (
            <>
              {freeOptions.length > 0 && (
                <div className="space-y-2">
                  <div className="text-sm font-medium" style={labelStyles}>
                    Free to try
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {freeOptions.map((opt) => {
                      const selected = provider === opt.id
                      return (
                        <button
                          key={opt.id}
                          type="button"
                          onClick={() => handleProviderChange(opt.id)}
                          title={opt.blurb}
                          className="rounded-lg px-3 py-2 text-sm font-medium transition-colors"
                          style={{
                            border: selected
                              ? '1.5px solid var(--accent)'
                              : '1px solid var(--border)',
                            background: selected
                              ? 'color-mix(in srgb, var(--accent) 14%, var(--bg-primary))'
                              : 'var(--bg-tertiary)',
                            color: 'var(--text-primary)',
                          }}
                        >
                          {opt.title}
                          {opt.badge ? (
                            <span
                              className="ml-1.5 text-[10px] opacity-80"
                              style={{ color: 'var(--accent)' }}
                            >
                              {opt.badge}
                            </span>
                          ) : null}
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}
              {ollamaHint && (
                <div
                  className="text-sm rounded-lg px-3 py-2"
                  style={{ ...mutedStyles, border: '1px solid var(--border)' }}
                >
                  {ollamaHint}
                </div>
              )}
              <div>
                <label
                  className="block mb-1.5 text-sm font-medium"
                  style={labelStyles}
                >
                  Provider
                </label>
                <select
                  value={provider}
                  onChange={(e) => handleProviderChange(e.target.value)}
                  className="w-full rounded-lg px-3 py-2.5 text-base outline-none"
                  style={inputStyles}
                  onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
                  onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
                >
                  {primaryProviders.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.badge ? `${p.name} · ${p.badge}` : p.name}
                    </option>
                  ))}
                  {showAdvanced && advancedProviders.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
                {!showAdvanced && advancedProviders.length > 0 && (
                  <button
                    type="button"
                    className="mt-1.5 text-sm underline"
                    style={mutedStyles}
                    onClick={() => setShowAdvanced(true)}
                  >
                    Custom endpoint…
                  </button>
                )}
              </div>
              {provider === 'demo' && (
                <div className="text-sm" style={mutedStyles}>
                  Demo needs no key (rate-limited). For private use, pick Ollama.
                </div>
              )}
              {activeMeta?.key_docs_url && provider !== 'demo' && provider !== 'ollama' && (
                <button
                  type="button"
                  className="text-sm underline"
                  style={{ color: 'var(--accent)' }}
                  onClick={() => void openExternalUrl(String(activeMeta.key_docs_url))}
                >
                  Get API key…
                </button>
              )}

              {provider === 'xai' && (
                <div
                  className="rounded-lg p-3 space-y-2"
                  style={{ border: '1px solid var(--border)', background: 'var(--bg-tertiary)' }}
                >
                  {xaiConnected ? (
                    <div className="text-sm" style={{ color: 'var(--success)' }}>
                      Signed in with xAI
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => void handleXaiSignIn()}
                      disabled={xaiLoginBusy}
                      className="w-full py-2.5 rounded-lg text-base font-semibold"
                      style={{
                        background: xaiLoginBusy ? 'var(--bg-secondary)' : 'var(--accent)',
                        color: '#fff',
                      }}
                    >
                      {xaiLoginBusy ? 'Waiting…' : 'Sign in with xAI'}
                    </button>
                  )}
                  {xaiUserCode && (
                    <div className="text-sm" style={labelStyles}>
                      Code: <code style={{ color: 'var(--accent)' }}>{xaiUserCode}</code>
                      {xaiVerifyUrl && (
                        <button
                          type="button"
                          className="block mt-1 underline"
                          style={{ color: 'var(--accent)' }}
                          onClick={() => void openExternalUrl(xaiVerifyUrl)}
                        >
                          Open verification page
                        </button>
                      )}
                    </div>
                  )}
                  {xaiLoginMsg && (
                    <div className="text-sm" style={mutedStyles}>{xaiLoginMsg}</div>
                  )}
                </div>
              )}

              {provider !== 'demo' && provider !== 'ollama' && (
                <div>
                  <label
                    className="block mb-1.5 text-sm font-medium"
                    style={labelStyles}
                  >
                    {provider === 'xai' ? 'API key (optional if signed in)' : 'API key'}
                  </label>
                  <input
                    type="password"
                    value={apiKey}
                    onChange={(e) => {
                      setApiKey(e.target.value)
                      setError('')
                    }}
                    placeholder={provider === 'xai' ? 'xai-…' : 'sk-…'}
                    className="w-full rounded-lg px-3 py-2.5 text-base outline-none"
                    style={inputStyles}
                    onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
                    onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleNext()
                    }}
                  />
                </div>
              )}

              <div>
                <label
                  className="block mb-1.5 text-sm font-medium"
                  style={labelStyles}
                >
                  Model
                </label>
                <select
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  className="w-full rounded-lg px-3 py-2.5 text-base outline-none"
                  style={inputStyles}
                  onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
                  onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
                >
                  {modelOptions.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                  {model && !modelOptions.includes(model) && (
                    <option value={model}>{model}</option>
                  )}
                </select>
                {(provider === 'ollama' || provider === 'custom' || provider === 'openrouter') && (
                  <input
                    type="text"
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    placeholder="Or type a model name"
                    className="w-full rounded-lg px-3 py-2 mt-1.5 text-sm outline-none"
                    style={inputStyles}
                    onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
                    onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
                  />
                )}
              </div>

              {showBaseUrl && (
                <div>
                  <label
                    className="block mb-1.5 text-sm font-medium"
                    style={labelStyles}
                  >
                    Base URL
                  </label>
                  <input
                    type="text"
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    className="w-full rounded-lg px-3 py-2.5 text-sm outline-none font-mono"
                    style={inputStyles}
                    onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
                    onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
                  />
                </div>
              )}
            </>
          )}

          {step === 'workspace' && (
            <div className="space-y-3">
              <p className="text-base" style={{ color: 'var(--text-primary)' }}>
                Default project folder for tools and shell (optional).
              </p>
              <input
                type="text"
                value={projectPath}
                onChange={(e) => setProjectPath(e.target.value)}
                placeholder="e.g. C:\Users\You\Projects\MyApp"
                className="w-full rounded-lg px-3 py-2.5 text-base outline-none"
                style={inputStyles}
                onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
                onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleNext()
                }}
              />
            </div>
          )}

          {step === 'persona' && (
            <div className="space-y-4">
              <div>
                <label className="block mb-1.5 text-sm font-medium" style={labelStyles}>
                  Your name
                </label>
                <input
                  value={userName}
                  onChange={(e) => setUserName(e.target.value)}
                  placeholder="Optional"
                  className="w-full rounded-lg px-3 py-2.5 text-base outline-none"
                  style={inputStyles}
                />
              </div>
              <div>
                <label
                  className="block mb-1.5 text-sm font-medium"
                  style={labelStyles}
                >
                  Style
                </label>
                <div className="grid grid-cols-2 gap-2">
                  {PERSONAS.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => setPersona(p.id)}
                      className="text-left px-3 py-3 rounded-lg transition-colors"
                      style={{
                        background: persona === p.id
                          ? 'color-mix(in srgb, var(--accent) 14%, var(--bg-primary))'
                          : 'var(--bg-tertiary)',
                        border: persona === p.id ? '1.5px solid var(--accent)' : '1px solid var(--border)',
                      }}
                    >
                      <div className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
                        {p.name}
                      </div>
                      <div className="text-xs mt-0.5" style={mutedStyles}>
                        {p.description}
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {step === 'vision' && (
            <div className="space-y-4">
              <div>
                <div className="text-base font-semibold mb-1" style={{ color: 'var(--text-primary)' }}>
                  See images locally?
                </div>
                <p className="text-sm leading-snug" style={mutedStyles}>
                  Optional ~{formatDownloadGb(visionStatus?.model?.approx_download_bytes)} local
                  decoder for chat models that can&apos;t see images. Runs on this PC only.
                </p>
              </div>
              <label
                className="flex items-start gap-3 px-4 py-3.5 rounded-lg cursor-pointer text-left"
                style={{
                  background: enableVision
                    ? 'color-mix(in srgb, var(--accent) 12%, var(--bg-primary))'
                    : 'var(--bg-tertiary)',
                  border: enableVision ? '1.5px solid var(--accent)' : '1px solid var(--border)',
                }}
              >
                <input
                  type="checkbox"
                  checked={enableVision}
                  onChange={(e) => setEnableVision(e.target.checked)}
                  className="mt-1 w-4 h-4"
                  style={{ accentColor: 'var(--accent)' }}
                />
                <span>
                  <span className="block text-base font-semibold" style={{ color: 'var(--text-primary)' }}>
                    {visionStatus?.installed ? 'Keep visual decoder on' : 'Install visual decoder'}
                  </span>
                  <span className="block text-sm mt-0.5" style={mutedStyles}>
                    {visionStatus?.installed
                      ? 'Already on this machine.'
                      : 'Download starts after setup — progress in the bottom status dock.'}
                  </span>
                </span>
              </label>
              {visionInstallMsg ? (
                <div className="text-sm" style={{ color: 'var(--accent)' }}>
                  {visionInstallMsg}
                </div>
              ) : null}
              {visionInstalling ? (
                <div className="text-sm" style={{ color: 'var(--accent)' }}>
                  Install running in background…
                </div>
              ) : null}
            </div>
          )}

          {step === 'finish' && (
            <div className="space-y-5">
              <div className="text-center space-y-2">
                <div className="text-2xl font-semibold" style={{ color: 'var(--accent)' }}>
                  You&apos;re ready
                </div>
                <p className="text-sm" style={mutedStyles}>
                  Enter to send · F1 for help
                  {enableVision && !visionStatus?.installed
                    ? ' · Vision download shows bottom-left'
                    : ''}
                </p>
              </div>
              <label
                className="flex items-start gap-3 px-4 py-3 rounded-lg cursor-pointer text-left"
                style={{
                  background: 'var(--bg-tertiary)',
                  border: '1px solid var(--border)',
                }}
              >
                <input
                  type="checkbox"
                  checked={launchAtLogin}
                  onChange={(e) => setLaunchAtLogin(e.target.checked)}
                  className="mt-1 w-4 h-4"
                  style={{ accentColor: 'var(--accent)' }}
                />
                <span>
                  <span className="block text-base font-medium" style={{ color: 'var(--text-primary)' }}>
                    Start with Windows
                  </span>
                  <span className="block text-sm" style={mutedStyles}>
                    Optional tray + warm server
                  </span>
                </span>
              </label>
              <button
                onClick={handleFinish}
                disabled={saving}
                className="w-full py-3 rounded-lg text-base font-semibold transition-colors"
                style={{
                  background: saving ? 'var(--bg-tertiary)' : 'var(--accent)',
                  color: saving ? 'var(--text-muted)' : '#fff',
                  cursor: saving ? 'not-allowed' : 'pointer',
                }}
              >
                {saving ? 'Saving…' : 'Start Chatting'}
              </button>
            </div>
          )}

          {error && (
            <div
              className="px-3 py-2.5 rounded-lg text-sm"
              style={{
                background: 'var(--error-bg, rgba(239,68,68,0.1))',
                color: 'var(--error)',
                border: '1px solid var(--error)',
              }}
            >
              {error}
            </div>
          )}

          {step !== 'welcome' && step !== 'finish' && (
            <div className="space-y-2 pt-1">
              <div className="flex gap-2">
                <button
                  onClick={handleBack}
                  disabled={saving}
                  className="flex-1 py-2.5 rounded-lg text-base font-medium transition-colors"
                  style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)', border: '1px solid var(--border)' }}
                >
                  Back
                </button>
                <button
                  onClick={handleNext}
                  disabled={saving}
                  className="flex-1 py-2.5 rounded-lg text-base font-semibold transition-colors"
                  style={{ background: 'var(--accent)', color: '#fff' }}
                >
                  Next
                </button>
              </div>
              <button
                onClick={handleSkip}
                disabled={saving}
                className="w-full py-2 rounded text-sm transition-colors"
                style={{ background: 'transparent', color: 'var(--text-muted)' }}
                title="Skip remaining setup — won't show again on next launch"
              >
                {saving ? 'Saving…' : 'Skip remaining'}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
