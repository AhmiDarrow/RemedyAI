import { apiFetch } from './client'

export interface Settings {
  llm_provider: string
  llm_model: string
  llm_base_url: string
  llm_api_key_set: boolean
  name: string
  persona: string
  project_path: string
  version: string
  config_exists: boolean
  setup_completed: boolean
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
