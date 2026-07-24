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
  /** Python sidecar version when different from shell (optional). */
  python_version?: string | null
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

/** Parse semver-ish strings for ordering (mirrors Rust/Python helpers). */
export function parseSemver(raw: string | null | undefined): [number, number, number] {
  const s = String(raw || '')
    .trim()
    .replace(/^[vV]/, '')
    .split(/[-+]/, 1)[0] || ''
  const parts = s.split('.').map((p) => {
    const digits = p.replace(/\D/g, '')
    return digits ? parseInt(digits, 10) : 0
  })
  return [parts[0] || 0, parts[1] || 0, parts[2] || 0]
}

export function isNewerVersion(latest: string, current: string): boolean {
  const a = parseSemver(latest)
  const b = parseSemver(current)
  for (let i = 0; i < 3; i++) {
    if (a[i]! > b[i]!) return true
    if (a[i]! < b[i]!) return false
  }
  return false
}

/**
 * API update check. Pass the *desktop shell* version as ``current`` so a
 * newer/older Python sidecar cannot hide a real desktop update.
 */
export async function checkUpdates(currentShellVersion?: string): Promise<UpdateInfo> {
  const q =
    currentShellVersion && currentShellVersion.trim()
      ? `?current=${encodeURIComponent(currentShellVersion.trim())}`
      : ''
  return apiFetch<UpdateInfo>(`/updates/check${q}`)
}

/**
 * Preferred path in the desktop shell — talks to Rust for GitHub installer URL.
 * Always returns a result object (never null) so the UI can show status/errors.
 */
export async function checkDesktopUpdate(): Promise<DesktopUpdateInfo> {
  if (!isTauri()) {
    return {
      current_version: 'unknown',
      latest_version: 'unknown',
      update_available: false,
      download_url: null,
      release_notes: null,
      error: 'Not running inside the desktop shell',
    }
  }
  try {
    return await tauriInvoke<DesktopUpdateInfo>('check_desktop_update')
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    return {
      current_version: 'unknown',
      latest_version: 'unknown',
      update_available: false,
      download_url: null,
      release_notes: null,
      error: `Desktop update check failed: ${msg}`,
    }
  }
}

/** Ollama-style: download installer with progress events, launch it, exit app. */
export async function startDesktopUpdate(downloadUrl: string): Promise<void> {
  if (!isTauri()) {
    throw new Error('In-app update is only available in the desktop app')
  }
  await tauriInvoke('start_desktop_update', { downloadUrl })
}
