import { apiFetch } from './client'
import { isTauri, tauriInvoke } from './tauri'

export interface XaiAuthStatus {
  provider: string
  auth_method: 'none' | 'api_key' | 'oauth' | string
  connected: boolean
  has_api_key: boolean
  has_oauth: boolean
  expires_at?: number | null
}

export interface XaiLoginStart {
  session_id: string
  user_code: string
  device_code?: string
  verification_uri: string
  verification_uri_complete: string
  interval: number
  expires_in: number
  status: string
  message?: string
}

export interface XaiLoginPoll {
  credentials: XaiAuthStatus
  session?: {
    session_id: string
    status: 'pending' | 'connected' | 'error' | 'unknown' | string
    error?: string | null
  }
}

export async function getXaiAuthStatus(): Promise<XaiAuthStatus> {
  return apiFetch<XaiAuthStatus>('/auth/xai')
}

export async function startXaiLogin(): Promise<XaiLoginStart> {
  return apiFetch<XaiLoginStart>('/auth/xai/login', { method: 'POST', body: '{}' })
}

export async function pollXaiLogin(sessionId: string): Promise<XaiLoginPoll> {
  const q = encodeURIComponent(sessionId)
  return apiFetch<XaiLoginPoll>(`/auth/xai/login/status?session_id=${q}`)
}

export async function saveXaiApiKey(apiKey: string): Promise<XaiAuthStatus & { status: string }> {
  return apiFetch('/auth/xai/apikey', {
    method: 'POST',
    body: JSON.stringify({ api_key: apiKey }),
  })
}

export async function logoutXai(): Promise<{ status: string; connected: boolean }> {
  return apiFetch('/auth/xai', { method: 'DELETE' })
}

/**
 * Open verification URL in the system browser.
 * Prefer Tauri shell plugin when available; fall back to window.open.
 */
export async function openExternalUrl(url: string): Promise<void> {
  if (!url) return
  if (isTauri()) {
    try {
      // tauri-plugin-shell open (plugin command name varies by version)
      await tauriInvoke('plugin:shell|open', { path: url })
      return
    } catch {
      try {
        await tauriInvoke('plugin:shell|open', { url })
        return
      } catch {
        // fall through
      }
    }
  }
  try {
    window.open(url, '_blank', 'noopener,noreferrer')
  } catch {
    // ignore
  }
}
