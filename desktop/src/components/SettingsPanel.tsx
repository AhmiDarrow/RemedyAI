import { useState, useEffect, useCallback } from 'react'
import { getSettings, updateSettings, type Settings, type SettingsUpdate } from '../api/settings'
import type { ThemeId } from '../themes'
import { THEME_LIST } from '../themes'

interface SettingsPanelProps {
  open: boolean
  onClose: () => void
  themeId: ThemeId
  onThemeChange: (id: ThemeId) => void
}

const PROVIDERS = [
  { id: 'openai', name: 'OpenAI' },
  { id: 'anthropic', name: 'Anthropic' },
  { id: 'google', name: 'Google AI' },
  { id: 'deepseek', name: 'DeepSeek' },
  { id: 'openrouter', name: 'OpenRouter' },
  { id: 'ollama', name: 'Ollama' },
  { id: 'custom', name: 'Custom / KoboldCPP' },
]

export function SettingsPanel({ open, onClose, themeId, onThemeChange }: SettingsPanelProps) {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const [provider, setProvider] = useState('openai')
  const [model, setModel] = useState('gpt-4o-mini')
  const [baseUrl, setBaseUrl] = useState('https://api.openai.com/v1')
  const [apiKey, setApiKey] = useState('')
  const [apiKeySet, setApiKeySet] = useState(false)
  const [projectPath, setProjectPath] = useState('.')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const s = await getSettings()
      setSettings(s)
      setProvider(s.llm_provider || 'openai')
      setModel(s.llm_model || 'gpt-4o-mini')
      setBaseUrl(s.llm_base_url || 'https://api.openai.com/v1')
      setApiKeySet(s.llm_api_key_set)
      setProjectPath(s.project_path || '.')
      setApiKey('')
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
    }
  }, [open, load])

  const handleSave = async () => {
    setSaving(true)
    setSaved(false)
    const updates: SettingsUpdate = {
      llm_provider: provider,
      llm_model: model,
      llm_base_url: baseUrl,
      project_path: projectPath,
    }
    if (apiKey) {
      updates.llm_api_key = apiKey
    }
    try {
      await updateSettings(updates)
      setSaved(true)
      setApiKey('')
      setApiKeySet(apiKey ? true : apiKeySet)
    } catch {
      // ignore
    } finally {
      setSaving(false)
    }
  }

  const handleProviderChange = (p: string) => {
    setProvider(p)
    if (p === 'ollama') setBaseUrl('http://127.0.0.1:11434/v1')
    else if (p === 'custom') setBaseUrl('http://127.0.0.1:5001/api/v1')
    else if (p === 'openai') setBaseUrl('https://api.openai.com/v1')
    else if (p === 'anthropic') setBaseUrl('https://api.anthropic.com/v1')
    else if (p === 'deepseek') setBaseUrl('https://api.deepseek.com/v1')
    else if (p === 'openrouter') setBaseUrl('https://openrouter.ai/api/v1')
    else if (p === 'google') setBaseUrl('https://generativelanguage.googleapis.com/v1beta/openai')
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
                {PROVIDERS.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>

              <Field label="Base URL" value={baseUrl} onChange={setBaseUrl} />
              <Field label="Model" value={model} onChange={setModel} />
              <Field
                label={apiKeySet ? 'API Key (set - change?)' : 'API Key'}
                value={apiKey}
                onChange={setApiKey}
                placeholder={apiKeySet ? '(leave blank to keep current)' : 'sk-...'}
                password
              />
            </section>

            {/* Project */}
            <section>
              <div className="font-semibold mb-2" style={{ color: 'var(--text-primary)', fontSize: '0.75rem' }}>
                Project
              </div>
              <Field label="Project path" value={projectPath} onChange={setProjectPath} placeholder="." />
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
                    <span
                      className="inline-block w-3 h-3 rounded-full border"
                      style={{ background: t.colors['--accent'], borderColor: 'var(--border)' }}
                    />
                    <span>{t.name}</span>
                    {t.id === themeId && (
                      <svg width="12" height="12" viewBox="0 0 12 12" fill="none" className="ml-auto">
                        <path d="M2 6l3 3 5-5" stroke="var(--accent)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    )}
                  </button>
                ))}
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
                  <span>v{settings?.version || '0.8.0'}</span>
                </div>
                <div className="flex justify-between">
                  <span style={{ color: 'var(--text-muted)' }}>Config</span>
                  <span style={{ fontFamily: 'monospace', fontSize: '0.65rem' }}>~/.remedy/config.toml</span>
                </div>
              </div>
            </section>
          </>
        )}
      </div>

      {/* Save */}
      <div
        className="px-3 py-2 border-t flex items-center gap-2"
        style={{ borderColor: 'var(--border)' }}
      >
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex-1 py-1.5 rounded text-xs font-medium transition-colors"
          style={{
            background: saving ? 'var(--bg-tertiary)' : 'var(--accent)',
            color: saving ? 'var(--text-muted)' : '#fff',
          }}
        >
          {saving ? 'Saving...' : 'Save Settings'}
        </button>
        {saved && (
          <span className="text-xs" style={{ color: 'var(--success)' }}>
            Saved
          </span>
        )}
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
