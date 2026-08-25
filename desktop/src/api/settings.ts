import { apiFetch } from './client'

export interface XaiAuthInfo {
  provider?: string
  auth_method?: string
  connected?: boolean
  has_api_key?: boolean
  has_oauth?: boolean
  expires_at?: number | null
}

export interface Settings {
  llm_provider: string
  llm_model: string
  llm_base_url: string
  /** User-set display name for the custom OpenAI-compatible endpoint */
  custom_llm_name?: string
  llm_api_key_set: boolean
  /** Booleans only — which providers have a stored key. */
  provider_keys_set?: Record<string, boolean>
  name: string
  /** What Remedy calls the human user */
  user_name?: string
  /** Partner gender presentation: female (default) | male | neutral */
  agent_gender?: string
  /** auto or BCP-47 id — chrome + reply language */
  ui_language?: string
  ui_languages?: Array<{
    id: string
    name_en: string
    name_native: string
    rtl: boolean
    chrome: boolean
  }>
  persona: string
  project_path: string
  access_scope?: string
  launch_at_login?: boolean
  start_in_tray?: boolean
  close_to_tray?: boolean
  /** Skip quit-server warning dialog */
  skip_quit_server_warning?: boolean
  harness_mode?: string
  harness_min_context_pct?: number
  harness_max_context_pct?: number
  /** Status bar: off | low | medium | high */
  thinking_level?: string
  /** Status bar: ask | auto (in-project) | full (warn) */
  approval_mode?: string
  /** conservative | balanced (default) | autonomous — never waives mail/pay */
  trust_profile?: string
  /** Tool process: off | medium | full (default off) */
  tool_process?: string
  /** @deprecated use tool_process */
  show_tool_calls?: boolean
  /** Opt-in web_fetch (public HTTP; SSRF-guarded) */
  web_tools_enabled?: boolean
  /**
   * Loopback HTTP may hand out the local API token (browser Web UI).
   * Desktop prefers IPC. Default true; set false for IPC-only.
   */
  http_bootstrap?: boolean
  /**
   * Opt-in privacy: tighter tool caps + email/phone scrub before cloud LLM.
   * Default false keeps the lightning path (secret scrub only).
   */
  privacy_mode?: boolean
  /** Route cloud chat through local Sleev gateway (token compression). */
  sleev_enabled?: boolean
  /** Optional gateway override; empty = auto-discover (127.0.0.1:17321). */
  sleev_gateway_url?: string
  /**
   * Owner opt-in: allow non-loopback Sleev gateway (LAN/remote).
   * Default false — otherwise provider API keys leave this machine.
   */
  sleev_allow_remote_gateway?: boolean
  /** Live Sleev install / gateway status from the server. */
  sleev?: {
    enabled?: boolean
    installed?: boolean
    gateway_url?: string
    gateway_is_loopback?: boolean
    allow_remote_gateway?: boolean
    harness?: string
    account_label?: string
    docs_url?: string
    home_url?: string
  }
  /** Soul Field personhood — default on (organism / continuity). */
  soul_field_enabled?: boolean
  build_os_advanced?: boolean
  rmb_enabled?: boolean
  allow_skill_creation?: boolean
  auto_approve_threshold?: number
  log_level?: string
  sarcasm_mode?: boolean
  /** Local visual decoder enabled in config */
  vision_enabled?: boolean
  vision_model_id?: string
  /** Prefer local decode even when chat model has native vision */
  vision_force_decode?: boolean
  vision?: {
    enabled?: boolean
    installed?: boolean
    ready?: boolean
    running?: boolean
    model_id?: string
    model_name?: string
    force_decode?: boolean
  }
  enabled_providers?: string[] | null
  enabled_models?: Record<string, string[]>
  last_model_by_provider?: Record<string, string>
  skills_active_budget?: number
  /** In-app Browser homepage (Settings); default Remedy GitHub */
  browser_home_url?: string
  version: string
  config_exists: boolean
  setup_completed: boolean
  needs_setup?: boolean
  llm_ready?: boolean
  xai_auth?: XaiAuthInfo
  /** Active channel ids (cli, telegram, …) */
  enabled_channels?: string[]
  /** Messenger connector status from catalog (no raw secrets) */
  messengers?: MessengerInfo[]
  /** Personal assistant status (prefs + local budget/debt counts; no secrets) */
  assistant?: AssistantStatus
}

