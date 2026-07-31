/** Settings form sections — presentation + local UI state wiring via props. */
import type { Dispatch, ReactNode, SetStateAction } from 'react'
import { updateSettings, type Settings } from '../../api/settings'
import {
  activateVisionBundle,
  installVision,
  cancelVisionInstall,
  reinstallVisionRuntime,
  startVisionServer,
  stopVisionServer,
  formatDownloadGb,
  type VisionStatus,
  type NanoSwarmStatus,
} from '../../api/vision'
import type { XaiAuthStatus } from '../../api/auth'
import { openExternalUrl } from '../../api/auth'
import type { ProviderInfo, ConnectedProvider } from '../../api/providers'
import type { ThemeId } from '../../themes'
import type { UpdateInfo } from '../../api/updates'
import { THEME_LIST } from '../../themes'
import { ThemeColorDot } from '../ThemeSwitcher'
import { HOTKEYS } from '../../hotkeys'
import type { ModelInfo } from '../../App'
import type { Density } from '../../utils/chatPrefs'
import type { SettingsMode } from '../../utils/settingsMode'
import {

  TOOL_PROCESS_MODES,
  type ToolProcessMode,
} from '../../utils/toolLabels'
import { SettingsSection } from '../SettingsSection'
import type { SettingsSectionId } from '../../utils/settingsSearch'
import type { MessengerInfo } from '../../api/settings'
import type { MessengerDraftMap } from '../../utils/messengerDrafts'
import { Field, PERSONAS } from './shared'
import { MessengersSection } from './MessengersSection'
import {
  AssistantSection,
  type AssistantDraft,
  type AssistantStatus,
} from './AssistantSection'

export interface SettingsFormProps {
  sectionProps: (id: SettingsSectionId) => {
    id: string
    title: string
    summary: string
    keywords: string
    forceOpen?: boolean
    hidden?: boolean
    onOpenChange?: (open: boolean) => void
  }
  provider: string
  setProvider: Dispatch<SetStateAction<string>>
  model: string
  setModel: Dispatch<SetStateAction<string>>
  baseUrl: string
  setBaseUrl: Dispatch<SetStateAction<string>>
  apiKey: string
  setApiKey: Dispatch<SetStateAction<string>>
  apiKeySet: boolean
  projectPath: string
  setProjectPath: Dispatch<SetStateAction<string>>
  browserHomeUrl: string
  setBrowserHomeUrl: Dispatch<SetStateAction<string>>
  /** Desktop Privacy Shield (Brave adblock) — optional; web UI omits. */
  privacyShield?: {
    enabled: boolean
    ready: boolean
    message: string
    attribution: string
    onToggle: (on: boolean) => void
    onRefresh: () => void
    busy?: boolean
  } | null
  persona: string
  setPersona: Dispatch<SetStateAction<string>>
  userName: string
  setUserName: Dispatch<SetStateAction<string>>
  agentName: string
  setAgentName: Dispatch<SetStateAction<string>>
  accessScope: string
  setAccessScope: Dispatch<SetStateAction<string>>
  launchAtLogin: boolean
  setLaunchAtLogin: Dispatch<SetStateAction<boolean>>
  startInTray: boolean
  setStartInTray: Dispatch<SetStateAction<boolean>>
  closeToTray: boolean
  setCloseToTray: Dispatch<SetStateAction<boolean>>
  skipQuitWarn: boolean
  setSkipQuitWarn: Dispatch<SetStateAction<boolean>>
  webToolsEnabled: boolean
  setWebToolsEnabled: Dispatch<SetStateAction<boolean>>
  httpBootstrap: boolean
  setHttpBootstrap: Dispatch<SetStateAction<boolean>>
  privacyMode: boolean
  setPrivacyMode: Dispatch<SetStateAction<boolean>>
  approvalMode: 'ask' | 'auto'
  setApprovalMode: Dispatch<SetStateAction<'ask' | 'auto'>>
  harnessMode: string
  setHarnessMode: Dispatch<SetStateAction<string>>
  harnessMinPct: number
  setHarnessMinPct: Dispatch<SetStateAction<number>>
  harnessMaxPct: number
  setHarnessMaxPct: Dispatch<SetStateAction<number>>
  thinkingLevel: 'off' | 'low' | 'medium' | 'high'
  setThinkingLevel: Dispatch<SetStateAction<'off' | 'low' | 'medium' | 'high'>>
  allowSkillCreation: boolean
  setAllowSkillCreation: Dispatch<SetStateAction<boolean>>
  autoApproveThreshold: number
  setAutoApproveThreshold: Dispatch<SetStateAction<number>>
  logLevel: string
  setLogLevel: Dispatch<SetStateAction<string>>
  sarcasmMode: boolean
  setSarcasmMode: Dispatch<SetStateAction<boolean>>
  toolProcess: ToolProcessMode
  setToolProcess: Dispatch<SetStateAction<ToolProcessMode>>
  onToolProcessChange?: (mode: ToolProcessMode) => void
  catalog: ProviderInfo[]
  showAdvanced: boolean
  setShowAdvanced: Dispatch<SetStateAction<boolean>>
  xaiAuth: XaiAuthStatus | null
  xaiLoginBusy: boolean
  xaiUserCode: string
  xaiVerifyUrl: string
  xaiLoginMsg: string
  handleXaiSignIn: () => void
  handleXaiLogout: () => void
  vision: VisionStatus | null
  swarm: NanoSwarmStatus | null
  visionBusy: boolean
  setVisionBusy: Dispatch<SetStateAction<boolean>>
  visionMsg: string
  setVisionMsg: Dispatch<SetStateAction<string>>
  refreshVision: () => Promise<VisionStatus | null>
  startVisionInstallPoll: () => void
  connectedList: ConnectedProvider[]
  providerSearch: string
  setProviderSearch: Dispatch<SetStateAction<string>>
  enabledProviders: string[] | null
  setEnabledProviders: Dispatch<SetStateAction<string[] | null>>
  enabledModels: Record<string, string[]>
  setEnabledModels: Dispatch<SetStateAction<Record<string, string[]>>>
  catalogExpand: string | null
  setCatalogExpand: Dispatch<SetStateAction<string | null>>
  skillsBudget: number
  setSkillsBudget: Dispatch<SetStateAction<number>>
  primaryProviders: ProviderInfo[]
  advancedProviders: ProviderInfo[]
  activeMeta: ProviderInfo
  showBaseUrl: boolean
  providerModels: Array<{ id: string; name: string; provider?: string }>
  handleProviderChange: (p: string) => void
  handleBrowseProject: () => void
  themeId: ThemeId
  onThemeChange: (id: ThemeId) => void
  density: Density
  onDensityChange?: (d: Density) => void
  customAccent: string
  onCustomAccentChange?: (hex: string) => void
  updateInfo: UpdateInfo | null
  checkingUpdates: boolean
  updateStatus?: string | null
  onCheckUpdates: () => void
  onInstallUpdate?: () => void
  models: ModelInfo[]
  onOpenHelp?: (articleId?: string) => void
  settings: Settings | null
  messengers?: MessengerInfo[]
  messengerDrafts?: MessengerDraftMap
  setMessengerDrafts?: Dispatch<SetStateAction<MessengerDraftMap>>
  assistant?: AssistantStatus | null
  assistantDraft?: AssistantDraft
  setAssistantDraft?: Dispatch<SetStateAction<AssistantDraft>>
  onAssistantAccountsChanged?: () => void
  settingsMode?: SettingsMode
}

