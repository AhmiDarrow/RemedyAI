/** Personal assistant API — Google OAuth (Calendar Phase 1). */
import { apiFetch } from './client'

export interface GoogleAppPublic {
  client_id_set?: boolean
  client_secret_set?: boolean
  redirect_uri?: string
  scopes?: string[]
}

export interface GoogleAuthStatus {
  provider?: string
  connected?: boolean
  email?: string
  has_refresh?: boolean
  expires_at?: number | null
  scopes?: string[]
  app?: GoogleAppPublic
  setup_hint?: string | null
}

export interface GoogleOAuthStart {
  status: string
  state: string
  auth_url: string
  redirect_uri?: string
  message?: string
}

export interface GoogleOAuthPoll {
  status: string
  state?: string
  error?: string | null
  email?: string
  credentials?: GoogleAuthStatus
}

export async function getGoogleStatus(): Promise<GoogleAuthStatus> {
  return apiFetch<GoogleAuthStatus>('/assistant/google')
}

export async function saveGoogleApp(body: {
  client_id?: string
  client_secret?: string
  redirect_uri?: string
  clear_secret?: boolean
}): Promise<{ status: string; app: GoogleAppPublic }> {
  return apiFetch('/assistant/google/app', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export async function startGoogleOAuth(redirectUri?: string): Promise<GoogleOAuthStart> {
  return apiFetch<GoogleOAuthStart>('/assistant/google/oauth/start', {
    method: 'POST',
    body: JSON.stringify(redirectUri ? { redirect_uri: redirectUri } : {}),
  })
}

export async function pollGoogleOAuth(state: string): Promise<GoogleOAuthPoll> {
  const q = encodeURIComponent(state)
  return apiFetch<GoogleOAuthPoll>(`/assistant/google/oauth/status?state=${q}`)
}

export async function disconnectGoogle(): Promise<{ status: string; google: GoogleAuthStatus }> {
  return apiFetch('/assistant/google', { method: 'DELETE' })
}
