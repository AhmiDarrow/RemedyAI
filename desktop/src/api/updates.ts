import { apiFetch } from './client'
import { isTauri, tauriInvoke } from './tauri'

export interface UpdateInfo {
  current_version: string
  latest_python: string | null
  latest_desktop: string | null
  release_url: string | null
  installer_url: string | null
  update_available: boolean
  error: string | null
}

export interface DesktopUpdateInfo {
  current_version: string
  latest_version: string
  update_available: boolean
  download_url: string | null
  release_notes: string | null
  error: string | null
}

export interface UpdateProgress {
  phase: 'downloading' | 'installing' | 'relaunch' | 'error' | string
  percent: number
  message: string
}

export async function checkUpdates(): Promise<UpdateInfo> {
  return apiFetch<UpdateInfo>('/updates/check')
}

/** Preferred path in the desktop shell — talks to Rust for GitHub installer URL. */
export async function checkDesktopUpdate(): Promise<DesktopUpdateInfo | null> {
  if (!isTauri()) return null
  try {
    return await tauriInvoke<DesktopUpdateInfo>('check_desktop_update')
  } catch {
    return null
  }
}

/** Ollama-style: download installer with progress events, launch it, exit app. */
export async function startDesktopUpdate(downloadUrl: string): Promise<void> {
  if (!isTauri()) {
    throw new Error('In-app update is only available in the desktop app')
  }
  await tauriInvoke('start_desktop_update', { downloadUrl })
}
