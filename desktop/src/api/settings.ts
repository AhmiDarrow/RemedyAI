import { apiFetch } from './client'

export interface Settings {
  llm_provider: string
  llm_model: string
  llm_base_url: string
  llm_api_key_set: boolean
  name: string
  project_path: string
  version: string
}

export interface SettingsUpdate {
  llm_provider?: string
  llm_model?: string
  llm_base_url?: string
  llm_api_key?: string
  project_path?: string
  name?: string
}

export async function getSettings(): Promise<Settings> {
  return apiFetch<Settings>('/settings')
}

export async function updateSettings(
  updates: SettingsUpdate,
): Promise<{ status: string; changes: string[]; config_path: string }> {
  return apiFetch('/settings', {
    method: 'PUT',
    body: JSON.stringify(updates),
  })
}
