/**
 * Pure helpers for mapping Settings API ↔ form fields.
 * Keeps SettingsPanel free of repetitive setX(s.x) / payload assembly.
 */

import type { Settings, SettingsUpdate } from '../../api/settings'
import { normalizeToolProcess, type ToolProcessMode } from '../../utils/toolLabels'
import type { MessengerDraftMap } from '../../utils/messengerDrafts'
import type { AssistantDraft } from './AssistantSection'

export type SettingsFormFields = {
  provider: string
  model: string
  baseUrl: string
  apiKey: string
  apiKeySet: boolean
  projectPath: string
  browserHomeUrl: string
  persona: string
  userName: string
  agentName: string
  agentGender: string
  accessScope: string
  launchAtLogin: boolean
  startInTray: boolean
  skipQuitWarn: boolean
  webToolsEnabled: boolean
  httpBootstrap: boolean
  privacyMode: boolean
  soulFieldEnabled: boolean
  approvalMode: 'ask' | 'auto'
  harnessMode: string
  harnessMinPct: number
  harnessMaxPct: number
  thinkingLevel: 'off' | 'low' | 'medium' | 'high'
  allowSkillCreation: boolean
  autoApproveThreshold: number
  logLevel: string
  sarcasmMode: boolean
  toolProcess: ToolProcessMode
  skillsBudget: number
  customName: string
  enabledProviders: string[] | null
  enabledModels: Record<string, string[]>
}

export function applySettingsSnapshot(
  s: Settings,
  set: {
    setProvider: (v: string) => void
    setModel: (v: string) => void
    setBaseUrl: (v: string) => void
    setApiKey: (v: string) => void
    setApiKeySet: (v: boolean) => void
    setProjectPath: (v: string) => void
    setBrowserHomeUrl: (v: string) => void
    setPersona: (v: string) => void
    setUserName: (v: string) => void
    setAgentName: (v: string) => void
    setAgentGender: (v: string) => void
    setAccessScope: (v: string) => void
    setLaunchAtLogin: (v: boolean) => void
    setStartInTray: (v: boolean) => void
    setSkipQuitWarn: (v: boolean) => void
    setWebToolsEnabled: (v: boolean) => void
    setHttpBootstrap: (v: boolean) => void
    setPrivacyMode: (v: boolean) => void
    setSoulFieldEnabled: (v: boolean) => void
    setApprovalMode: (v: 'ask' | 'auto') => void
    setHarnessMode: (v: string) => void
    setHarnessMinPct: (v: number) => void
    setHarnessMaxPct: (v: number) => void
    setThinkingLevel: (v: 'off' | 'low' | 'medium' | 'high') => void
    setAllowSkillCreation: (v: boolean) => void
    setAutoApproveThreshold: (v: number) => void
    setLogLevel: (v: string) => void
    setSarcasmMode: (v: boolean) => void
    setToolProcess: (v: ToolProcessMode) => void
    setSkillsBudget: (v: number) => void
    setCustomName: (v: string) => void
    setEnabledProviders: (v: string[] | null) => void
    setEnabledModels: (v: Record<string, string[]>) => void
  },
): void {
  const prov = s.llm_provider || 'openai'
  set.setProvider(prov)
  set.setModel(s.llm_model || 'gpt-4o-mini')
  set.setBaseUrl(s.llm_base_url || '')
  set.setApiKey('')
  set.setApiKeySet(Boolean(s.llm_api_key_set))
  set.setProjectPath(s.project_path || '.')
  set.setBrowserHomeUrl(
    s.browser_home_url || 'https://github.com/AhmiDarrow/RemedyAI',
  )
  set.setPersona(s.persona || 'balanced')
  set.setUserName((s.user_name || '').trim())
  set.setAgentName((s.name || 'Remedy').trim() || 'Remedy')
  set.setAgentGender(String(s.agent_gender || 'female'))
  set.setAccessScope(s.access_scope || 'project')
  set.setLaunchAtLogin(Boolean(s.launch_at_login))
  set.setStartInTray(Boolean(s.start_in_tray))
  set.setSkipQuitWarn(Boolean(s.skip_quit_server_warning))
  set.setWebToolsEnabled(Boolean(s.web_tools_enabled))
  set.setHttpBootstrap(Boolean(s.http_bootstrap))
  set.setPrivacyMode(Boolean(s.privacy_mode))
  set.setSoulFieldEnabled(s.soul_field_enabled !== false)
  const am = String(s.approval_mode || 'ask').toLowerCase()
  set.setApprovalMode(am === 'auto' ? 'auto' : 'ask')
  set.setHarnessMode(s.harness_mode || 'auto')
  set.setHarnessMinPct(Number(s.harness_min_context_pct ?? 0.75))
  set.setHarnessMaxPct(Number(s.harness_max_context_pct ?? 0.92))
  const tl = String(s.thinking_level || 'high').toLowerCase()
  if (tl === 'off' || tl === 'low' || tl === 'medium' || tl === 'high') {
    set.setThinkingLevel(tl)
  }
  set.setAllowSkillCreation(s.allow_skill_creation !== false)
  set.setAutoApproveThreshold(Number(s.auto_approve_threshold ?? 0.8))
  set.setLogLevel(s.log_level || 'INFO')
  set.setSarcasmMode(Boolean(s.sarcasm_mode))
  set.setToolProcess(
    normalizeToolProcess(s.tool_process ?? s.show_tool_calls),
  )
  set.setSkillsBudget(Number(s.skills_active_budget ?? 80))
  set.setCustomName(String(s.custom_llm_name || ''))
  set.setEnabledProviders(
    Array.isArray(s.enabled_providers) ? (s.enabled_providers as string[]) : null,
  )
  set.setEnabledModels(
    s.enabled_models && typeof s.enabled_models === 'object'
      ? (s.enabled_models as Record<string, string[]>)
      : {},
  )
}

export function buildSettingsUpdate(fields: SettingsFormFields): SettingsUpdate {
  return {
    llm_provider: fields.provider,
    llm_model: fields.model,
    llm_base_url: fields.baseUrl,
    llm_api_key: fields.apiKey || undefined,
    project_path: fields.projectPath,
    browser_home_url: fields.browserHomeUrl,
    persona: fields.persona,
    user_name: fields.userName,
    name: fields.agentName,
    agent_gender: fields.agentGender,
    access_scope: fields.accessScope,
    launch_at_login: fields.launchAtLogin,
    start_in_tray: fields.startInTray,
    skip_quit_server_warning: fields.skipQuitWarn,
    web_tools_enabled: fields.webToolsEnabled,
    http_bootstrap: fields.httpBootstrap,
    privacy_mode: fields.privacyMode,
    soul_field_enabled: fields.soulFieldEnabled,
    approval_mode: fields.approvalMode,
    harness_mode: fields.harnessMode,
    harness_min_context_pct: fields.harnessMinPct,
    harness_max_context_pct: fields.harnessMaxPct,
    thinking_level: fields.thinkingLevel,
    allow_skill_creation: fields.allowSkillCreation,
    auto_approve_threshold: fields.autoApproveThreshold,
    log_level: fields.logLevel,
    sarcasm_mode: fields.sarcasmMode,
    tool_process: fields.toolProcess,
    skills_active_budget: fields.skillsBudget,
    custom_llm_name: fields.customName,
    enabled_providers: fields.enabledProviders,
    enabled_models: fields.enabledModels,
  } as SettingsUpdate
}

// Silence unused-type imports used by call sites
export type { MessengerDraftMap, AssistantDraft }