export function SettingsFormSections(p: SettingsFormProps): ReactNode {
  const {
    sectionProps,
    provider,
    model, setModel,
    baseUrl, setBaseUrl,
    apiKey, setApiKey,
    apiKeySet,
    projectPath, setProjectPath,
    browserHomeUrl, setBrowserHomeUrl,
    privacyShield = null,
    persona, setPersona,
    userName, setUserName,
    agentName, setAgentName,
    accessScope, setAccessScope,
    launchAtLogin, setLaunchAtLogin,
    startInTray, setStartInTray,
    closeToTray: _closeToTray,
    setCloseToTray: _setCloseToTray,
    skipQuitWarn, setSkipQuitWarn,
    webToolsEnabled, setWebToolsEnabled,
    httpBootstrap, setHttpBootstrap,
    privacyMode, setPrivacyMode,
    approvalMode, setApprovalMode,
    harnessMode, setHarnessMode,
    harnessMinPct, setHarnessMinPct,
    harnessMaxPct, setHarnessMaxPct,
    thinkingLevel, setThinkingLevel,
    allowSkillCreation, setAllowSkillCreation,
    autoApproveThreshold, setAutoApproveThreshold,
    logLevel, setLogLevel,
    sarcasmMode, setSarcasmMode,
    toolProcess, setToolProcess,
    onToolProcessChange,
    catalog, showAdvanced, setShowAdvanced,
    xaiAuth, xaiLoginBusy, xaiUserCode, xaiVerifyUrl, xaiLoginMsg,
    handleXaiSignIn, handleXaiLogout,
    vision, swarm: _swarm, visionBusy, setVisionBusy, visionMsg, setVisionMsg,
    refreshVision, startVisionInstallPoll,
    connectedList, providerSearch, setProviderSearch,
    enabledProviders, setEnabledProviders,
    enabledModels, setEnabledModels,
    catalogExpand, setCatalogExpand,
    skillsBudget, setSkillsBudget,
    primaryProviders, advancedProviders, activeMeta, showBaseUrl, providerModels,
    handleProviderChange, handleBrowseProject,
    themeId, onThemeChange, density, onDensityChange,
    customAccent, onCustomAccentChange,
    updateInfo, checkingUpdates, updateStatus, onCheckUpdates, onInstallUpdate,
    onOpenHelp, settings,
    messengers = [],
    messengerDrafts = {},
    setMessengerDrafts,
    assistant = null,
    assistantDraft = {},
    setAssistantDraft,
    onAssistantAccountsChanged,
    settingsMode = 'simple',
  } = p

  return (
          <>
            {/* Provider */}
            <SettingsSection
              {...sectionProps('provider')}
              defaultOpen
            >
              <div className="text-[10px] mb-2 leading-snug" style={{ color: 'var(--text-muted)' }}>
                Free options: <strong style={{ color: 'var(--text-secondary)' }}>Demo</strong> (no signup),
                Gemini / Groq / OpenRouter / Mistral (free key), or Ollama (local).
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
                  className="mb-2 text-[10px] underline"
                  style={{ color: 'var(--text-muted)' }}
                  onClick={() => setShowAdvanced(true)}
                >
                  Show advanced (custom endpoint)…
                </button>
              )}
              {provider === 'demo' && (
                <div
                  className="text-[10px] rounded px-2 py-1.5 mb-2 leading-snug"
                  style={{ color: 'var(--text-muted)', border: '1px solid var(--border)' }}
                >
                  Demo is guest chat only (Codestral, Gemini Flash Lite, GPT-OSS). Image/video
                  and other gateway models are hidden — they need a real key or are not chat.
                  Add Gemini/Groq free key or Ollama for serious free use.
                </div>
              )}
              {activeMeta?.key_docs_url && provider !== 'demo' && (
                <button
                  type="button"
                  className="mb-2 text-[10px] underline block"
                  style={{ color: 'var(--accent)' }}
                  onClick={() => void openExternalUrl(String(activeMeta.key_docs_url))}
                >
                  {provider === 'ollama' ? 'Download Ollama…' : 'Get free API key / docs…'}
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
            </SettingsSection>

            {/* Provider catalog — enable for main-screen picker */}
            <SettingsSection
              {...sectionProps('provider-catalog')}
            >
              <div className="text-[10px] mb-2" style={{ color: 'var(--text-muted)' }}>
                Connected providers with a key, OAuth, Demo, or local Ollama appear in the
                main status bar. Disable to hide without deleting credentials.
              </div>
              <input
                type="search"
                value={providerSearch}
                onChange={(e) => setProviderSearch(e.target.value)}
                placeholder="Search providers…"
                className="w-full rounded px-2 py-1 text-xs mb-2 outline-none"
                style={{
                  background: 'var(--bg-tertiary)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border)',
                }}
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
                          border: '1px solid var(--border)',
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
                                const base =
                                  prev
                                  ?? (connectedList.length
                                    ? connectedList.map((x) => x.id)
                                    : catalog.map((x) => x.id))
                                if (on) return [...new Set([...base, p.id])]
                                return base.filter((id) => id !== p.id)
                              })
                            }}
                          />
                          <button
                            type="button"
                            className="flex-1 min-w-0 text-left"
                            onClick={() =>
                              setCatalogExpand((cur) => (cur === p.id ? null : p.id))
                            }
                          >
                            <span className="font-medium">{p.name}</span>
                            <span className="block text-[10px]" style={{ color: 'var(--text-muted)' }}>
                              {conn ? 'connected' : 'not connected'}
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
            <SettingsSection
              {...sectionProps('you-agent')}
            >
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
            </SettingsSection>

            {/* Project */}
            <SettingsSection
              {...sectionProps('workspace')}
            >
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
              {(!projectPath.trim() || projectPath.trim() === '.') && (
                <div
                  className="text-[10px] leading-snug mt-1.5 rounded px-2 py-1.5"
                  style={{
                    color: 'var(--warning)',
                    background: 'color-mix(in srgb, var(--warning) 12%, var(--bg-tertiary))',
                    border: '1px solid color-mix(in srgb, var(--warning) 35%, var(--border))',
                  }}
                >
                  <strong>No project folder</strong> — tools use <strong>full</strong> access on
                  this PC (your Windows user). Fine for general help; for coding, pick a project
                  folder so work stays focused and safer.
                </div>
              )}
              <label className="block mb-0.5 mt-3" style={{ color: 'var(--text-muted)' }}>
                Browser homepage
              </label>
              <input
                type="url"
                value={browserHomeUrl}
                onChange={(e) => setBrowserHomeUrl(e.target.value)}
                placeholder="https://github.com/AhmiDarrow/RemedyAI"
                className="w-full rounded px-2 py-1 text-xs outline-none mb-1"
                style={{
                  background: 'var(--bg-tertiary)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border)',
                }}
                spellCheck={false}
              />
              <div className="text-[10px] leading-snug" style={{ color: 'var(--text-muted)' }}>
                In-app Browser (⌂ Home). Default is the Remedy GitHub repo. Use http(s) only.
              </div>
              {privacyShield && (
                <div
                  className="mt-3 rounded px-2 py-2"
                  style={{
                    background: 'var(--bg-tertiary)',
                    border: '1px solid var(--border)',
                  }}
                >
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <label
                      className="text-xs font-medium flex items-center gap-2"
                      style={{ color: 'var(--text-primary)' }}
                    >
                      <input
                        type="checkbox"
                        checked={privacyShield.enabled}
                        onChange={(e) => privacyShield.onToggle(e.target.checked)}
                        disabled={privacyShield.busy}
                      />
                      Browser Privacy Shield
                    </label>
                    <button
                      type="button"
                      className="text-[10px] px-1.5 py-0.5 rounded"
                      style={{
                        border: '1px solid var(--border)',
                        color: 'var(--text-secondary)',
                        opacity: privacyShield.busy ? 0.5 : 1,
                      }}
                      disabled={privacyShield.busy}
                      onClick={() => privacyShield.onRefresh()}
                      title="Re-download EasyList / EasyPrivacy"
                    >
                      {privacyShield.busy ? 'Updating…' : 'Update lists'}
                    </button>
                  </div>
                  <div className="text-[10px] leading-snug" style={{ color: 'var(--text-muted)' }}>
                    {privacyShield.message}
                    {!privacyShield.ready && privacyShield.enabled
                      ? ' (first run downloads filter lists).'
                      : ''}
                  </div>
                  <div
                    className="text-[9px] leading-snug mt-1"
                    style={{ color: 'var(--text-muted)', opacity: 0.85 }}
                    title={privacyShield.attribution}
                  >
                    Blocks ad/tracker navigations and hides many page ads (Brave engine + EasyList).
                    Not a full browser extension — use ↗ system browser for full uBlock Origin.
                  </div>
                </div>
              )}
            </SettingsSection>

            {/* Privacy — simple + advanced (what leaves this PC to the model) */}
            <SettingsSection {...sectionProps('privacy')}>
              <div className="text-[10px] leading-snug mb-2" style={{ color: 'var(--text-muted)' }}>
                Remedy is local-first. Chat and tool results still go to{' '}
                <strong style={{ color: 'var(--text-secondary)' }}>your chosen LLM</strong> when
                you use a cloud model. Privacy mode tightens what we send — default stays off for
                maximum speed and capability.
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={privacyMode}
                onClick={() => setPrivacyMode(!privacyMode)}
                className="w-full flex items-center justify-between gap-3 rounded-lg px-3 py-2.5 text-left transition-colors"
                style={{
                  background: privacyMode
                    ? 'color-mix(in srgb, var(--accent) 14%, var(--bg-tertiary))'
                    : 'var(--bg-tertiary)',
                  border: `1px solid ${privacyMode ? 'var(--accent)' : 'var(--border)'}`,
                }}
                title={
                  privacyMode
                    ? 'Privacy mode on — click to return to full-speed secret scrub only'
                    : 'Privacy mode off — click for tighter tool caps + email/phone scrub to the model'
                }
              >
                <div className="min-w-0">
                  <div
                    className="text-xs font-semibold"
                    style={{ color: privacyMode ? 'var(--accent)' : 'var(--text-primary)' }}
                  >
                    {privacyMode ? 'Privacy mode on' : 'Privacy mode off'}
                  </div>
                  <div className="text-[10px] leading-snug mt-0.5" style={{ color: 'var(--text-muted)' }}>
                    {privacyMode
                      ? 'Email, phone, and SSN shapes redacted · shorter tool results · still secret-safe'
                      : 'Lightning path — secrets redacted, full tool context for capable work'}
                  </div>
                </div>
                <span
                  className="flex-shrink-0 relative inline-flex h-6 w-11 rounded-full transition-colors"
                  style={{
                    background: privacyMode ? 'var(--accent)' : 'var(--border)',
                  }}
                  aria-hidden
                >
                  <span
                    className="absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform"
                    style={{
                      left: privacyMode ? 'calc(100% - 1.35rem)' : '0.125rem',
                    }}
                  />
                </span>
              </button>
              <div className="text-[10px] leading-snug mt-2" style={{ color: 'var(--text-muted)' }}>
                Also on the status bar. API keys never leave this PC as model input. Keys stay under{' '}
                <code className="text-[10px]">~/.remedy/auth/</code> (DPAPI on Windows).
              </div>
            </SettingsSection>

            {/* Access */}
            <SettingsSection
              {...sectionProps('access')}
            >
              <label className="block mb-0.5" style={{ color: 'var(--text-muted)' }}>
                Filesystem scope
              </label>
              <select
                value={accessScope}
                onChange={(e) => setAccessScope(e.target.value)}
                disabled={!projectPath.trim() || projectPath.trim() === '.'}
                className="w-full rounded px-2 py-1 text-xs mb-1 outline-none"
                style={{
                  background: 'var(--bg-tertiary)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border)',
                  opacity: !projectPath.trim() || projectPath.trim() === '.' ? 0.55 : 1,
                }}
              >
                <option value="untrusted">Untrusted project (strict)</option>
                <option value="project">Project + Desktop/Docs/Downloads</option>
                <option value="home">Project + full home folder</option>
                <option value="full">Full user machine (you grant)</option>
              </select>
              <div className="text-[10px] leading-snug" style={{ color: 'var(--text-muted)' }}>
                {(!projectPath.trim() || projectPath.trim() === '.') ? (
                  <>
                    Scope is forced to <strong>full</strong> while no project folder is set.
                    Choose a folder above to use Project / Home / Untrusted jails.
                  </>
                ) : (
                  <>
                    <strong>Untrusted</strong> = project root only + always Ask for shell/write
                    (use for downloaded folders). Full still runs as your Windows user.
                  </>
                )}
              </div>
            </SettingsSection>

            {/* Security & power (owner keeps full capability; defaults stay safe) */}
            <SettingsSection
              {...sectionProps('security-power')}
            >
              <div className="text-[10px] leading-snug mb-2" style={{ color: 'var(--text-muted)' }}>
                Defaults are safe. <strong style={{ color: 'var(--text-secondary)' }}>Auto</strong>{' '}
                approvals and opt-in tools never remove your power — they let Remedy finish work
                when you say so. Hard wipe/privilege blocks stay on for everyone.
              </div>
              <div className="mb-2">
                <div className="text-xs font-medium mb-1" style={{ color: 'var(--text-primary)' }}>
                  Approvals
                </div>
                <div className="flex gap-1 mb-1">
                  {(
                    [
                      { id: 'ask' as const, label: 'Ask', hint: 'Safe default — confirm shell/write/skills' },
                      {
                        id: 'auto' as const,
                        label: 'Auto',
                        hint: 'Work until done — full owner power on trusted scope',
                      },
                    ] as const
                  ).map((m) => (
                    <button
                      key={m.id}
                      type="button"
                      onClick={() => setApprovalMode(m.id)}
                      className="flex-1 py-1.5 rounded text-xs font-medium"
                      title={m.hint}
                      style={{
                        background: approvalMode === m.id ? 'var(--accent)' : 'var(--bg-tertiary)',
                        color: approvalMode === m.id ? '#fff' : 'var(--text-secondary)',
                        border: '1px solid var(--border)',
                      }}
                    >
                      {m.label}
                    </button>
                  ))}
                </div>
                <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                  {approvalMode === 'auto'
                    ? 'Auto: shell, write, edit, and skills run without prompts (except Untrusted scope). Use when you want Remedy to finish the job.'
                    : 'Ask: high-impact tools show Approve/Deny. Soft-risk patterns are labeled on the banner.'}
                </div>
              </div>
              <div className="mb-2">
                <div className="text-xs font-medium mb-1" style={{ color: 'var(--text-primary)' }}>
                  Thinking level
                </div>
                <div className="flex gap-1 mb-1 flex-wrap">
                  {(['off', 'low', 'medium', 'high'] as const).map((lvl) => (
                    <button
                      key={lvl}
                      type="button"
                      onClick={() => setThinkingLevel(lvl)}
                      className="flex-1 min-w-[3rem] py-1.5 rounded text-xs font-medium capitalize"
                      style={{
                        background: thinkingLevel === lvl ? 'var(--accent)' : 'var(--bg-tertiary)',
                        color: thinkingLevel === lvl ? '#fff' : 'var(--text-secondary)',
                        border: '1px solid var(--border)',
                      }}
                    >
                      {lvl}
                    </button>
                  ))}
                </div>
                <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                  Also on the status bar. High = more deliberation when the model supports it.
                </div>
              </div>
              <label className="flex items-center gap-2 mb-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={privacyMode}
                  onChange={(e) => setPrivacyMode(e.target.checked)}
                  style={{ accentColor: 'var(--accent)' }}
                />
                <span style={{ color: 'var(--text-primary)' }}>
                  Privacy mode (tighter model egress)
                </span>
              </label>
              <div className="text-[10px] leading-snug mb-1.5 pl-6" style={{ color: 'var(--text-muted)' }}>
                Same control as the Privacy section / status bar. Off by default so Remedy stays fast.
              </div>
              <label className="flex items-center gap-2 mb-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={webToolsEnabled}
                  onChange={(e) => setWebToolsEnabled(e.target.checked)}
                  style={{ accentColor: 'var(--accent)' }}
                />
                <span style={{ color: 'var(--text-primary)' }}>
                  Enable web_fetch (public HTTP only)
                </span>
              </label>
              <div className="text-[10px] leading-snug mb-1.5 pl-6" style={{ color: 'var(--text-muted)' }}>
                Opt-in. Private/localhost/metadata hosts are blocked (SSRF + DNS pin). Public web stays available.
              </div>
              <label className="flex items-center gap-2 mb-1 cursor-pointer">
                <input
                  type="checkbox"
                  checked={httpBootstrap}
                  onChange={(e) => setHttpBootstrap(e.target.checked)}
                  style={{ accentColor: 'var(--accent)' }}
                />
                <span style={{ color: 'var(--text-primary)' }}>
                  Allow browser token bootstrap
                </span>
              </label>
              <div className="text-[10px] leading-snug pl-6" style={{ color: 'var(--text-muted)' }}>
                On (default): browser Web UI can get the local token on loopback. Off: desktop IPC only
                (still full power in the app). Override anytime with{' '}
                <code className="text-[10px]">REMEDY_HTTP_BOOTSTRAP</code>.
              </div>
            </SettingsSection>

            {/* Always ready */}
            <SettingsSection
              {...sectionProps('always-ready')}
            >
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
                <span style={{ color: 'var(--text-primary)' }}>
                  Start hidden in tray
                </span>
              </label>
              <div className="text-[10px] leading-snug mb-1.5 pl-6" style={{ color: 'var(--text-muted)' }}>
                Off = window opens normally (recommended). On = only a tray icon until you click it.
                Independent of “Start with Windows”.
              </div>
              <label className="flex items-center gap-2 mb-1" style={{ opacity: 0.95 }}>
                <input
                  type="checkbox"
                  checked
                  disabled
                  readOnly
                  style={{ accentColor: 'var(--accent)' }}
                  aria-label="Close window always hides to tray"
                />
                <span style={{ color: 'var(--text-primary)' }}>
                  Close window (✕) always hides to tray
                </span>
              </label>
              <div className="text-[10px] leading-snug mb-1.5 pl-6" style={{ color: 'var(--text-muted)' }}>
                Always on for the always-ready partner: the OS ✕ / Alt+F4 hides Remedy to the
                system tray and keeps the local API running (Web UI and chat stay warm).
                Fully stop Remedy only from the tray menu <strong>Quit</strong> (or app menu Quit) —
                that stops the local server.
              </div>
              <label className="flex items-center gap-2 mb-1 cursor-pointer">
                <input
                  type="checkbox"
                  checked={skipQuitWarn}
                  onChange={(e) => setSkipQuitWarn(e.target.checked)}
                  style={{ accentColor: 'var(--accent)' }}
                />
                <span style={{ color: 'var(--text-primary)' }}>
                  Don&apos;t warn when quitting (server stops)
                </span>
              </label>
              <div className="text-[10px] leading-snug" style={{ color: 'var(--text-muted)' }}>
                Opt-in only. Uses the Windows <strong>Startup folder</strong> (Settings → Apps → Startup) —
                not the registry Run key. Quit fully stops the local API (browser WebUI dies);
                use <strong>Switch to WebUI</strong> or hide-to-tray to keep the server running.
              </div>
            </SettingsSection>

            {/* Tool process visibility */}
            <SettingsSection
              {...sectionProps('tool-process')}
            >
              <div className="text-[10px] leading-snug mb-2" style={{ color: 'var(--text-muted)' }}>
                How much <em>Process</em> detail to show under replies — same list, more depth.
                The chat answer is always complete (never truncated by this setting).
              </div>
              <div className="flex gap-1 mb-1 flex-wrap">
                {TOOL_PROCESS_MODES.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => {
                      setToolProcess(m.id)
                      onToolProcessChange?.(m.id)
                    }}
                    className="flex-1 min-w-[3.5rem] py-1.5 rounded text-xs font-medium"
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
            </SettingsSection>

            {/* Local model — required dependency (Apache 2.0 SmolVLM2) */}
            <SettingsSection
              {...sectionProps('vision')}
            >
              <div className="text-[10px] leading-snug mb-2" style={{ color: 'var(--text-muted)' }}>
                Local <strong style={{ color: 'var(--text-secondary)' }}>SmolVLM2 2.2B</strong>{' '}
                (Apache 2.0 · llama.cpp) — image understanding + local assist. Required dependency
                when your chat model is text-only. One-time download (~1.6 GB). Starts with Remedy.
              </div>
              {(vision?.warnings?.length || 0) > 0 && (
                <div
                  className="rounded-md px-2 py-1.5 mb-2 text-[10px] space-y-1"
                  style={{
                    background: 'color-mix(in srgb, #f59e0b 12%, var(--bg-primary))',
                    border: '1px solid var(--border)',
                    color: 'var(--text-secondary)',
                  }}
                >
                  {(vision?.warnings || []).map((w) => (
                    <div key={w.slice(0, 48)}>{w}</div>
                  ))}
                </div>
              )}
              <div
                className="rounded-md px-2 py-1.5 mb-2 text-[10px] space-y-0.5"
                style={{
                  background: 'var(--bg-tertiary)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-secondary)',
                }}
              >
                <div className="flex justify-between gap-2">
                  <span style={{ color: 'var(--text-muted)' }}>Model</span>
                  <span>{vision?.model?.name || 'SmolVLM2 2.2B'}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span style={{ color: 'var(--text-muted)' }}>Status</span>
                  <span>
                    {!vision
                      ? '…'
                      : vision.ready
                        ? vision.running
                          ? 'Ready (running)'
                          : 'Ready (idle)'
                        : vision.installed
                          ? vision.enabled
                            ? 'Installed'
                            : 'Installed · disabled'
                          : vision.progress?.phase === 'downloading' ||
                              vision.progress?.phase === 'extracting' ||
                              vision.progress?.phase === 'verifying'
                            ? `${vision.progress?.resumed ? 'Resuming…' : 'Installing…'} ${vision.progress?.message || ''}`
                            : vision.progress?.phase === 'cancelled'
                              ? 'Cancelled (resume available)'
                              : 'Not installed'}
                  </span>
                </div>
                {vision?.runtime_version ? (
                  <div className="flex justify-between gap-2">
                    <span style={{ color: 'var(--text-muted)' }}>llama.cpp</span>
                    <span>{vision.runtime_version}</span>
                  </div>
                ) : null}
                {vision?.health?.cpu_runtime != null ? (
                  <div className="flex justify-between gap-2">
                    <span style={{ color: 'var(--text-muted)' }}>Runtime</span>
                    <span>
                      {vision.health.cpu_runtime ? 'CPU' : 'GPU/CUDA'}
                      {vision.health.nvidia_detected ? ' · NVIDIA seen' : ''}
                    </span>
                  </div>
                ) : null}
                {vision?.health?.ram_gb != null || vision?.health?.disk_free_gb != null ? (
                  <div className="flex justify-between gap-2">
                    <span style={{ color: 'var(--text-muted)' }}>Resources</span>
                    <span>
                      {vision.health?.ram_gb != null ? `RAM ~${vision.health.ram_gb} GB` : ''}
                      {vision.health?.ram_gb != null && vision.health?.disk_free_gb != null
                        ? ' · '
                        : ''}
                      {vision.health?.disk_free_gb != null
                        ? `Disk free ~${vision.health.disk_free_gb} GB`
                        : ''}
                    </span>
                  </div>
                ) : null}
                {(vision?.progress?.bytes_total || 0) > 0 &&
                (vision?.progress?.phase === 'downloading' ||
                  vision?.progress?.phase === 'extracting') ? (
                  <div className="mt-1">
                    <div
                      className="h-1 rounded overflow-hidden"
                      style={{ background: 'var(--bg-primary)' }}
                    >
                      <div
                        className="h-full rounded"
                        style={{
                          width: `${Math.min(
                            100,
                            Math.round(
                              (100 * (vision.progress?.bytes_done || 0)) /
                                (vision.progress?.bytes_total || 1),
                            ),
                          )}%`,
                          background: 'var(--accent)',
                        }}
                      />
                    </div>
                    <div className="mt-0.5" style={{ color: 'var(--text-muted)' }}>
                      {formatDownloadGb(vision.progress?.bytes_done)} /{' '}
                      {formatDownloadGb(vision.progress?.bytes_total)}
                      {vision.progress?.current_file
                        ? ` · ${vision.progress.current_file}`
                        : ''}
                    </div>
                  </div>
                ) : null}
              </div>
              <label className="flex items-start gap-2 mb-2 text-xs cursor-pointer">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={Boolean(vision?.enabled)}
                  disabled={!vision?.installed || visionBusy}
                  onChange={(e) => {
                    const on = e.target.checked
                    void (async () => {
                      setVisionBusy(true)
                      setVisionMsg('')
                      try {
                        await updateSettings({ vision_enabled: on })
                        await refreshVision()
                        setVisionMsg(on ? 'Decoder enabled' : 'Decoder disabled')
                      } catch (err) {
                        setVisionMsg(err instanceof Error ? err.message : String(err))
                      } finally {
                        setVisionBusy(false)
                      }
                    })()
                  }}
                />
                <span>
                  <span className="block" style={{ color: 'var(--text-primary)' }}>
                    Enable for text-only chat models
                  </span>
                  <span className="block text-[10px]" style={{ color: 'var(--text-muted)' }}>
                    When the provider cannot see images, decode locally into text.
                  </span>
                </span>
              </label>
              <label className="flex items-start gap-2 mb-2 text-xs cursor-pointer">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={Boolean(vision?.force_decode)}
                  disabled={!vision?.installed || !vision?.enabled || visionBusy}
                  onChange={(e) => {
                    const on = e.target.checked
                    void (async () => {
                      setVisionBusy(true)
                      setVisionMsg('')
                      try {
                        await updateSettings({ vision_force_decode: on })
                        await refreshVision()
                        setVisionMsg(
                          on
                            ? 'Prefer local decoder even when the chat model has vision'
                            : 'Using provider vision when the model supports it',
                        )
                      } catch (err) {
                        setVisionMsg(err instanceof Error ? err.message : String(err))
                      } finally {
                        setVisionBusy(false)
                      }
                    })()
                  }}
                />
                <span>
                  <span className="block" style={{ color: 'var(--text-primary)' }}>
                    Prefer local decoder even if chat model has vision
                  </span>
                  <span className="block text-[10px]" style={{ color: 'var(--text-muted)' }}>
                    Sends a short text brief to the provider instead of image pixels — usually
                    fewer tokens and lower cost. Falls back to provider vision if the decoder
                    is not ready.
                  </span>
                </span>
              </label>
              <div className="flex flex-wrap gap-1.5">
                {!vision?.installed ? (
                  <>
                    <button
                      type="button"
                      disabled={visionBusy}
                      className="px-2 py-1 rounded text-[10px] font-medium"
                      style={{ background: 'var(--accent)', color: '#fff' }}
                      onClick={() => {
                        void (async () => {
                          setVisionBusy(true)
                          setVisionMsg('Downloading pinned local model…')
                          try {
                            const preferCuda = Boolean(vision?.health?.nvidia_detected)
                            const r = await installVision({ prefer_cuda: preferCuda })
                            if (
                              r.mode === 'already_installed'
                              || r.mode === 'local_files'
                              || r.mode === 'bundled'
                            ) {
                              setVisionMsg(r.message || 'Local model ready — starts with Remedy')
                              setVisionBusy(false)
                            } else {
                              startVisionInstallPoll()
                              setVisionMsg(
                                r.message
                                  || 'Downloading SmolVLM2 2.2B — server starts when finished.',
                              )
                            }
                            await refreshVision()
                          } catch (err) {
                            setVisionBusy(false)
                            setVisionMsg(err instanceof Error ? err.message : String(err))
                          }
                        })()
                      }}
                    >
                      Download &amp; install local model
                    </button>
                    <button
                      type="button"
                      disabled={visionBusy}
                      className="px-2 py-1 rounded text-[10px]"
                      style={{
                        background: 'var(--bg-tertiary)',
                        color: 'var(--text-muted)',
                        border: '1px solid var(--border)',
                      }}
                      title="If files already exist under ~/.remedy/vision"
                      onClick={() => {
                        void (async () => {
                          setVisionBusy(true)
                          setVisionMsg('Looking for existing files…')
                          try {
                            const r = await activateVisionBundle()
                            if (r.ok === false || r.error) {
                              setVisionMsg(
                                r.error || 'No local files found — use Download & install.',
                              )
                            } else {
                              setVisionMsg(r.message || 'Activated — starts with Remedy')
                            }
                            await refreshVision()
                          } catch (err) {
                            setVisionMsg(err instanceof Error ? err.message : String(err))
                          } finally {
                            setVisionBusy(false)
                          }
                        })()
                      }}
                    >
                      Use existing files
                    </button>
                    {(vision?.progress?.phase === 'downloading'
                      || vision?.progress?.phase === 'extracting'
                      || vision?.progress?.phase === 'verifying') && (
                      <button
                        type="button"
                        className="px-2 py-1 rounded text-[10px]"
                        style={{
                          background: 'var(--bg-tertiary)',
                          color: 'var(--text-primary)',
                          border: '1px solid var(--border)',
                        }}
                        onClick={() => {
                          void (async () => {
                            try {
                              await cancelVisionInstall()
                              setVisionMsg('Cancel requested…')
                              await refreshVision()
                            } catch (err) {
                              setVisionMsg(err instanceof Error ? err.message : String(err))
                            }
                          })()
                        }}
                      >
                        Cancel
                      </button>
                    )}
                  </>
                ) : (
                  <>
                    {!vision.running ? (
                      <button
                        type="button"
                        disabled={visionBusy}
                        className="px-2 py-1 rounded text-[10px]"
                        style={{
                          background: 'var(--bg-tertiary)',
                          color: 'var(--text-primary)',
                          border: '1px solid var(--border)',
                        }}
                        onClick={() => {
                          void (async () => {
                            setVisionBusy(true)
                            try {
                              const r = await startVisionServer()
                              if (!r.ok) setVisionMsg(r.error || 'Start failed')
                              else setVisionMsg('Local server started')
                              await refreshVision()
                            } catch (err) {
                              setVisionMsg(err instanceof Error ? err.message : String(err))
                            } finally {
                              setVisionBusy(false)
                            }
                          })()
                        }}
                      >
                        Start server
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled={visionBusy}
                        className="px-2 py-1 rounded text-[10px]"
                        style={{
                          background: 'var(--bg-tertiary)',
                          color: 'var(--text-primary)',
                          border: '1px solid var(--border)',
                        }}
                        onClick={() => {
                          void (async () => {
                            setVisionBusy(true)
                            try {
                              await stopVisionServer()
                              setVisionMsg('Server stopped')
                              await refreshVision()
                            } catch (err) {
                              setVisionMsg(err instanceof Error ? err.message : String(err))
                            } finally {
                              setVisionBusy(false)
                            }
                          })()
                        }}
                      >
                        Stop server
                      </button>
                    )}
                    {vision.health?.nvidia_detected && vision.health?.cpu_runtime ? (
                      <button
                        type="button"
                        disabled={visionBusy}
                        className="px-2 py-1 rounded text-[10px]"
                        style={{
                          background: 'var(--bg-tertiary)',
                          color: 'var(--text-primary)',
                          border: '1px solid var(--border)',
                        }}
                        title="Use CUDA llama-server (same SmolVLM2 weights)"
                        onClick={() => {
                          void (async () => {
                            setVisionBusy(true)
                            setVisionMsg('Switching to CUDA runtime…')
                            try {
                              await reinstallVisionRuntime(true)
                              startVisionInstallPoll()
                            } catch (err) {
                              setVisionBusy(false)
                              setVisionMsg(err instanceof Error ? err.message : String(err))
                            }
                          })()
                        }}
                      >
                        Use CUDA
                      </button>
                    ) : null}
                    {vision.health && !vision.health.cpu_runtime ? (
                      <button
                        type="button"
                        disabled={visionBusy}
                        className="px-2 py-1 rounded text-[10px]"
                        style={{
                          background: 'var(--bg-tertiary)',
                          color: 'var(--text-muted)',
                          border: '1px solid var(--border)',
                        }}
                        onClick={() => {
                          void (async () => {
                            setVisionBusy(true)
                            setVisionMsg('Switching to CPU runtime…')
                            try {
                              await reinstallVisionRuntime(false)
                              startVisionInstallPoll()
                            } catch (err) {
                              setVisionBusy(false)
                              setVisionMsg(err instanceof Error ? err.message : String(err))
                            }
                          })()
                        }}
                      >
                        Use CPU
                      </button>
                    ) : null}
                  </>
                )}
                <button
                  type="button"
                  className="px-2 py-1 rounded text-[10px]"
                  style={{ color: 'var(--text-muted)' }}
                  onClick={() => void refreshVision()}
                >
                  Refresh
                </button>
              </div>
              {visionMsg ? (
                <div className="text-[10px] mt-1.5" style={{ color: 'var(--text-muted)' }}>
                  {visionMsg}
                </div>
              ) : null}
              {onOpenHelp ? (
                <button
                  type="button"
                  className="text-[10px] mt-1 underline"
                  style={{ color: 'var(--accent)' }}
                  onClick={() => onOpenHelp('14-visual-decoder')}
                >
                  Help: local vision
                </button>
              ) : null}
            </SettingsSection>

            {/* Memory Harness */}
            <SettingsSection
              {...sectionProps('memory-harness')}
            >
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
              <div className="text-[10px] leading-snug mb-2" style={{ color: 'var(--text-muted)' }}>
                Keeps long chats lean without deleting your transcript. Use{' '}
                <code style={{ color: 'var(--accent)' }}>/compact</code> or{' '}
                <code style={{ color: 'var(--accent)' }}>/harness</code>.
              </div>
              <label className="block mb-0.5" style={{ color: 'var(--text-muted)' }}>
                Auto min context % ({Math.round(harnessMinPct * 100)}%)
              </label>
              <input
                type="range"
                min={5}
                max={90}
                step={1}
                value={Math.round(harnessMinPct * 100)}
                onChange={(e) => setHarnessMinPct(Number(e.target.value) / 100)}
                className="w-full mb-2"
                style={{ accentColor: 'var(--accent)' }}
              />
              <label className="block mb-0.5" style={{ color: 'var(--text-muted)' }}>
                Auto max context % ({Math.round(harnessMaxPct * 100)}%)
              </label>
              <input
                type="range"
                min={10}
                max={99}
                step={1}
                value={Math.round(harnessMaxPct * 100)}
                onChange={(e) => setHarnessMaxPct(Number(e.target.value) / 100)}
                className="w-full mb-1"
                style={{ accentColor: 'var(--accent)' }}
              />
              <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                In Auto mode, prune starts near min and compress is nudged by max. Defaults 75% / 92%.
              </div>
            </SettingsSection>

            {/* Advanced */}
            <SettingsSection
              {...sectionProps('advanced')}
            >
              <label className="flex items-center gap-2 mb-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={allowSkillCreation}
                  onChange={(e) => setAllowSkillCreation(e.target.checked)}
                  style={{ accentColor: 'var(--accent)' }}
                />
                <span style={{ color: 'var(--text-primary)' }}>Allow skill creation (learning loop)</span>
              </label>
              <label className="block mb-0.5" style={{ color: 'var(--text-muted)' }}>
                Learning auto-approve threshold ({autoApproveThreshold.toFixed(2)})
              </label>
              <input
                type="range"
                min={0}
                max={100}
                step={5}
                value={Math.round(autoApproveThreshold * 100)}
                onChange={(e) => setAutoApproveThreshold(Number(e.target.value) / 100)}
                className="w-full mb-2"
                style={{ accentColor: 'var(--accent)' }}
              />
              <label className="block mb-0.5" style={{ color: 'var(--text-muted)' }}>
                Log level
              </label>
              <select
                value={logLevel}
                onChange={(e) => setLogLevel(e.target.value)}
                className="w-full rounded px-2 py-1 text-xs mb-2 outline-none"
                style={{
                  background: 'var(--bg-tertiary)',
                  color: 'var(--text-primary)',
                  border: '1px solid var(--border)',
                }}
              >
                {['DEBUG', 'INFO', 'WARNING', 'ERROR'].map((l) => (
                  <option key={l} value={l}>{l}</option>
                ))}
              </select>
              <label className="flex items-center gap-2 mb-1 cursor-pointer">
                <input
                  type="checkbox"
                  checked={sarcasmMode}
                  onChange={(e) => setSarcasmMode(e.target.checked)}
                  style={{ accentColor: 'var(--accent)' }}
                />
                <span style={{ color: 'var(--text-primary)' }}>Sarcasm mode (tone flag)</span>
              </label>
              <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                Advanced knobs only — defaults keep full owner power. Skill creation stays on so Remedy can improve.
              </div>
            </SettingsSection>

            {setMessengerDrafts ? (
              <MessengersSection
                sectionProps={sectionProps('channels')}
                messengers={messengers}
                messengerDrafts={messengerDrafts}
                setMessengerDrafts={setMessengerDrafts}
              />
            ) : (
              <SettingsSection {...sectionProps('channels')}>
                <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                  Messenger settings unavailable.
                </div>
              </SettingsSection>
            )}

            {setAssistantDraft ? (
              <AssistantSection
                sectionProps={sectionProps('assistant')}
                assistant={assistant}
                draft={assistantDraft}
                setDraft={setAssistantDraft}
                onAccountsChanged={onAssistantAccountsChanged}
                settingsMode={settingsMode}
              />
            ) : (
              <SettingsSection {...sectionProps('assistant')}>
                <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                  Personal assistant settings unavailable.
                </div>
              </SettingsSection>
            )}

            {/* License */}
            <SettingsSection
              {...sectionProps('license')}
            >
              <div className="text-[10px] leading-snug space-y-1.5" style={{ color: 'var(--text-secondary)' }}>
                <p style={{ margin: 0 }}>
                  <strong style={{ color: 'var(--text-primary)' }}>Source-available</strong> —
                  free for solo developers and small indies (&lt;$1M revenue and &lt;20 FTE).
                  Personal / education / research free.
                </p>
                <p style={{ margin: 0 }}>
                  Larger orgs, multi-tenant SaaS, or commercial resale: email{' '}
                  <code style={{ color: 'var(--accent)' }}>ahmitdarrow@gmail.com</code>
                  {' '}(subject: RemedyAI commercial license).
                </p>
                <p style={{ margin: 0, color: 'var(--text-muted)' }}>
                  Copyright (c) 2025–2026 Ahmi Darrow. Binding terms in repo LICENSE; summary in COMMERCIAL.md.
                  No license keys or phone-home in the app.
                </p>
                {onOpenHelp ? (
                  <button
                    type="button"
                    className="text-xs underline"
                    style={{ color: 'var(--accent)', background: 'none', border: 0, padding: 0 }}
                    onClick={() => onOpenHelp('04-security-and-data')}
                  >
                    Open Security &amp; data (includes bootstrap / power notes) →
                  </button>
                ) : null}
              </div>
            </SettingsSection>

            {/* Theme */}
            <SettingsSection
              {...sectionProps('theme')}
            >
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
            </SettingsSection>

            {/* Help / Keyboard */}
            <SettingsSection
              {...sectionProps('help')}
            >
              <div className="text-xs mb-2" style={{ color: 'var(--text-muted)' }}>
                Enter sends · Shift+Enter new line · /help for the command card · F1 full manual
              </div>
              {onOpenHelp && (
                <div className="flex flex-col gap-1.5 mb-2">
                  <button
                    type="button"
                    onClick={() => onOpenHelp()}
                    className="w-full py-1.5 rounded text-xs font-medium"
                    style={{
                      background: 'var(--accent)',
                      color: '#fff',
                    }}
                  >
                    Open Help wiki (owner&apos;s manual)
                  </button>
                  <div className="flex flex-wrap gap-1">
                    {(
                      [
                        ['02-first-run', 'Setup'],
                        ['03-providers-and-auth', 'Providers'],
                        ['14-visual-decoder', 'Vision'],
                        ['09-troubleshooting', 'Troubleshoot'],
                        ['13-whats-new', "What's new"],
                      ] as const
                    ).map(([id, label]) => (
                      <button
                        key={id}
                        type="button"
                        onClick={() => onOpenHelp(id)}
                        className="px-2 py-1 rounded text-[10px]"
                        style={{
                          background: 'var(--bg-tertiary)',
                          color: 'var(--text-secondary)',
                          border: '1px solid var(--border)',
                        }}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
              )}
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
            </SettingsSection>

            {/* MCP host — export skills to external MCP clients */}
            <SettingsSection
              {...sectionProps('mcp')}
            >
              <div className="text-xs space-y-2" style={{ color: 'var(--text-secondary)' }}>
                <p style={{ margin: 0, fontSize: '0.75rem' }}>
                  Run Remedy as an MCP <strong>server</strong> so other MCP-compatible
                  tools can use skills and plans on <em>this machine</em> (same-owner, local only).
                </p>
                <div
                  className="p-2 rounded font-mono text-[11px] break-all"
                  style={{
                    background: 'var(--bg-primary)',
                    border: '1px solid var(--border)',
                    color: 'var(--text-primary)',
                  }}
                >
                  remedy mcp serve
                  <br />
                  # or: remedy-mcp
                </div>
                <button
                  type="button"
                  className="w-full py-1.5 rounded text-xs font-medium"
                  style={{
                    background: 'var(--bg-tertiary)',
                    border: '1px solid var(--border)',
                    color: 'var(--text-primary)',
                  }}
                  onClick={() => {
                    const snippet = JSON.stringify(
                      {
                        mcpServers: {
                          remedy: {
                            command: 'remedy-mcp',
                            args: [],
                          },
                        },
                      },
                      null,
                      2,
                    )
                    void navigator.clipboard.writeText(snippet).catch(() => {
                      /* ignore */
                    })
                  }}
                >
                  Copy MCP client JSON
                </button>
                <ul
                  className="m-0 pl-4 space-y-1"
                  style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}
                >
                  <li>
                    Tools: skill list/search/get, plan list/show; script run opt-in only.
                  </li>
                  <li>Quarantined skills stay blocked until you Trust them in Skills.</li>
                  <li>
                    Script execution: set env <code>REMEDY_MCP_ALLOW_RUN=1</code> (owner choice).
                  </li>
                  <li>Logs go to stderr so the MCP JSON stream stays clean.</li>
                </ul>
                {onOpenHelp && (
                  <button
                    type="button"
                    className="text-xs underline"
                    style={{ color: 'var(--accent)', background: 'none', border: 0, padding: 0 }}
                    onClick={() => onOpenHelp('10-cli-and-api')}
                  >
                    Open CLI &amp; API help →
                  </button>
                )}
              </div>
            </SettingsSection>

            {/* About */}
            <SettingsSection
              {...sectionProps('about')}
            >
              <div
                className="rounded-lg px-3 py-2 mb-3 text-xs leading-relaxed"
                style={{
                  background: 'var(--bg-tertiary)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-secondary)',
                }}
              >
                <div className="font-semibold mb-0.5" style={{ color: 'var(--text-primary)' }}>
                  From the creator
                </div>
                My name is Ahmi, I hope you enjoy my Remedy.
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

              <div className="mt-3 pt-3 border-t space-y-1.5" style={{ borderColor: 'var(--border)' }}>
                <button
                  type="button"
                  onClick={() => {
                    void (async () => {
                      try {
                        const { isTauri, tauriInvoke } = await import('../../api/tauri')
                        if (isTauri()) {
                          await tauriInvoke('switch_to_web_ui')
                          return
                        }
                      } catch (e) {
                        console.warn('switch_to_web_ui:', e)
                      }
                      await openExternalUrl('http://127.0.0.1:7400/')
                    })()
                  }}
                  className="w-full py-1.5 rounded text-xs font-medium transition-colors"
                  style={{
                    background: 'var(--bg-tertiary)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--border)',
                  }}
                  title="Minimize desktop to tray and open the WebUI chat in your default browser"
                >
                  Switch to WebUI…
                </button>
                <div className="text-[10px] px-0.5" style={{ color: 'var(--text-muted)' }}>
                  Hides Remedy to the tray and opens the local WebUI at{' '}
                  <code style={{ color: 'var(--accent)' }}>http://127.0.0.1:7400/</code>
                  . Tray → Show Remedy returns to desktop.
                </div>
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
                <button
                  type="button"
                  onClick={() => {
                    const ver =
                      updateInfo?.current_version
                      || settings?.version
                      || 'unknown'
                    void import('../../utils/reportIssue').then(({ openReportIssue }) =>
                      openReportIssue(ver),
                    )
                  }}
                  className="w-full py-1.5 rounded text-xs font-medium transition-colors"
                  style={{
                    background: 'var(--bg-tertiary)',
                    color: 'var(--text-primary)',
                    border: '1px solid var(--border)',
                  }}
                  title="Open GitHub Issues in your browser"
                >
                  Report an issue on GitHub…
                </button>
                <div className="text-[10px] px-0.5" style={{ color: 'var(--text-muted)' }}>
                  Opens{' '}
                  <button
                    type="button"
                    className="underline"
                    style={{ color: 'var(--accent)' }}
                    onClick={() =>
                      void import('../../utils/reportIssue').then(({ githubIssuesUrl }) =>
                        openExternalUrl(githubIssuesUrl()),
                      )
                    }
                  >
                    github.com/AhmiDarrow/RemedyAI/issues
                  </button>
                  {' '}— include version and steps when you can.
                </div>
                {/* Always reserve status area so a check never looks like a no-op */}
                <div className="mt-2 space-y-1 min-h-[2.5rem]">
                  {checkingUpdates && (
                    <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
                      Contacting GitHub releases…
                    </div>
                  )}
                  {!checkingUpdates && updateStatus && (
                    <div
                      className="text-xs break-words"
                      style={{
                        color: updateInfo?.update_available
                          ? 'var(--accent)'
                          : updateInfo?.error
                            ? 'var(--error, #ef4444)'
                            : 'var(--text-secondary)',
                      }}
                    >
                      {updateStatus}
                    </div>
                  )}
                  {updateInfo && (
                    <>
                      <div className="flex justify-between text-xs">
                        <span style={{ color: 'var(--text-muted)' }}>This app</span>
                        <span style={{ color: 'var(--text-primary)' }}>
                          v{updateInfo.current_version}
                        </span>
                      </div>
                      {(updateInfo.latest_desktop || updateInfo.latest_python) && (
                        <div className="flex justify-between text-xs">
                          <span style={{ color: 'var(--text-muted)' }}>Latest release</span>
                          <span
                            style={{
                              color: updateInfo.update_available
                                ? 'var(--accent)'
                                : 'var(--text-primary)',
                            }}
                          >
                            v{updateInfo.latest_desktop || updateInfo.latest_python}
                          </span>
                        </div>
                      )}
                      {updateInfo.python_version &&
                        updateInfo.python_version !== updateInfo.current_version && (
                          <div className="flex justify-between text-xs">
                            <span style={{ color: 'var(--text-muted)' }}>Sidecar</span>
                            <span style={{ color: 'var(--text-muted)' }}>
                              v{updateInfo.python_version}
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
                      {!updateInfo.update_available &&
                        !updateInfo.error &&
                        !checkingUpdates && (
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
            </SettingsSection>
          </>

  )
}
