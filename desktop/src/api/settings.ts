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
  persona: string
  project_path: string
  access_scope?: string
  launch_at_login?: boolean
  start_in_tray?: boolean
  close_to_tray?: boolean
  harness_mode?: string
  harness_min_context_pct?: number
  harness_max_context_pct?: number
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
  persona?: string
  setup_completed?: boolean
  access_scope?: string
  launch_at_login?: boolean
  start_in_tray?: boolean
  close_to_tray?: boolean
  harness_mode?: string
  harness_min_context_pct?: number
  harness_max_context_pct?: number
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
