import { apiFetch } from './client'

export interface UpdateInfo {
  current_version: string
  latest_python: string | null
  latest_desktop: string | null
  release_url: string | null
  update_available: boolean
  error: string | null
}

export async function checkUpdates(): Promise<UpdateInfo> {
  return apiFetch<UpdateInfo>('/updates/check')
}
