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

const PRESETS = [
  { name: 'OpenAI', provider: 'openai', baseUrl: 'https://api.openai.com/v1', models: ['gpt-4o-mini', 'gpt-4o', 'gpt-3.5-turbo'] },
  { name: 'Anthropic', provider: 'anthropic', baseUrl: 'https://api.anthropic.com/v1', models: ['claude-3.5-sonnet', 'claude-3-haiku'] },
  { name: 'Google AI', provider: 'google', baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai', models: ['gemini-2.5-flash', 'gemini-2.0-flash'] },
  { name: 'DeepSeek', provider: 'deepseek', baseUrl: 'https://api.deepseek.com/v1', models: ['deepseek-chat', 'deepseek-reasoner'] },
  { name: 'OpenRouter', provider: 'openrouter', baseUrl: 'https://openrouter.ai/api/v1', models: ['openrouter/auto', 'openai/gpt-4o-mini'] },
  { name: 'Ollama', provider: 'ollama', baseUrl: 'http://localhost:11434/v1', models: ['llama3.2', 'qwen2.5', 'codellama'] },
  { name: 'Custom', provider: 'custom', baseUrl: 'http://127.0.0.1:5001/api/v1', models: ['gpt-4o-mini'] },
]

function getPreset(provider: string) {
  return PRESETS.find((p) => p.provider === provider) ?? PRESETS[0]
}

interface SetupWizardProps {
  open: boolean
  onComplete: () => void
}

export function SetupWizard({ open, onComplete }: SetupWizardProps) {
  const [provider, setProvider] = useState('openai')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('gpt-4o-mini')
  const [baseUrl, setBaseUrl] = useState('https://api.openai.com/v1')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

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

  const handleSave = useCallback(async () => {
    if (!apiKey.trim() && provider !== 'ollama') {
      setError('Please enter your API key to continue, or select Ollama for local models.')
      return
    }
    setSaving(true)
    setError('')
    try {
      await updateSettings({
        llm_provider: provider,
        llm_model: model,
        llm_base_url: baseUrl,
        llm_api_key: apiKey || undefined,
      })
      onComplete()
    } catch {
      setError('Failed to save settings. Is the server running?')
    } finally {
      setSaving(false)
    }
  }, [apiKey, provider, model, baseUrl, onComplete])

  const handleSkip = useCallback(() => {
    onComplete()
  }, [onComplete])

  if (!open) return null

  const preset = getPreset(provider)

  return (
    <div
      className="flex items-center justify-center h-full"
      style={{ background: 'var(--bg-primary)' }}
    >
      <div
        className="rounded-xl shadow-2xl overflow-hidden"
        style={{
          width: 440,
          background: 'var(--bg-secondary)',
          border: '1px solid var(--border)',
        }}
      >
        <div className="px-6 pt-6 pb-4 text-center">
          <div
            className="text-2xl font-bold mb-1"
            style={{ color: 'var(--accent)' }}
          >
            Remedy AI
          </div>
          <div
            className="text-sm mb-3"
            style={{ color: 'var(--text-secondary)' }}
          >
            Welcome! Let&apos;s set up your AI provider.
          </div>
          <div
            className="text-xs"
            style={{ color: 'var(--text-muted)' }}
          >
            You can change these settings at any time from the Settings panel.
          </div>
        </div>

        <div className="px-6 pb-6 space-y-4">
          <section>
            <label
              className="block mb-1 text-xs font-medium"
              style={{ color: 'var(--text-secondary)' }}
            >
              Provider
            </label>
            <select
              value={provider}
              onChange={(e) => handleProviderChange(e.target.value)}
              className="w-full rounded px-3 py-2 text-sm outline-none"
              style={{
                background: 'var(--bg-tertiary)',
                color: 'var(--text-primary)',
                border: '1px solid var(--border)',
              }}
              onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
              onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
            >
              {PROVIDERS.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </section>

          <section>
            <label
              className="block mb-1 text-xs font-medium"
              style={{ color: 'var(--text-secondary)' }}
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
              style={{
                background: 'var(--bg-tertiary)',
                color: 'var(--text-primary)',
                border: '1px solid var(--border)',
              }}
              onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
              onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleSave()
              }}
            />
            {provider === 'ollama' && (
              <div className="mt-1 text-xs" style={{ color: 'var(--text-muted)' }}>
                Make sure Ollama is running locally with your preferred model pulled.
              </div>
            )}
          </section>

          <section>
            <label
              className="block mb-1 text-xs font-medium"
              style={{ color: 'var(--text-secondary)' }}
            >
              Model
            </label>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="w-full rounded px-3 py-2 text-sm outline-none"
              style={{
                background: 'var(--bg-tertiary)',
                color: 'var(--text-primary)',
                border: '1px solid var(--border)',
              }}
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
              style={{
                background: 'var(--bg-tertiary)',
                color: 'var(--text-primary)',
                border: '1px solid var(--border)',
              }}
              onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
              onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
            />
          </section>

          <section>
            <label
              className="block mb-1 text-xs font-medium"
              style={{ color: 'var(--text-secondary)' }}
            >
              Base URL
            </label>
            <input
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              className="w-full rounded px-3 py-2 text-sm outline-none font-mono text-xs"
              style={{
                background: 'var(--bg-tertiary)',
                color: 'var(--text-primary)',
                border: '1px solid var(--border)',
              }}
              onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
              onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
            />
          </section>

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

          <div className="flex gap-2 pt-2">
            <button
              onClick={handleSkip}
              className="flex-1 py-2 rounded text-sm font-medium transition-colors"
              style={{
                background: 'var(--bg-tertiary)',
                color: 'var(--text-secondary)',
                border: '1px solid var(--border)',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--bg-primary)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--bg-tertiary)')}
            >
              Skip for now
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex-1 py-2 rounded text-sm font-medium transition-colors"
              style={{
                background: saving ? 'var(--bg-tertiary)' : 'var(--accent)',
                color: saving ? 'var(--text-muted)' : '#fff',
                cursor: saving ? 'not-allowed' : 'pointer',
              }}
            >
              {saving ? 'Saving...' : 'Get Started'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
