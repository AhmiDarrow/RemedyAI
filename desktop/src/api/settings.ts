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
  llm_api_key_set: boolean
  name: string
  /** What Remedy calls the human user */
  user_name?: string
  persona: string
  project_path: string
  access_scope?: string
  launch_at_login?: boolean
  start_in_tray?: boolean
  close_to_tray?: boolean
  harness_mode?: string
  harness_min_context_pct?: number
  harness_max_context_pct?: number
  /** Status bar: off | low | medium | high */
  thinking_level?: string
  /** Status bar: ask (default) | auto */
  approval_mode?: string
  /** Tool process: off | medium | full (default off) */
  tool_process?: string
  /** @deprecated use tool_process */
  show_tool_calls?: boolean
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
  version: string
  config_exists: boolean
  setup_completed: boolean
  needs_setup?: boolean
  llm_ready?: boolean
  xai_auth?: XaiAuthInfo
}

export interface SettingsUpdate {
  llm_provider?: string
  llm_model?: string
  llm_base_url?: string
  llm_api_key?: string
  project_path?: string
  name?: string
  user_name?: string
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
  tool_process?: string
  vision_enabled?: boolean
  vision_model_id?: string
  vision_force_decode?: boolean
  enabled_providers?: string[]
  enabled_models?: Record<string, string[]>
  last_model_by_provider?: Record<string, string>
  skills_active_budget?: number
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
}> {
  return apiFetch('/settings', {
    method: 'PUT',
    body: JSON.stringify(updates),
  })
}
