import { useState, useEffect, useCallback } from 'react'
import { getSettings, updateSettings, type Settings, type SettingsUpdate } from '../api/settings'
import type { ThemeId } from '../themes'
import type { UpdateInfo } from '../api/updates'
import { THEME_LIST } from '../themes'
import type { ModelInfo } from '../App'

interface SettingsPanelProps {
  open: boolean
  onClose: () => void
  themeId: ThemeId
  onThemeChange: (id: ThemeId) => void
  updateInfo: UpdateInfo | null
  checkingUpdates: boolean
  onCheckUpdates: () => void
  /** Opens the Ollama-style download → install → relaunch screen. */
  onInstallUpdate?: () => void
  models: ModelInfo[]
  onSettingsSaved?: () => void
}

const PROVIDERS = [
  { id: 'openai', name: 'OpenAI', baseUrl: 'https://api.openai.com/v1', defaultModel: 'gpt-4o-mini' },
  { id: 'anthropic', name: 'Anthropic', baseUrl: 'https://api.anthropic.com/v1', defaultModel: 'claude-3-5-sonnet-latest' },
  { id: 'google', name: 'Google AI', baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai', defaultModel: 'gemini-2.5-flash' },
  { id: 'deepseek', name: 'DeepSeek', baseUrl: 'https://api.deepseek.com/v1', defaultModel: 'deepseek-chat' },
  { id: 'openrouter', name: 'OpenRouter', baseUrl: 'https://openrouter.ai/api/v1', defaultModel: 'openrouter/auto' },
  { id: 'ollama', name: 'Ollama', baseUrl: 'http://127.0.0.1:11434/v1', defaultModel: 'llama3.2' },
  { id: 'custom', name: 'Custom / KoboldCPP', baseUrl: 'http://127.0.0.1:5001/api/v1', defaultModel: 'default' },
] as const

export function SettingsPanel({
  open,
  onClose,
  themeId,
  onThemeChange,
  updateInfo,
  checkingUpdates,
  onCheckUpdates,
  onInstallUpdate,
  models,
  onSettingsSaved,
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

  // Only show models for the selected provider (backend also filters).
  const providerModels = models.filter(
    (m) => !m.provider || m.provider === provider || provider === 'openrouter' || provider === 'custom',
  )

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
      setErrorMessage('')
    }
  }, [open, load])

  const handleSave = async () => {
    setSaving(true)
    setSaved(false)
    setErrorMessage('')
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
      const result = await updateSettings(updates)
      // Server may normalize provider/model/url — reflect that immediately.
      if (result && typeof result === 'object') {
        const r = result as SettingsUpdate & { llm_provider?: string; llm_model?: string; llm_base_url?: string }
        if (r.llm_provider) setProvider(r.llm_provider)
        if (r.llm_model) setModel(r.llm_model)
        if (r.llm_base_url) setBaseUrl(r.llm_base_url)
      }
      setSaved(true)
      setApiKey('')
      setApiKeySet(apiKey ? true : apiKeySet)
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
    const preset = PROVIDERS.find((x) => x.id === p)
    if (preset) {
      setBaseUrl(preset.baseUrl)
      setModel(preset.defaultModel)
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
                {PROVIDERS.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>

              <Field label="Base URL" value={baseUrl} onChange={setBaseUrl} />
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
                Models are scoped to <strong>{provider}</strong>. Discovery uses the Base URL above.
              </div>
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
                Project workspace
              </div>
              <Field
                label="Default project folder"
                value={projectPath}
                onChange={setProjectPath}
                placeholder="e.g. C:\Users\You\Projects\MyApp"
              />
              <div className="text-[10px] leading-snug" style={{ color: 'var(--text-muted)' }}>
                New chat sessions use this as the agent working directory (file tools, shell cwd, and @file search),
                like opening a folder in OpenCode.
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
            {saving ? 'Saving...' : 'Save Settings'}
          </button>
          {saved && !errorMessage && (
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
