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
  const [enableVision, setEnableVision] = useState(true)
  const [visionStatus, setVisionStatus] = useState<VisionStatus | null>(null)
  const [visionInstallMsg, setVisionInstallMsg] = useState('')
  const [visionInstalling, setVisionInstalling] = useState(false)
  const [visionInstallPct, setVisionInstallPct] = useState<number | null>(null)
  const visionPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const finishAbortRef = useRef(false)

  const stepIndex = STEPS.indexOf(step)
  const primaryProviders = useMemo(() => catalog.filter((p) => !p.advanced), [catalog])
  const advancedProviders = useMemo(() => catalog.filter((p) => p.advanced), [catalog])
  /** Free cloud keys only — Demo/Ollama get dedicated cards above. */
  const freeKeyOptions = useMemo(
    () =>
      freeOptions.filter(
        (o) => o.id !== 'demo' && o.id !== 'ollama' && (o.tier === 'free_key' || o.free_tier === 'free_key'),
      ),
    [freeOptions],
  )
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

  const saveSetupCore = useCallback(async () => {
    try {
      const { invoke } = await import('@tauri-apps/api/core')
      await invoke('set_launch_at_login', { enabled: launchAtLogin })
      await invoke('set_desktop_prefs', {
        close_to_tray: true,
        start_in_tray: false,
        skip_quit_server_warning: false,
      })
    } catch {
      /* browser or missing command */
    }
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
      start_in_tray: false,
      close_to_tray: true,
      vision_enabled: enableVision,
      vision_model_id: 'qwen2.5-vl-3b',
    })
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
  ])

  /** Start vision download and poll with live UI; if backgroundOnly, return after start. */
  const runVisionInstall = useCallback(
    async (opts?: { backgroundOnly?: boolean }) => {
      if (!enableVision || visionStatus?.installed) {
        if (enableVision && visionStatus?.installed) {
          setVisionInstallMsg('Local model ready — starts with Remedy.')
          setVisionInstallPct(100)
        }
        return
      }
      setVisionInstalling(true)
      setVisionInstallPct(0)
      setVisionInstallMsg('Downloading local vision model (Qwen2.5-VL 3B)…')
      finishAbortRef.current = false
      try {
        const preferCuda = Boolean(visionStatus?.health?.nvidia_detected)
        const started = await installVision({ prefer_cuda: preferCuda })
        if (
          started.mode === 'already_installed'
          || started.mode === 'local_files'
          || (started.installed && started.ready)
        ) {
          setVisionInstallMsg('Local model ready — starts with Remedy.')
          setVisionInstallPct(100)
          setVisionInstalling(false)
          return
        }
        if (opts?.backgroundOnly) {
          setVisionInstallMsg(
            'Download continues in the background — progress shows in the status bar.',
          )
          setVisionInstalling(false)
          return
        }
        const deadline = Date.now() + 45 * 60 * 1000
        while (Date.now() < deadline && !finishAbortRef.current) {
          await new Promise((r) => setTimeout(r, 1500))
          let vs: VisionStatus | null = null
          try {
            vs = await getVisionStatus()
            setVisionStatus(vs)
          } catch {
            continue
          }
          const phase = (vs.progress?.phase || '').toLowerCase()
          const pct =
            vs.progress?.bytes_total && vs.progress.bytes_total > 0
              ? Math.round(
                  (100 * (vs.progress.bytes_done || 0)) / vs.progress.bytes_total,
                )
              : null
          if (pct != null) {
            setVisionInstallPct(pct)
            setVisionInstallMsg(
              `Downloading local model… ${pct}% — server starts when finished.`,
            )
          } else if (vs.progress?.message) {
            setVisionInstallMsg(vs.progress.message)
          }
          if (vs.ready && vs.installed) {
            setVisionInstallMsg('Local model ready — starts with Remedy.')
            setVisionInstallPct(100)
            break
          }
          if (phase === 'error') {
            setVisionInstallMsg(
              vs.progress?.error
                || 'Download failed — open Settings → Local vision to retry.',
            )
            break
          }
          if (phase === 'cancelled') {
            setVisionInstallMsg(
              'Download cancelled — open Settings → Local vision to resume.',
            )
            break
          }
        }
        if (Date.now() >= deadline) {
          setVisionInstallMsg(
            'Download still running in the background — you can use Remedy; local vision will activate when ready.',
          )
        }
      } catch (ve) {
        console.warn('Local model install start failed', ve)
        setVisionInstallMsg(
          'Could not start download — open Settings → Local vision to retry.',
        )
      } finally {
        setVisionInstalling(false)
      }
    },
    [enableVision, visionStatus?.installed, visionStatus?.health?.nvidia_detected],
  )

  const handleFinish = useCallback(async () => {
    setSaving(true)
    setError('')
    try {
      await saveSetupCore()
      await runVisionInstall()
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
  }, [saveSetupCore, runVisionInstall, onComplete])

  /** Save + start vision in background + enter app immediately. */
  const handleUseAppNow = useCallback(async () => {
    setSaving(true)
    setError('')
    try {
      await saveSetupCore()
      finishAbortRef.current = true
      void runVisionInstall({ backgroundOnly: true })
      onComplete()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      setError(
        msg && msg !== 'Failed to fetch'
          ? `Failed to save settings: ${msg}`
          : 'Failed to save settings. Is the local server running?',
      )
    } finally {
      setSaving(false)
    }
  }, [saveSetupCore, runVisionInstall, onComplete])

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
          <img
            src="/logo.png"
            alt="Remedy"
            draggable={false}
            style={{
              height: 40,
              width: 'auto',
              maxWidth: 240,
              objectFit: 'contain',
              margin: '0 auto 10px',
              display: 'block',
            }}
          />
          <div
            className="text-2xl font-bold tracking-tight mb-1"
            style={{ color: 'var(--text-primary)' }}
          >
            Remedy
          </div>
          <div className="text-sm" style={mutedStyles}>
            Your local AI partner
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
              {/* Clean free path — three choices max, not a chip flea market */}
              <div className="space-y-2">
                <div className="text-sm font-medium" style={labelStyles}>
                  Start free
                </div>
                <div className="grid gap-2">
                  <button
                    type="button"
                    onClick={() => handleProviderChange('demo')}
                    className="w-full text-left rounded-xl px-3.5 py-3 transition-colors"
                    style={{
                      border:
                        provider === 'demo'
                          ? '1.5px solid var(--accent)'
                          : '1px solid var(--border)',
                      background:
                        provider === 'demo'
                          ? 'color-mix(in srgb, var(--accent) 12%, var(--bg-primary))'
                          : 'var(--bg-tertiary)',
                    }}
                  >
                    <div className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
                      Demo · no signup
                    </div>
                    <div className="text-xs mt-0.5" style={mutedStyles}>
                      Chat immediately on a rate-limited free gateway. Switch later in Settings.
                    </div>
                  </button>
                  <button
                    type="button"
                    onClick={() => handleProviderChange('ollama')}
                    className="w-full text-left rounded-xl px-3.5 py-3 transition-colors"
                    style={{
                      border:
                        provider === 'ollama'
                          ? '1.5px solid var(--accent)'
                          : '1px solid var(--border)',
                      background:
                        provider === 'ollama'
                          ? 'color-mix(in srgb, var(--accent) 12%, var(--bg-primary))'
                          : 'var(--bg-tertiary)',
                    }}
                  >
                    <div className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
                      Ollama · free & private on this PC
                    </div>
                    <div className="text-xs mt-0.5" style={mutedStyles}>
                      {ollamaHint
                        || 'Requires Ollama installed locally. No cloud API key.'}
                    </div>
                  </button>
                </div>
                {freeKeyOptions.length > 0 && (
                  <div className="pt-1">
                    <label className="block mb-1 text-xs font-medium" style={mutedStyles}>
                      Or a free cloud key (optional)
                    </label>
                    <select
                      value={freeKeyOptions.some((o) => o.id === provider) ? provider : ''}
                      onChange={(e) => {
                        const id = e.target.value
                        if (id) handleProviderChange(id)
                      }}
                      className="w-full rounded-lg px-3 py-2 text-sm outline-none"
                      style={inputStyles}
                      onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
                      onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
                    >
                      <option value="">Choose provider…</option>
                      {freeKeyOptions.map((opt) => (
                        <option key={opt.id} value={opt.id}>
                          {opt.title || opt.name}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
              </div>

              <div>
                <label
                  className="block mb-1.5 text-sm font-medium"
                  style={labelStyles}
                >
                  Or pick a paid / full provider
                </label>
                <select
                  value={
                    provider === 'demo' || provider === 'ollama'
                      || freeKeyOptions.some((o) => o.id === provider)
                      ? provider
                      : provider
                  }
                  onChange={(e) => handleProviderChange(e.target.value)}
                  className="w-full rounded-lg px-3 py-2.5 text-base outline-none"
                  style={inputStyles}
                  onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
                  onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
                >
                  {primaryProviders.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
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
                  Demo is rate-limited and not private. Prefer Ollama or your own key for real work.
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
                  Local vision
                </div>
                <p className="text-sm leading-snug" style={mutedStyles}>
                  One-time download of pinned{' '}
                  <strong style={{ color: 'var(--text-secondary)' }}>Qwen2.5-VL 3B</strong> (~
                  {formatDownloadGb(visionStatus?.model?.approx_download_bytes)}) for screenshots and
                  OCR. Same files on every PC. After install, the local server{' '}
                  <strong style={{ color: 'var(--text-secondary)' }}>starts with Remedy</strong>.
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
                    {visionStatus?.installed
                      ? 'Keep local model on (starts with Remedy)'
                      : 'Install local model on finish'}
                  </span>
                  <span className="block text-sm mt-0.5" style={mutedStyles}>
                    {visionStatus?.installed
                      ? 'Already on this machine — auto-starts when Remedy launches.'
                      : 'Download starts after setup · progress in the status dock · then auto-starts every launch.'}
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
                  Starting download…
                </div>
              ) : null}
            </div>
          )}

          {step === 'finish' && (
            <div className="space-y-5">
              {(saving || visionInstalling) && enableVision && !visionStatus?.installed ? (
                <div className="space-y-4">
                  <div className="text-center space-y-1">
                    <div className="text-xl font-semibold" style={{ color: 'var(--accent)' }}>
                      {visionInstalling ? 'Installing local model' : 'Saving setup…'}
                    </div>
                    <p className="text-sm" style={mutedStyles}>
                      Qwen2.5-VL downloads once — progress stays visible (not frozen).
                    </p>
                  </div>
                  <div
                    className="h-2.5 rounded-full overflow-hidden"
                    style={{ background: 'var(--bg-tertiary)' }}
                  >
                    <div
                      className="h-full rounded-full transition-all duration-300"
                      style={{
                        width: `${Math.min(100, Math.max(2, visionInstallPct ?? (visionInstalling ? 8 : 2)))}%`,
                        background: 'var(--accent)',
                      }}
                    />
                  </div>
                  <p className="text-sm text-center" style={{ color: 'var(--text-secondary)' }}>
                    {visionInstallMsg
                      || (saving && !visionInstalling ? 'Saving settings…' : 'Starting download…')}
                    {visionInstallPct != null ? ` · ${visionInstallPct}%` : ''}
                  </p>
                  <button
                    type="button"
                    onClick={() => void handleUseAppNow()}
                    disabled={saving && !visionInstalling}
                    className="w-full py-2.5 rounded-lg text-sm font-medium"
                    style={{
                      background: 'var(--bg-tertiary)',
                      color: 'var(--text-primary)',
                      border: '1px solid var(--border)',
                    }}
                  >
                    Use app while downloading
                  </button>
                </div>
              ) : (
                <>
                  <div className="text-center space-y-2">
                    <div className="text-2xl font-semibold" style={{ color: 'var(--accent)' }}>
                      You&apos;re ready
                    </div>
                    <p className="text-sm" style={mutedStyles}>
                      Enter to send · F1 for help
                      {enableVision
                        ? visionStatus?.installed
                          ? ' · Local model starts with Remedy'
                          : ' · Local model download shows progress on finish'
                        : ''}
                    </p>
                  </div>
                  {visionInstallMsg ? (
                    <p className="text-sm text-center" style={{ color: 'var(--accent)' }}>
                      {visionInstallMsg}
                    </p>
                  ) : null}
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
                    onClick={() => void handleFinish()}
                    disabled={saving}
                    className="w-full py-3 rounded-lg text-base font-semibold transition-colors"
                    style={{
                      background: saving ? 'var(--bg-tertiary)' : 'var(--accent)',
                      color: saving ? 'var(--text-muted)' : '#fff',
                      cursor: saving ? 'not-allowed' : 'pointer',
                    }}
                  >
                    {saving ? 'Working…' : 'Start Chatting'}
                  </button>
                  {enableVision && !visionStatus?.installed ? (
                    <button
                      type="button"
                      onClick={() => void handleUseAppNow()}
                      disabled={saving}
                      className="w-full py-2 rounded-lg text-sm"
                      style={{ color: 'var(--text-muted)' }}
                    >
                      Use app now — download model in background
                    </button>
                  ) : null}
                </>
              )}
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
