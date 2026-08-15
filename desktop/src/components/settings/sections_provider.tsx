/** Settings form sections — provider. */
import type { ReactNode } from 'react'
import { connectReasonLabel, type ConnectedProvider } from '../../api/providers'
import type { SettingsFormProps } from './formTypes'
import { SettingsSection } from '../SettingsSection'
import {
  FormActionButton,
  FormHint,
  FormLabel,
  FormLinkButton,
  FormNotice,
  FormSelect,
  FormToggle,
} from './formUi'
import { Field } from './shared'
import { openExternalUrl } from '../../api/auth'

export function SettingsSections_provider(p: SettingsFormProps): ReactNode {
  const {
    sectionProps,
    provider,
    model,
    setModel,
    baseUrl,
    setBaseUrl,
    apiKey,
    setApiKey,
    apiKeySet,
    catalog,
    showAdvanced,
    setShowAdvanced,
    xaiAuth,
    xaiLoginBusy,
    xaiUserCode,
    xaiVerifyUrl,
    xaiLoginMsg,
    handleXaiSignIn,
    handleXaiLogout,
    connectedList,
    providerSearch,
    setProviderSearch,
    enabledProviders,
    setEnabledProviders,
    enabledModels,
    setEnabledModels,
    catalogExpand,
    setCatalogExpand,
    skillsBudget,
    setSkillsBudget,
    primaryProviders,
    advancedProviders,
    activeMeta,
    showBaseUrl,
    providerModels,
    customName = '',
    setCustomName,
    handleProviderChange,
    onTestConnection,
    testBusy = false,
    testMsg = null,
    testOk = null,
    sleevEnabled = false,
    setSleevEnabled,
    sleevGatewayUrl = '',
    setSleevGatewayUrl,
    sleevAllowRemoteGateway = false,
    setSleevAllowRemoteGateway,
    sleevStatus = null,
  } = p

  const sleevInstalled = Boolean(sleevStatus?.installed)
  const sleevGateway =
    (sleevGatewayUrl || '').trim()
    || String(sleevStatus?.gateway_url || 'http://127.0.0.1:17321')
  const sleevGatewayLooksRemote = (() => {
    const u = (sleevGatewayUrl || '').trim().toLowerCase()
    if (!u) return false
    try {
      const host = new URL(u.includes('://') ? u : `http://${u}`).hostname
      return !['localhost', '127.0.0.1', '0.0.0.0', '::1'].includes(host)
    } catch {
      return false
    }
  })()

  return (
    <>
      <SettingsSection
        {...sectionProps('provider')}
        defaultOpen
      >
        <FormHint>
          Free options: <strong style={{ color: 'var(--text-secondary)' }}>Demo</strong> (no signup),
          Gemini / Groq / OpenRouter / Mistral (free key), or Ollama (local).
        </FormHint>
        <FormLabel>Type</FormLabel>
        <FormSelect value={provider} onChange={handleProviderChange}>
          {primaryProviders.map((p) => (
            <option key={p.id} value={p.id}>
              {p.badge ? `${p.name} · ${p.badge}` : p.name}
            </option>
          ))}
          {showAdvanced && advancedProviders.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </FormSelect>
        {!showAdvanced && advancedProviders.length > 0 && (
          <FormLinkButton onClick={() => setShowAdvanced(true)}>
            Show advanced (custom endpoint)…
          </FormLinkButton>
        )}
        {provider === 'demo' && (
          <FormNotice>
            Demo is guest chat only (Codestral, Gemini Flash Lite, GPT-OSS). Image/video
            and other gateway models are hidden — they need a real key or are not chat.
            Add Gemini/Groq free key or Ollama for serious free use.
          </FormNotice>
        )}
        {activeMeta?.key_docs_url && provider !== 'demo' && (
          <FormLinkButton
            accent
            onClick={() => void openExternalUrl(String(activeMeta.key_docs_url))}
          >
            {provider === 'ollama' ? 'Download Ollama…' : 'Get free API key / docs…'}
          </FormLinkButton>
        )}

        {provider === 'custom' && (
          <Field
            label="Name"
            value={customName}
            onChange={(v) => setCustomName?.(v)}
            placeholder="e.g. LM Studio / llama.cpp"
          />
        )}
        {showBaseUrl && (
          <Field label="Base URL" value={baseUrl} onChange={setBaseUrl} />
        )}
        <FormLabel>Model</FormLabel>
        <FormSelect value={model} onChange={setModel}>
          {providerModels.length === 0 && <option value={model}>{model}</option>}
          {providerModels.every((m) => m.id !== model) && model && (
            <option value={model}>{model} (current)</option>
          )}
          {providerModels.map((m) => (
            <option key={m.id} value={m.id}>{m.name}</option>
          ))}
        </FormSelect>
        <FormHint>
          Models for <strong>{activeMeta?.name || provider}</strong>
          {showBaseUrl ? ' · custom base URL enabled' : ''}.
          {(() => {
            const row = connectedList.find((x) => x.id === provider)
            if (!row) return ''
            return ` · ${connectReasonLabel(row.connect_reason, row.connected)}`
          })()}
        </FormHint>

        {provider === 'xai' && (
          <div className="mb-2 p-2.5 rounded-lg space-y-2 ui-surface" style={{ boxShadow: 'none' }}>
            <div className="font-medium" style={{ color: 'var(--text-primary)' }}>
              Sign in with xAI
            </div>
            <FormHint>
              Use your SuperGrok / X Premium+ account (recommended), or a console API key below.
            </FormHint>
            {xaiAuth?.connected ? (
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px]" style={{ color: 'var(--success)' }}>
                  Connected ({xaiAuth.auth_method === 'oauth' ? 'OAuth' : 'API key'})
                </span>
                <FormActionButton onClick={() => void handleXaiLogout()}>
                  Sign out
                </FormActionButton>
              </div>
            ) : (
              <FormActionButton
                variant="primary"
                className="w-full"
                disabled={xaiLoginBusy}
                onClick={() => void handleXaiSignIn()}
              >
                {xaiLoginBusy ? 'Waiting for approval…' : 'Sign in with xAI'}
              </FormActionButton>
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
              <FormHint>
                {xaiLoginMsg}
              </FormHint>
            )}
          </div>
        )}

        {provider !== 'demo' && provider !== 'ollama' && provider !== 'rmb' && (
          <>
            <Field
              label={
                provider === 'xai'
                  ? apiKeySet
                    ? 'API key (optional — change?)'
                    : 'API key (optional)'
                  : apiKeySet
                    ? 'API Key (set — change?)'
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
                    : provider === 'deepseek'
                      ? 'sk-… from platform.deepseek.com'
                      : 'sk-…'
              }
              password
            />
            {!apiKeySet && !apiKey.trim() && provider !== 'xai' && (
              <FormNotice>
                This provider needs an API key before chat will work. Paste it,
                Test, then Save.
              </FormNotice>
            )}
            {onTestConnection && (
              <div className="flex items-center gap-2 mb-2">
                <FormActionButton
                  variant="primary"
                  disabled={testBusy}
                  onClick={() => onTestConnection()}
                >
                  {testBusy ? 'Testing…' : 'Test connection'}
                </FormActionButton>
                {testMsg && (
                  <span
                    className="text-[11px]"
                    style={{
                      color: testOk
                        ? 'var(--success)'
                        : testOk === false
                          ? 'var(--error, #ef4444)'
                          : 'var(--text-secondary)',
                    }}
                  >
                    {testMsg}
                  </span>
                )}
              </div>
            )}
          </>
        )}
        {provider === 'ollama' && (
          <div className="flex items-center gap-2 mb-2">
            {onTestConnection && (
              <FormActionButton
                disabled={testBusy}
                onClick={() => onTestConnection()}
              >
                {testBusy ? 'Checking…' : 'Check Ollama'}
              </FormActionButton>
            )}
            {testMsg && (
              <span
                className="text-[11px]"
                style={{ color: testOk ? 'var(--success)' : 'var(--text-secondary)' }}
              >
                {testMsg}
              </span>
            )}
          </div>
        )}

        {/* Sleev — local context-compression gateway (saves provider tokens) */}
        <div
          className="mt-3 p-2 rounded space-y-2"
          style={{ border: '1px solid var(--border)', background: 'var(--bg-tertiary)' }}
        >
          <div className="font-medium" style={{ color: 'var(--text-primary)' }}>
            Sleev · save tokens
          </div>
          <FormHint>
            Routes cloud chat (xAI, DeepSeek, OpenAI, …) through the local{' '}
            <strong style={{ color: 'var(--text-secondary)' }}>Sleev</strong> gateway so
            long sessions compress stale context before it hits your provider bill.
            Same API keys and models — Ollama / RMB / Demo stay direct.
          </FormHint>
          <FormToggle
            label={
              sleevEnabled
                ? 'Sleev routing on'
                : 'Route cloud providers via Sleev'
            }
            checked={Boolean(sleevEnabled)}
            onChange={(on) => setSleevEnabled?.(on)}
          />
          <FormHint>
            {sleevInstalled
              ? `Sleev detected · gateway ${sleevGateway}`
              : 'Sleev not detected on this machine. Install: npm install -g sleev · then run sleev'}
            {sleevStatus?.account_label
              ? ` · signed in as ${sleevStatus.account_label}`
              : ''}
          </FormHint>
          {showAdvanced && setSleevGatewayUrl && (
            <>
              <Field
                label="Sleev gateway URL (optional)"
                value={sleevGatewayUrl}
                onChange={setSleevGatewayUrl}
                placeholder="http://127.0.0.1:17321"
              />
              <FormHint>
                Loopback only by default (127.0.0.1 / localhost). A LAN or remote
                URL forwards your provider API keys to that host — enable the
                toggle below only if you trust it.
              </FormHint>
              {setSleevAllowRemoteGateway && (
                <FormToggle
                  label={
                    sleevAllowRemoteGateway
                      ? 'Allow remote Sleev gateway (API keys leave this PC)'
                      : 'Allow non-loopback Sleev gateway…'
                  }
                  checked={Boolean(sleevAllowRemoteGateway)}
                  onChange={(on) => setSleevAllowRemoteGateway(on)}
                />
              )}
              {sleevGatewayLooksRemote && !sleevAllowRemoteGateway && (
                <FormNotice>
                  Gateway host is not loopback. Save will be refused until you
                  enable “Allow non-loopback Sleev gateway”.
                </FormNotice>
              )}
            </>
          )}
          <FormLinkButton
            accent
            onClick={() => void openExternalUrl('https://sleev.ai/docs/quickstart')}
          >
            Sleev docs…
          </FormLinkButton>
        </div>
      </SettingsSection>

      {/* Provider catalog — enable for main-screen picker */}
      <SettingsSection
        {...sectionProps('provider-catalog')}
      >
        <FormHint>
          Connected providers with a key, OAuth, Demo, or a live local host appear
          in the status bar. Click a row to switch Type and add a key. Disable to
          hide without deleting credentials.
        </FormHint>
        <input
          type="search"
          value={providerSearch}
          onChange={(e) => setProviderSearch(e.target.value)}
          placeholder="Search providers…"
          className="ui-input mb-2"
        />
        <div className="max-h-48 overflow-y-auto space-y-1">
          {(connectedList.length ? connectedList : catalog)
            .filter((p) => {
              const q = providerSearch.trim().toLowerCase()
              if (!q) return true
              return (
                p.id.includes(q)
                || p.name.toLowerCase().includes(q)
                || (p.models || []).some(
                  (m) => m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q),
                )
              )
            })
            .map((p) => {
              const conn = 'connected' in p ? Boolean((p as ConnectedProvider).connected) : true
              const isEnabled =
                enabledProviders === null
                  ? true
                  : enabledProviders.includes(p.id)
              const models = p.models || []
              const modelAllow = enabledModels[p.id]
              const expanded = catalogExpand === p.id
              return (
                <div
                  key={p.id}
                  className="rounded px-2 py-1.5"
                  style={{
                    background: 'var(--bg-secondary)',
                    border: `1px solid ${
                      p.id === provider ? 'var(--accent)' : 'var(--border)'
                    }`,
                    opacity: conn ? 1 : 0.65,
                  }}
                >
                  <div className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={isEnabled}
                      onChange={(e) => {
                        const on = e.target.checked
                        setEnabledProviders((prev) => {
                          const base = prev ?? catalog.map((x) => x.id)
                          if (on) return [...new Set([...base, p.id])]
                          return base.filter((id) => id !== p.id)
                        })
                      }}
                    />
                    <button
                      type="button"
                      className="flex-1 min-w-0 text-left"
                      onClick={() => {
                        if (p.id !== provider) handleProviderChange(p.id)
                        setCatalogExpand((cur) => (cur === p.id ? null : p.id))
                      }}
                    >
                      <span className="font-medium">{p.name}</span>
                      <span className="block text-[10px]" style={{ color: 'var(--text-muted)' }}>
                        {connectReasonLabel(
                          'connect_reason' in p
                            ? (p as ConnectedProvider).connect_reason
                            : undefined,
                          conn,
                        )}
                        {' · '}
                        {models.length} models
                        {modelAllow ? ` · ${modelAllow.length} enabled` : ''}
                        {expanded ? ' · hide models' : ' · models'}
                      </span>
                    </button>
                  </div>
                  {expanded && models.length > 0 && (
                    <div className="mt-1.5 ml-5 space-y-0.5 max-h-28 overflow-y-auto">
                      {models.map((m) => {
                        const mid = m.id
                        const checked =
                          !modelAllow || modelAllow.length === 0
                            ? true
                            : modelAllow.includes(mid)
                        return (
                          <label
                            key={mid}
                            className="flex items-center gap-1.5 text-[10px]"
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(e) => {
                                const on = e.target.checked
                                setEnabledModels((prev) => {
                                  const allIds = models.map((x) => x.id)
                                  const cur =
                                    prev[p.id] && prev[p.id]!.length > 0
                                      ? [...prev[p.id]!]
                                      : [...allIds]
                                  let next: string[]
                                  if (on) next = [...new Set([...cur, mid])]
                                  else next = cur.filter((id) => id !== mid)
                                  // empty list means "all" — store full set minus unchecked
                                  const out = { ...prev }
                                  if (next.length === allIds.length) {
                                    delete out[p.id]
                                  } else {
                                    out[p.id] = next
                                  }
                                  return out
                                })
                              }}
                            />
                            <span className="truncate">{m.name || mid}</span>
                          </label>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })}
        </div>
        <div className="mt-2 flex items-center gap-2">
          <label className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
            Skills active budget
          </label>
          <input
            type="number"
            min={10}
            max={500}
            value={skillsBudget}
            onChange={(e) => setSkillsBudget(Number(e.target.value) || 80)}
            className="w-16 rounded px-1 py-0.5 text-xs outline-none"
            style={{
              background: 'var(--bg-tertiary)',
              border: '1px solid var(--border)',
              color: 'var(--text-primary)',
            }}
            title="Soft cap for skills in the hot catalog (100+ library scale)"
          />
        </div>
      </SettingsSection>

      {/* You + Agent */}
    </>
  )
}