/** Settings → Personal assistant prefs (GET status / PUT patch). */
export interface AssistantBriefPrefs {
  enabled?: boolean
  hour_local?: number
  quiet_start?: number
  quiet_end?: number
  include_calendar?: boolean
  include_mail?: boolean
  include_goals?: boolean
  include_budget?: boolean
  messenger_delivery?: boolean
}

export interface AssistantAccountInfo {
  id?: string
  provider?: string
  email?: string
  status?: string
  capabilities?: string[]
  last_sync?: string
  error?: string
}

export interface AssistantPrivacyNotices {
  privacy_ai_short?: string
  privacy_ai_full?: string
  privacy_ai_checkbox?: string
  account_connect_checkbox?: string
  money_disclaimer_short?: string
  money_disclaimer_full?: string
  google_scopes_plain?: string
}

export interface AssistantStatus {
  enabled?: boolean
  timezone?: string
  money_disclaimer_accepted?: boolean
  money_disclaimer?: string
  privacy_ai_accepted?: boolean
  account_access_accepted?: boolean
  privacy?: AssistantPrivacyNotices
  brief?: AssistantBriefPrefs
  accounts?: AssistantAccountInfo[]
  has_budget?: boolean
  debt_count?: number
  bill_count?: number
  providers_planned?: Array<{ id: string; name: string; status: string }>
  data_residency?: string
  tokens_to_model?: boolean
}

export interface AssistantUpdate {
  enabled?: boolean
  timezone?: string
  money_disclaimer_accepted?: boolean
  privacy_ai_accepted?: boolean
  account_access_accepted?: boolean
  default_calendar_account?: string
  default_mail_account?: string
  brief?: AssistantBriefPrefs
}

export interface MessengerFieldSchema {
  key: string
  label: string
  kind: 'secret' | 'text' | 'bool' | 'list' | 'url' | string
  placeholder?: string
  help?: string
  required?: boolean
}

export interface MessengerInfo {
  id: string
  name: string
  description?: string
  status: 'ready' | 'partial' | 'planned' | string
  enabled: boolean
  token_set: boolean
  inbound?: boolean
  outbound?: boolean
  docs_url?: string
  badge?: string
  max_reply_chars?: number
  fields?: Record<string, unknown>
  field_schema?: MessengerFieldSchema[]
}

export interface SettingsUpdate {
  llm_provider?: string
  llm_model?: string
  llm_base_url?: string
  /** User-set display name for the custom OpenAI-compatible endpoint */
  custom_llm_name?: string
  llm_api_key?: string
  project_path?: string
  name?: string
  user_name?: string
  agent_gender?: string
  ui_language?: string
  persona?: string
  setup_completed?: boolean
  access_scope?: string
  launch_at_login?: boolean
  start_in_tray?: boolean
  close_to_tray?: boolean
  harness_mode?: string
  harness_min_context_pct?: number
  harness_max_context_pct?: number
  thinking_level?: string
  approval_mode?: string
  trust_profile?: string
  tool_process?: string
  web_tools_enabled?: boolean
  http_bootstrap?: boolean
  privacy_mode?: boolean
  /** Route cloud chat through local Sleev gateway */
  sleev_enabled?: boolean
  sleev_gateway_url?: string
  sleev_allow_remote_gateway?: boolean
  soul_field_enabled?: boolean
  build_os_advanced?: boolean
  rmb_enabled?: boolean
  allow_skill_creation?: boolean
  auto_approve_threshold?: number
  log_level?: string
  sarcasm_mode?: boolean
  vision_enabled?: boolean
  vision_model_id?: string
  vision_force_decode?: boolean
  enabled_providers?: string[] | null
  enabled_models?: Record<string, string[]>
  last_model_by_provider?: Record<string, string>
  skills_active_budget?: number
  browser_home_url?: string
  enabled_channels?: string[]
  messengers?: Record<string, Record<string, unknown>>
  /** Personal assistant prefs (also mirrored under ~/.remedy/assistant.json) */
  assistant?: AssistantUpdate
}

export async function getSettings(): Promise<Settings> {
  return apiFetch<Settings>('/settings')
}

export async function updateSettings(
  updates: SettingsUpdate,
): Promise<{
  status: string
  changes: string[]
  config_path: string
  llm_provider?: string
  llm_model?: string
  llm_base_url?: string
  custom_llm_name?: string
}> {
  return apiFetch('/settings', {
    method: 'PUT',
    body: JSON.stringify(updates),
  })
}
