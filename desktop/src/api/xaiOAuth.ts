/** xAI device-code OAuth that outlives Settings (rail can unmount the panel). */

import { pollXaiLogin, startXaiLogin, type XaiAuthStatus, type XaiLoginPoll, type XaiLoginStart } from './auth'
import { closeBrowserRail, openUrlInBrowserRail } from './computer'
import { updateSettings } from './settings'
import { fetchModels, pickDefaultModel } from './modelDiscovery'
import { listProviders } from './providers'
import { isTauri } from './tauri'

/**
 * Open the sign-in window *during the click*. A browser only lets a page open
 * a window from a user gesture; by the time the device code comes back from
 * the server (several awaits later) that permission is gone and window.open is
 * silently blocked — the WebUI "sign-in does nothing" bug. The desktop app
 * uses its own Browser rail instead, so this is a web-only step.
 *
 * Returns null on desktop, or when the browser refused even this.
 */
export function preopenSignInWindow(
  open: (url: string, target: string) => Window | null = (u, t) => window.open(u, t),
  desktop: () => boolean = isTauri,
): Window | null {
  if (desktop()) return null
  try {
    const w = open('about:blank', '_blank')
    if (!w) return null
    try {
      w.document.title = 'Signing in to xAI…'
      w.document.body.style.cssText = 'font-family:system-ui,sans-serif;padding:32px;color:#444'
      w.document.body.textContent = 'Opening xAI sign-in…'
    } catch {
      /* cross-origin or locked — the navigation below still works */
    }
    return w
  } catch {
    return null
  }
}

/** Point a pre-opened window at the verify page. False when it is gone. */
export function sendSignInWindowTo(w: Window | null | undefined, url: string): boolean {
  if (!w || w.closed || !url) return false
  try {
    // Sever the opener link the way noopener would have.
    w.opener = null
  } catch {
    /* */
  }
  try {
    w.location.href = url
    return true
  } catch {
    return false
  }
}

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

/**
 * Model to keep after an xAI sign-in: an existing grok model stays; anything
 * else becomes the provider default resolved from discovery / the live catalog
 * (never a hardcoded id). Empty when nothing is known yet.
 */
export function xaiModelAfterOauth(
  current: string | undefined,
  providerDefault?: string | null,
): string {
  const m = (current || '').trim()
  if (m.toLowerCase().startsWith('grok')) return m
  return (providerDefault || '').trim()
}

/** Best-known xAI default: endpoint discovery first, then the backend catalog. */
export async function resolveXaiDefaultModel(current?: string): Promise<string> {
  try {
    const data = await fetchModels('xai')
    const picked = pickDefaultModel(
      xaiModelAfterOauth(current, ''),
      data.models,
      data.default,
    )
    if (picked) return picked
  } catch {
    /* discovery unavailable — fall through to catalog */
  }
  try {
    const cat = await listProviders()
    return cat.find((p) => p.id === 'xai')?.default_model || ''
  } catch {
    return ''
  }
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
  const model = xaiModelAfterOauth(
    preferredModel,
    await resolveXaiDefaultModel(preferredModel),
  )
  await updateSettings({
    llm_provider: 'xai',
    ...(model ? { llm_model: model } : {}),
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
        void closeBrowserRail()
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
        void closeBrowserRail()
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
  /** Default true. Wizard passes false — no workspace rail yet. */
  openRail?: boolean
  /** Web: the window opened during the click (preopenSignInWindow). */
  popup?: Window | null
}): Promise<XaiLoginStart> {
  stopXaiOAuthPoll()
  preferredModel = opts?.llmModel
  let start: XaiLoginStart
  try {
    start = await startXaiLogin()
  } catch (e) {
    try {
      opts?.popup?.close()
    } catch {
      /* */
    }
    throw e
  }
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
  if (!sendSignInWindowTo(opts?.popup, verifyUrl)) {
    void openUrlInBrowserRail(verifyUrl, {
      keepSettings: opts?.keepSettings !== false,
      openRail: opts?.openRail,
    })
  }
  startPolling()
  return start
}
