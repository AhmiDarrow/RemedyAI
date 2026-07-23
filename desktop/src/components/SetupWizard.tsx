import { useState, useCallback } from 'react'
import { updateSettings } from '../api/settings'

const PROVIDERS = [
  { id: 'openai', name: 'OpenAI' },
  { id: 'anthropic', name: 'Anthropic' },
  { id: 'google', name: 'Google AI' },
  { id: 'deepseek', name: 'DeepSeek' },
  { id: 'openrouter', name: 'OpenRouter' },
  { id: 'ollama', name: 'Ollama (local)' },
  { id: 'custom', name: 'Custom / OpenAI-compatible' },
] as const

// Keep aligned with backend PROVIDER_CATALOG + SettingsPanel defaults.
const PRESETS = [
  { name: 'OpenAI', provider: 'openai', baseUrl: 'https://api.openai.com/v1', models: ['gpt-4o-mini', 'gpt-4o', 'gpt-4.1-mini', 'o4-mini'] },
  { name: 'Anthropic', provider: 'anthropic', baseUrl: 'https://api.anthropic.com/v1', models: ['claude-3-5-sonnet-latest', 'claude-3-5-haiku-latest', 'claude-3-haiku-20240307'] },
  { name: 'Google AI', provider: 'google', baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai', models: ['gemini-2.5-flash', 'gemini-2.0-flash', 'gemini-1.5-pro'] },
  { name: 'DeepSeek', provider: 'deepseek', baseUrl: 'https://api.deepseek.com/v1', models: ['deepseek-chat', 'deepseek-reasoner'] },
  { name: 'OpenRouter', provider: 'openrouter', baseUrl: 'https://openrouter.ai/api/v1', models: ['openrouter/auto', 'openai/gpt-4o-mini', 'anthropic/claude-3.5-sonnet'] },
  { name: 'Ollama', provider: 'ollama', baseUrl: 'http://127.0.0.1:11434/v1', models: ['llama3.2', 'qwen2.5', 'codellama'] },
  { name: 'Custom', provider: 'custom', baseUrl: 'http://127.0.0.1:5001/api/v1', models: ['default'] },
]

const PERSONAS = [
  { id: 'balanced', name: 'Balanced', description: 'Helpful and adaptable to the task' },
  { id: 'efficient', name: 'Efficient', description: 'Concise, code-first, minimal explanation' },
  { id: 'detailed', name: 'Detailed', description: 'Thorough explanations with context' },
  { id: 'playful', name: 'Playful', description: 'Casual tone with light humor' },
] as const

function getPreset(provider: string) {
  return PRESETS.find((p) => p.provider === provider) ?? PRESETS[0]
}

interface SetupWizardProps {
  open: boolean
  onComplete: () => void
}

type Step = 'welcome' | 'provider' | 'workspace' | 'persona' | 'finish'
const STEPS: Step[] = ['welcome', 'provider', 'workspace', 'persona', 'finish']

export function SetupWizard({ open, onComplete }: SetupWizardProps) {
  const [step, setStep] = useState<Step>('welcome')
  const [provider, setProvider] = useState('openai')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('gpt-4o-mini')
  const [baseUrl, setBaseUrl] = useState('https://api.openai.com/v1')
  const [projectPath, setProjectPath] = useState('')
  const [persona, setPersona] = useState('balanced')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const stepIndex = STEPS.indexOf(step)

  const handleProviderChange = useCallback(
    (p: string) => {
      setProvider(p)
      const preset = getPreset(p)
      setBaseUrl(preset.baseUrl)
      setModel(preset.models[0])
      setError('')
    },
    [],
  )

  const handleNext = useCallback(() => {
    if (step === 'provider') {
      // Local providers (Ollama, custom / localhost) need no key.
      const isLocal =
        provider === 'ollama' ||
        provider === 'custom' ||
        /^(https?:\/\/)?(127\.0\.0\.1|localhost|\[::1\])/i.test(baseUrl)
      if (!isLocal && !apiKey.trim()) {
        setError('Enter an API key, or choose Ollama / Custom for local models. Use Skip setup to configure later.')
        return
      }
    }
    const idx = STEPS.indexOf(step)
    if (idx < STEPS.length - 1) {
      setStep(STEPS[idx + 1])
      setError('')
    }
  }, [step, provider, apiKey, baseUrl])

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
      await updateSettings({
        llm_provider: provider,
        llm_model: model,
        llm_base_url: baseUrl,
        llm_api_key: apiKey || undefined,
        project_path: projectPath || undefined,
        persona: persona || undefined,
        setup_completed: true,
      })
      onComplete()
    } catch {
      setError('Failed to save settings. Is the server running?')
    } finally {
      setSaving(false)
    }
  }, [apiKey, provider, model, baseUrl, projectPath, persona, onComplete])

  const handleSkip = useCallback(async () => {
    setSaving(true)
    setError('')
    try {
      await updateSettings({ setup_completed: true })
      onComplete()
    } catch {
      onComplete()
    } finally {
      setSaving(false)
    }
  }, [onComplete])

  if (!open) return null

  const preset = getPreset(provider)

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
            Your self-improving AI companion
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
                  Welcome to <strong>Remedy AI</strong> — a self-improving AI assistant that
                  learns, remembers context, and adapts to the way you work.
                </div>
                <div className="text-xs space-y-1" style={mutedStyles}>
                  <p>Powered by skills, memory, and multi-model support</p>
                  <p>Works with OpenAI, Anthropic, Google, DeepSeek, OpenRouter, and Ollama</p>
                </div>
              </div>
              <button
                onClick={handleNext}
                className="w-full py-2.5 rounded text-sm font-medium transition-colors"
                style={{ background: 'var(--accent)', color: '#fff' }}
              >
                Get Started
              </button>
              <button
                onClick={handleSkip}
                className="w-full py-2 rounded text-xs transition-colors"
                style={{ background: 'transparent', color: 'var(--text-muted)' }}
                title="Skip setup and use defaults"
              >
                Skip setup
              </button>
            </div>
          )}

          {step === 'provider' && (
            <>
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
                  {PROVIDERS.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </div>

              <div>
                <label
                  className="block mb-1 text-xs font-medium"
                  style={labelStyles}
                >
                  {provider === 'ollama' ? 'API Key (optional for local)' : 'API Key'}
                </label>
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => {
                    setApiKey(e.target.value)
                    setError('')
                  }}
                  placeholder={provider === 'ollama' ? 'Leave blank for local' : 'sk-...'}
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
                  {preset.models.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
                <input
                  type="text"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder="Or type a custom model name"
                  className="w-full rounded px-3 py-1.5 mt-1 text-xs outline-none"
                  style={inputStyles}
                  onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
                  onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
                />
              </div>

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
                  You're ready!
                </div>
                <div className="text-xs space-y-2" style={mutedStyles}>
                  <p><strong>/help</strong> — Show available commands</p>
                  <p><strong>/memory</strong> — Search what Remedy remembers</p>
                  <p><strong>/skills</strong> — Browse loaded skills</p>
                  <p><strong>/handoff</strong> — Generate a session summary</p>
                </div>
              </div>
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
            <div className="flex gap-2 pt-2">
              <button
                onClick={handleBack}
                className="flex-1 py-2 rounded text-sm font-medium transition-colors"
                style={{ background: 'var(--bg-tertiary)', color: 'var(--text-secondary)', border: '1px solid var(--border)' }}
                onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-primary)')}
                onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--bg-tertiary)')}
              >
                Back
              </button>
              <button
                onClick={handleNext}
                className="flex-1 py-2 rounded text-sm font-medium transition-colors"
                style={{ background: 'var(--accent)', color: '#fff' }}
              >
                Next
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
