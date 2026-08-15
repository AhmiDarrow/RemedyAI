/** xAI device-code OAuth that outlives Settings (rail can unmount the panel). */

import { pollXaiLogin, startXaiLogin, type XaiAuthStatus, type XaiLoginPoll, type XaiLoginStart } from './auth'
import { openUrlInBrowserRail } from './computer'
import { updateSettings } from './settings'

export const XAI_OAUTH_EVENT = 'remedy:xai-oauth'
const SESSION_KEY = 'remedy.xaiOauthSession'

export type XaiOAuthEvent = {
  phase: 'started' | 'connected' | 'error'
  userCode?: string
  verifyUrl?: string
  message?: string
  credentials?: XaiAuthStatus
  error?: string
}

export function xaiModelAfterOauth(current: string | undefined): string {
  const m = (current || '').trim()
  return m.toLowerCase().startsWith('grok') ? m : 'grok-4.3'
}

/** Only the *this* device-code session is done — not a pre-existing API key. */
export function xaiOauthSessionPhase(
  poll: Pick<XaiLoginPoll, 'session'>,
): 'pending' | 'connected' | 'error' {
  const st = String(poll.session?.status || '')
  if (st === 'connected') return 'connected'
  if (st === 'error') return 'error'
  return 'pending'
}

export function emitXaiOAuth(detail: XaiOAuthEvent): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent(XAI_OAUTH_EVENT, { detail }))
}

export function subscribeXaiOAuth(fn: (e: XaiOAuthEvent) => void): () => void {
  if (typeof window === 'undefined') return () => {}
  const handler = (ev: Event) => fn((ev as CustomEvent<XaiOAuthEvent>).detail)
  window.addEventListener(XAI_OAUTH_EVENT, handler)
  return () => window.removeEventListener(XAI_OAUTH_EVENT, handler)
}

let pollTimer: ReturnType<typeof setInterval> | null = null
let sessionId: string | null = null
let preferredModel: string | undefined
let deadlineMs = 0

export function stopXaiOAuthPoll(): void {
  if (pollTimer != null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function readStoredSession(): string | null {
  try {
    return sessionStorage.getItem(SESSION_KEY)
  } catch {
    return null
  }
}

function writeStoredSession(id: string | null): void {
  try {
    if (id) sessionStorage.setItem(SESSION_KEY, id)
    else sessionStorage.removeItem(SESSION_KEY)
  } catch {
    /* private mode */
  }
}

async function persistXaiProvider(): Promise<void> {
  const model = xaiModelAfterOauth(preferredModel)
  await updateSettings({
    llm_provider: 'xai',
    llm_model: model,
    llm_base_url: 'https://api.x.ai/v1',
  })
}

function startPolling(): void {
  if (!sessionId || pollTimer != null) return
  const tick = async () => {
    if (!sessionId) return
    if (deadlineMs && Date.now() > deadlineMs) {
      stopXaiOAuthPoll()
      writeStoredSession(null)
      sessionId = null
      emitXaiOAuth({ phase: 'error', error: 'Sign-in expired' })
      return
    }
    try {
      const poll = await pollXaiLogin(sessionId)
      const phase = xaiOauthSessionPhase(poll)
      if (phase === 'connected') {
        stopXaiOAuthPoll()
        writeStoredSession(null)
        sessionId = null
        try {
          await persistXaiProvider()
        } catch (err) {
          console.warn('persist xAI after OAuth:', err)
          emitXaiOAuth({
            phase: 'error',
            error:
              'Signed in with xAI, but could not save the provider. Open Settings, pick xAI, and Save.',
          })
          return
        }
        emitXaiOAuth({
          phase: 'connected',
          credentials: poll.credentials,
          message: 'Signed in with xAI',
        })
      } else if (phase === 'error') {
        stopXaiOAuthPoll()
        writeStoredSession(null)
        sessionId = null
        emitXaiOAuth({
          phase: 'error',
          error: poll.session?.error || 'Sign-in failed or expired',
        })
      }
    } catch {
      /* keep polling */
    }
  }
  pollTimer = setInterval(() => {
    void tick()
  }, 4000)
  void tick()
}

export function resumeXaiOAuthPoll(): void {
  if (pollTimer != null) return
  const sid = sessionId || readStoredSession()
  if (!sid) return
  sessionId = sid
  startPolling()
}

export async function beginXaiOAuth(opts?: {
  keepSettings?: boolean
  llmModel?: string
}): Promise<XaiLoginStart> {
  stopXaiOAuthPoll()
  preferredModel = opts?.llmModel
  const start = await startXaiLogin()
  sessionId = start.session_id
  deadlineMs = Date.now() + Math.max(60, Number(start.expires_in) || 900) * 1000
  writeStoredSession(start.session_id)
  const verifyUrl = start.verification_uri_complete || start.verification_uri
  emitXaiOAuth({
    phase: 'started',
    userCode: start.user_code,
    verifyUrl,
    message: start.message || `Enter code ${start.user_code} at xAI`,
  })
  void openUrlInBrowserRail(verifyUrl, { keepSettings: opts?.keepSettings !== false })
  startPolling()
  return start
}
