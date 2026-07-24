import { useState, useCallback, useEffect, useRef, useMemo } from 'react'
import { getSettings, updateSettings } from '../api/settings'
import {
  startXaiLogin,
  pollXaiLogin,
  openExternalUrl,
} from '../api/auth'
import {
  listProviders,
  detectOllama,
  FALLBACK_PROVIDERS,
  type ProviderInfo,
} from '../api/providers'

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

type Step = 'welcome' | 'provider' | 'workspace' | 'persona' | 'finish'
const STEPS: Step[] = ['welcome', 'provider', 'workspace', 'persona', 'finish']

export function SetupWizard({ open, onComplete }: SetupWizardProps) {
  const [step, setStep] = useState<Step>('welcome')
  const [catalog, setCatalog] = useState<ProviderInfo[]>(FALLBACK_PROVIDERS)
  const [provider, setProvider] = useState('openai')
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

  // Load catalog + env bootstrap + Ollama detect when wizard opens.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    ;(async () => {
      const providers = await listProviders()
      if (cancelled) return
      setCatalog(providers)
      try {
        const s = await getSettings()
        if (cancelled) return
        if (s.llm_provider) {
          setProvider(s.llm_provider)
          const meta = providers.find((p) => p.id === s.llm_provider)
          if (meta?.advanced) setShowAdvanced(true)
          if (s.llm_model) setModel(s.llm_model)
          if (s.llm_base_url) setBaseUrl(s.llm_base_url)
        }
      } catch {
        // offline
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
      // Local providers (Ollama, custom / localhost) need no key.
      const isLocal =
        provider === 'ollama' ||
        provider === 'custom' ||
        /^(https?:\/\/)?(127\.0\.0\.1|localhost|\[::1\])/i.test(baseUrl)
      const xaiOk = provider === 'xai' && (xaiConnected || !!apiKey.trim())
      if (!isLocal && !apiKey.trim() && !xaiOk) {
        setError(
          provider === 'xai'
            ? 'Sign in with xAI or enter an API key. Use Skip setup to configure later.'
            : 'Enter an API key, or choose Ollama for local models. Use Skip setup to configure later.',
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
      })
      onComplete()
    } catch {
      setError('Failed to save settings. Is the server running?')
    } finally {
      setSaving(false)
    }
  }, [apiKey, provider, model, baseUrl, projectPath, persona, userName, launchAtLogin, onComplete])

  const handleSkip = useCallback(async () => {
    // Mark setup done so the wizard never blocks launch again.
    // User can configure the provider later in Settings.
    setSaving(true)
    setError('')
    try {
      await updateSettings({ setup_completed: true })
      onComplete()
    } catch {
      // Still enter the app if the server briefly fails — avoid lockout.
      onComplete()
    } finally {
      setSaving(false)
    }
  }, [onComplete])

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

  return (
    <div
      className="flex items-center justify-center h-full"
      style={{ background: 'var(--bg-primary)' }}
    >
      <div
        className="rounded-xl shadow-2xl overflow-hidden"
        style={{ width: 480, ...cardStyles }}
      >
        <div className="px-6 pt-6 pb-3 text-center">
          <div
            className="text-2xl font-bold mb-1"
            style={{ color: 'var(--accent)' }}
          >
            Remedy AI
          </div>
          <div className="text-xs" style={mutedStyles}>
            Self-improving software coding agent
          </div>
        </div>

        <div className="px-6 pb-2">
          <div
            className="h-1 rounded-full overflow-hidden"
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
            className="flex justify-between mt-1.5 text-xs"
            style={mutedStyles}
          >
            {STEPS.map((s, i) => (
              <span
                key={s}
                className={i <= stepIndex ? 'font-medium' : ''}
                style={i <= stepIndex ? { color: 'var(--accent)' } : undefined}
              >
                {s === 'welcome' && 'Welcome'}
                {s === 'provider' && 'Provider'}
                {s === 'workspace' && 'Workspace'}
                {s === 'persona' && 'Persona'}
                {s === 'finish' && 'Ready'}
              </span>
            ))}
          </div>
        </div>

        <div className="px-6 pb-6 pt-3 space-y-4">

          {step === 'welcome' && (
            <div className="space-y-4">
              <div className="text-center space-y-3">
                <div className="text-sm" style={{ color: 'var(--text-primary)' }}>
                  Welcome to <strong>Remedy AI</strong> — a software coding agent for
                  projects and tools. Configure your LLM provider before chat starts.
                </div>
                <div className="text-xs space-y-1" style={mutedStyles}>
                  <p>Skills, memory, and multi-model support for engineering work</p>
                  <p>OpenAI, Anthropic, Google, DeepSeek, xAI, Groq, Mistral, OpenRouter, Ollama</p>
                  <p>Not a medical or clinical product — you can skip and set this later in Settings.</p>
                </div>
              </div>
              <button
                onClick={handleNext}
                disabled={saving}
                className="w-full py-2.5 rounded text-sm font-medium transition-colors"
                style={{ background: 'var(--accent)', color: '#fff' }}
              >
                Get Started
              </button>
              <button
                onClick={handleSkip}
                disabled={saving}
                className="w-full py-2 rounded text-xs transition-colors"
                style={{ background: 'transparent', color: 'var(--text-muted)' }}
                title="Skip setup for now — won't show again on next launch"
              >
                {saving ? 'Saving…' : 'Skip setup (configure later)'}
              </button>
            </div>
          )}

          {step === 'provider' && (
            <>
              <div className="text-xs" style={mutedStyles}>
                Connect a provider now so chat is not stuck in fallback mode.
                xAI supports Sign in with account; Ollama / local need no key.
              </div>
              {ollamaHint && (
                <div className="text-xs rounded px-2 py-1.5" style={{ ...mutedStyles, border: '1px solid var(--border)' }}>
                  {ollamaHint}
                </div>
              )}
              <div>
                <label
                  className="block mb-1 text-xs font-medium"
                  style={labelStyles}
                >
                  Provider
                </label>
                <select
                  value={provider}
                  onChange={(e) => handleProviderChange(e.target.value)}
                  className="w-full rounded px-3 py-2 text-sm outline-none"
                  style={inputStyles}
                  onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
                  onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
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
                    className="mt-1 text-xs underline"
                    style={mutedStyles}
                    onClick={() => setShowAdvanced(true)}
                  >
                    Show advanced (custom endpoint)…
                  </button>
                )}
              </div>

              {provider === 'xai' && (
                <div
                  className="rounded-md p-3 space-y-2"
                  style={{ border: '1px solid var(--border)', background: 'var(--bg-tertiary)' }}
                >
                  <div className="text-xs font-medium" style={labelStyles}>
                    Sign in with xAI
                  </div>
                  <div className="text-xs" style={mutedStyles}>
                    Recommended for SuperGrok / X Premium+. Or paste a console API key below.
                  </div>
                  {xaiConnected ? (
                    <div className="text-xs" style={{ color: 'var(--success)' }}>
                      Connected via xAI account
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => void handleXaiSignIn()}
                      disabled={xaiLoginBusy}
                      className="w-full py-2 rounded text-sm font-semibold"
                      style={{
                        background: xaiLoginBusy ? 'var(--bg-secondary)' : 'var(--accent)',
                        color: '#fff',
                      }}
                    >
                      {xaiLoginBusy ? 'Waiting for approval…' : 'Sign in with xAI'}
                    </button>
                  )}
                  {xaiUserCode && (
                    <div className="text-xs" style={labelStyles}>
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
                    <div className="text-xs" style={mutedStyles}>{xaiLoginMsg}</div>
                  )}
                </div>
              )}

              <div>
                <label
                  className="block mb-1 text-xs font-medium"
                  style={labelStyles}
                >
                  {provider === 'ollama'
                    ? 'API Key (optional for local)'
                    : provider === 'xai'
                      ? 'API Key (optional if signed in)'
                      : 'API Key'}
                </label>
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => {
                    setApiKey(e.target.value)
                    setError('')
                  }}
                  placeholder={
                    provider === 'ollama'
                      ? 'Leave blank for local'
                      : provider === 'xai'
                        ? 'xai-… from console.x.ai'
                        : 'sk-...'
                  }
                  className="w-full rounded px-3 py-2 text-sm outline-none"
                  style={inputStyles}
                  onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
                  onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleNext()
                  }}
                />
                {provider === 'ollama' && (
                  <div className="mt-1 text-xs" style={mutedStyles}>
                    Make sure Ollama is running locally with your preferred model pulled.
                  </div>
                )}
              </div>

              <div>
                <label
                  className="block mb-1 text-xs font-medium"
                  style={labelStyles}
                >
                  Model
                </label>
                <select
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  className="w-full rounded px-3 py-2 text-sm outline-none"
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
                    className="w-full rounded px-3 py-1.5 mt-1 text-xs outline-none"
                    style={inputStyles}
                    onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
                    onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
                  />
                )}
              </div>

              {showBaseUrl && (
                <div>
                  <label
                    className="block mb-1 text-xs font-medium"
                    style={labelStyles}
                  >
                    Base URL
                  </label>
                  <input
                    type="text"
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    className="w-full rounded px-3 py-2 text-sm outline-none font-mono text-xs"
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
              <div className="text-sm" style={{ color: 'var(--text-primary)' }}>
                <p>
                  Set a default project folder. The agent uses it as the working directory for tools and
                  shell commands, and for <code style={mutedStyles}>@file</code> / <code style={mutedStyles}>@folder</code> search.
                </p>
              </div>
              <div>
                <label
                  className="block mb-1 text-xs font-medium"
                  style={labelStyles}
                >
                  Default project folder (optional)
                </label>
                <input
                  type="text"
                  value={projectPath}
                  onChange={(e) => setProjectPath(e.target.value)}
                  placeholder="e.g. C:\Users\You\Projects\MyApp or leave empty"
                  className="w-full rounded px-3 py-2 text-sm outline-none"
                  style={inputStyles}
                  onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
                  onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleNext()
                  }}
                />
              </div>
            </div>
          )}

          {step === 'persona' && (
            <div className="space-y-3">
              <div>
                <label className="block mb-1 text-xs font-medium" style={labelStyles}>
                  Your name (what Remedy calls you)
                </label>
                <input
                  value={userName}
                  onChange={(e) => setUserName(e.target.value)}
                  placeholder="e.g. Alex"
                  className="w-full rounded-lg px-3 py-2 text-sm outline-none mb-1"
                  style={{
                    background: 'var(--bg-primary)',
                    border: '1px solid var(--border)',
                    color: 'var(--text-primary)',
                  }}
                />
                <div className="text-[10px] mb-2" style={mutedStyles}>
                  Optional now — you can set this later in Settings.
                </div>
              </div>
              <div>
                <label
                  className="block mb-1 text-xs font-medium"
                  style={labelStyles}
                >
                  Communication style
                </label>
                <div className="space-y-1.5">
                  {PERSONAS.map((p) => (
                    <label
                      key={p.id}
                      className="flex items-center gap-3 px-3 py-2.5 rounded cursor-pointer transition-colors"
                      style={{
                        background: persona === p.id ? 'var(--accent-subtle, rgba(var(--accent-rgb, 99, 102, 241), 0.1))' : 'var(--bg-tertiary)',
                        border: persona === p.id ? '1px solid var(--accent)' : '1px solid var(--border)',
                      }}
                    >
                      <input
                        type="radio"
                        name="persona"
                        value={p.id}
                        checked={persona === p.id}
                        onChange={() => setPersona(p.id)}
                        className="accent-current"
                        style={{ accentColor: 'var(--accent)' }}
                      />
                      <div>
                        <div className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
                          {p.name}
                        </div>
                        <div className="text-xs" style={mutedStyles}>
                          {p.description}
                        </div>
                      </div>
                    </label>
                  ))}
                </div>
              </div>
            </div>
          )}

          {step === 'finish' && (
            <div className="space-y-4">
              <div className="text-center space-y-3">
                <div className="text-xl font-semibold" style={{ color: 'var(--accent)' }}>
                  Your partner is ready
                </div>
                <div className="text-xs space-y-2" style={mutedStyles}>
                  <p><strong>Enter</strong> send · <strong>Shift+Enter</strong> new line</p>
                  <p><strong>↑</strong> previous prompt · <strong>↓</strong> next</p>
                  <p><strong>/help</strong> · <strong>/remember</strong> · <strong>/compact</strong></p>
                  <p><strong>Ctrl+/</strong> — shortcuts anytime</p>
                </div>
              </div>
              <label
                className="flex items-start gap-2 px-3 py-2.5 rounded cursor-pointer text-left"
                style={{
                  background: 'var(--bg-tertiary)',
                  border: '1px solid var(--border)',
                }}
              >
                <input
                  type="checkbox"
                  checked={launchAtLogin}
                  onChange={(e) => setLaunchAtLogin(e.target.checked)}
                  className="mt-0.5"
                  style={{ accentColor: 'var(--accent)' }}
                />
                <span>
                  <span className="block text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
                    Keep Remedy ready (Start with Windows)
                  </span>
                  <span className="block text-xs" style={mutedStyles}>
                    Optional. Launches at login, tray presence, warm local server. Change anytime in Settings.
                  </span>
                </span>
              </label>
              <button
                onClick={handleFinish}
                disabled={saving}
                className="w-full py-2.5 rounded text-sm font-medium transition-colors"
                style={{
                  background: saving ? 'var(--bg-tertiary)' : 'var(--accent)',
                  color: saving ? 'var(--text-muted)' : '#fff',
                  cursor: saving ? 'not-allowed' : 'pointer',
                }}
              >
                {saving ? 'Saving...' : 'Start Chatting'}
              </button>
            </div>
          )}

          {error && (
            <div
              className="px-3 py-2 rounded text-xs"
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
            <div className="space-y-2 pt-2">
              <div className="flex gap-2">
                <button
                  onClick={handleBack}
                  disabled={saving}
                  className="flex-1 py-2 rounded text-sm font-medium transition-colors"
                  style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)', border: '1px solid var(--border)' }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-primary)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--bg-tertiary)')}
                >
                  Back
                </button>
                <button
                  onClick={handleNext}
                  disabled={saving}
                  className="flex-1 py-2 rounded text-sm font-medium transition-colors"
                  style={{ background: 'var(--accent)', color: '#fff' }}
                >
                  Next
                </button>
              </div>
              <button
                onClick={handleSkip}
                disabled={saving}
                className="w-full py-1.5 rounded text-xs transition-colors"
                style={{ background: 'transparent', color: 'var(--text-muted)' }}
                title="Skip remaining setup — won't show again on next launch"
              >
                {saving ? 'Saving…' : 'Skip remaining setup (won\'t ask again)'}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
