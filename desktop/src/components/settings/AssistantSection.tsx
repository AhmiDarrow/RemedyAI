/** Settings → Personal assistant — Google Calendar (Browser rail OAuth), brief, budget. */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from 'react'
import {
  disconnectGoogle,
  getGoogleStatus,
  pollGoogleOAuth,
  saveGoogleApp,
  startGoogleOAuth,
  type GoogleAuthStatus,
} from '../../api/assistant'
import { openExternalUrl } from '../../api/auth'
import { openUrlInBrowserRail } from '../../api/computer'
import { SettingsSection } from '../SettingsSection'

export type AssistantDraft = {
  enabled?: boolean
  timezone?: string
  money_disclaimer_accepted?: boolean
  brief?: {
    enabled?: boolean
    hour_local?: number
    quiet_start?: number
    quiet_end?: number
    include_calendar?: boolean
    include_mail?: boolean
    include_goals?: boolean
    include_budget?: boolean
    messenger_delivery?: boolean
  }
}

type SectionProps = {
  id: string
  title: string
  summary: string
  keywords: string
  forceOpen?: boolean
  hidden?: boolean
  onOpenChange?: (open: boolean) => void
}

export type AssistantStatus = {
  enabled?: boolean
  timezone?: string
  money_disclaimer_accepted?: boolean
  money_disclaimer?: string
  brief?: AssistantDraft['brief']
  accounts?: Array<{
    id?: string
    provider?: string
    email?: string
    status?: string
    capabilities?: string[]
  }>
  has_budget?: boolean
  debt_count?: number
  bill_count?: number
  providers_planned?: Array<{ id: string; name: string; status: string }>
}

export interface AssistantSectionProps {
  sectionProps: SectionProps
  assistant: AssistantStatus | null | undefined
  draft: AssistantDraft
  setDraft: Dispatch<SetStateAction<AssistantDraft>>
  onAccountsChanged?: () => void
}

const DEFAULT_REDIRECT = 'http://127.0.0.1:7400/api/assistant/google/callback'

const inputStyle = {
  borderColor: 'var(--border)',
  background: 'var(--bg-input, var(--bg-elevated))',
  color: 'var(--text-primary)',
} as const

const GOOGLE_CONSOLE = 'https://console.cloud.google.com/apis/credentials'

function shortErr(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e)
  if (/client_id not set|enter a client id/i.test(raw)) {
    return 'Paste your Google OAuth Client ID (from Google Cloud Console), then Connect.'
  }
  if (/405|method not allowed/i.test(raw)) return 'Server outdated — restart Remedy.'
  if (/404|not found/i.test(raw)) return 'Server outdated — restart Remedy.'
  if (raw.length > 140) return `${raw.slice(0, 137)}…`
  return raw
}

export function AssistantSection({
  sectionProps,
  assistant,
  draft,
  setDraft,
  onAccountsChanged,
}: AssistantSectionProps): ReactNode {
  const brief = { ...(assistant?.brief || {}), ...(draft.brief || {}) }
  const enabled =
    draft.enabled !== undefined ? draft.enabled : Boolean(assistant?.enabled ?? true)
  const disclaimerAccepted =
    draft.money_disclaimer_accepted !== undefined
      ? draft.money_disclaimer_accepted
      : Boolean(assistant?.money_disclaimer_accepted)

  const [google, setGoogle] = useState<GoogleAuthStatus | null>(null)
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [authUrl, setAuthUrl] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const redirectUri = google?.app?.redirect_uri || DEFAULT_REDIRECT
  const hasSavedClient = Boolean(google?.app?.client_id_set)

  const stopPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  const refreshGoogle = useCallback(async () => {
    try {
      const g = await getGoogleStatus()
      setGoogle(g)
      return g
    } catch {
      return null
    }
  }, [])

  useEffect(() => {
    void refreshGoogle()
    return () => stopPoll()
  }, [refreshGoogle, stopPoll])

  const patchBrief = (key: string, value: boolean | number) => {
    setDraft((prev) => ({
      ...prev,
      brief: { ...(prev.brief || {}), [key]: value },
    }))
  }

  const googleConnected = Boolean(google?.connected)
  const summary = !enabled
    ? 'Off'
    : googleConnected
      ? `On · ${google?.email || 'Google'}`
      : 'On · Google not connected'

  const openConsole = async () => {
    setMsg('Opening Google Cloud in Browser rail…')
    const where = await openUrlInBrowserRail(GOOGLE_CONSOLE)
    setMsg(
      where === 'rail'
        ? 'Browser rail: create OAuth client → copy Client ID here.'
        : 'Opened Google Cloud (external). Copy Client ID back here.',
    )
  }

  const handleConnect = async () => {
    setBusy(true)
    setMsg('')
    setAuthUrl(null)
    stopPoll()
    try {
      const id = clientId.trim()
      if (!id && !hasSavedClient) {
        setMsg('Need a Client ID first — use “Get Client ID”, then paste it below.')
        setBusy(false)
        return
      }
      if (id) {
        await saveGoogleApp({
          client_id: id,
          ...(clientSecret.trim() ? { client_secret: clientSecret.trim() } : {}),
        })
        setClientSecret('')
        await refreshGoogle()
      }
      const start = await startGoogleOAuth()
      setAuthUrl(start.auth_url)
      const where = await openUrlInBrowserRail(start.auth_url)
      setMsg(
        where === 'rail'
          ? 'Sign in in the Browser rail (right). Waiting…'
          : 'Sign in in the window that opened. Waiting…',
      )
      const state = start.state
      pollRef.current = setInterval(() => {
        void (async () => {
          try {
            const poll = await pollGoogleOAuth(state)
            if (poll.status === 'connected' || poll.credentials?.connected) {
              stopPoll()
              setBusy(false)
              setAuthUrl(null)
              const email = poll.email || poll.credentials?.email
              setMsg(email ? `Connected · ${email}` : 'Connected')
              await refreshGoogle()
              onAccountsChanged?.()
            } else if (poll.status === 'error') {
              stopPoll()
              setBusy(false)
              setMsg(poll.error || 'Sign-in failed')
            }
          } catch {
            /* keep polling */
          }
        })()
      }, 2000)
    } catch (e: unknown) {
      setBusy(false)
      setMsg(shortErr(e))
    }
  }

  const handleDisconnect = async () => {
    setBusy(true)
    setMsg('')
    stopPoll()
    try {
      await disconnectGoogle()
      setMsg('Disconnected')
      setAuthUrl(null)
      await refreshGoogle()
      onAccountsChanged?.()
    } catch (e: unknown) {
      setMsg(shortErr(e))
    } finally {
      setBusy(false)
    }
  }

  const copyRedirect = async () => {
    try {
      await navigator.clipboard.writeText(redirectUri)
      setMsg('Redirect URI copied — paste it into your OAuth client.')
    } catch {
      setMsg(redirectUri)
    }
  }

  return (
    <SettingsSection {...sectionProps} summary={summary}>
      <label className="flex items-center gap-2 mb-2 cursor-pointer text-[11px]">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setDraft((p) => ({ ...p, enabled: e.target.checked }))}
          style={{ accentColor: 'var(--accent)' }}
        />
        <span style={{ color: 'var(--text-primary)' }}>Enabled</span>
      </label>

      {/* Google */}
      <div className="mb-2 text-[10px]" style={{ color: 'var(--text-secondary)' }}>
        <div className="font-medium mb-1" style={{ color: 'var(--text-primary)' }}>
          Google Calendar
        </div>
        {googleConnected ? (
          <div className="flex items-center gap-2 flex-wrap">
            <span style={{ color: 'var(--text-primary)' }}>{google?.email || 'Connected'}</span>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                void handleDisconnect()
              }}
              className="rounded px-2 py-0.5 border text-[10px]"
              style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
            >
              Disconnect
            </button>
          </div>
        ) : (
          <div className="space-y-1.5">
            <p className="m-0 leading-snug" style={{ color: 'var(--text-muted)' }}>
              Google requires your own free OAuth client (Remedy cannot sign in as “Google”
              without it). Sign-in opens in the <strong style={{ color: 'var(--text-secondary)' }}>Browser rail</strong>.
            </p>
            <ol className="m-0 pl-3.5 space-y-0.5 leading-snug" style={{ color: 'var(--text-muted)' }}>
              <li>
                <button
                  type="button"
                  className="underline p-0 border-0 bg-transparent cursor-pointer text-[10px]"
                  style={{ color: 'var(--accent)' }}
                  onClick={() => {
                    void openConsole()
                  }}
                >
                  Get Client ID
                </button>
                {' '}(Cloud Console → Credentials → Create OAuth client → Desktop or Web)
              </li>
              <li>
                Add redirect{' '}
                <button
                  type="button"
                  className="underline p-0 border-0 bg-transparent cursor-pointer text-[10px] font-mono"
                  style={{ color: 'var(--accent)' }}
                  title="Click to copy"
                  onClick={() => {
                    void copyRedirect()
                  }}
                >
                  {redirectUri}
                </button>
                {' '}· enable Calendar API
              </li>
              <li>Paste Client ID → Connect → finish sign-in in Browser rail</li>
            </ol>
            <input
              type="text"
              placeholder={hasSavedClient ? 'Client ID saved — paste to replace' : 'Paste Client ID here'}
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              className="w-full rounded border px-1.5 py-1 text-[10px] outline-none"
              style={inputStyle}
              autoComplete="off"
            />
            <input
              type="password"
              placeholder="Client secret (only if Google shows one)"
              value={clientSecret}
              onChange={(e) => setClientSecret(e.target.value)}
              className="w-full rounded border px-1.5 py-1 text-[10px] outline-none"
              style={inputStyle}
              autoComplete="off"
            />
            <div className="flex flex-wrap gap-1.5 items-center">
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  void handleConnect()
                }}
                className="rounded px-2 py-1 text-[10px]"
                style={{ background: 'var(--accent)', color: 'var(--accent-fg, #fff)' }}
              >
                {busy ? 'Waiting for Google…' : 'Connect'}
              </button>
              {authUrl ? (
                <button
                  type="button"
                  className="rounded px-2 py-1 text-[10px] border"
                  style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
                  onClick={() => {
                    void openExternalUrl(authUrl)
                    setMsg('Opened in system browser (fallback).')
                  }}
                >
                  System browser
                </button>
              ) : null}
            </div>
          </div>
        )}
        {msg ? (
          <div className="mt-1.5" style={{ color: 'var(--text-muted)' }}>
            {msg}
          </div>
        ) : null}
      </div>

      {/* Brief */}
      <div className="mb-2 text-[10px]" style={{ color: 'var(--text-secondary)' }}>
        <div className="font-medium mb-1" style={{ color: 'var(--text-primary)' }}>
          Daily brief
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <label className="flex items-center gap-1 cursor-pointer">
            <input
              type="checkbox"
              checked={Boolean(brief.enabled)}
              onChange={(e) => patchBrief('enabled', e.target.checked)}
              style={{ accentColor: 'var(--accent)' }}
            />
            On
          </label>
          <label className="flex items-center gap-1">
            Hour
            <input
              type="number"
              min={0}
              max={23}
              className="w-10 rounded border px-1 py-0.5 text-[10px]"
              style={inputStyle}
              value={Number(brief.hour_local ?? 7)}
              onChange={(e) => patchBrief('hour_local', Number(e.target.value))}
            />
          </label>
          {(
            [
              ['include_calendar', 'Cal'],
              ['include_mail', 'Mail'],
              ['include_goals', 'Goals'],
              ['include_budget', 'Budget'],
            ] as const
          ).map(([key, label]) => (
            <label key={key} className="flex items-center gap-1 cursor-pointer">
              <input
                type="checkbox"
                checked={Boolean(brief[key])}
                onChange={(e) => patchBrief(key, e.target.checked)}
                style={{ accentColor: 'var(--accent)' }}
              />
              {label}
            </label>
          ))}
        </div>
      </div>

      {/* Money */}
      <div className="text-[10px]" style={{ color: 'var(--text-secondary)' }}>
        <div className="font-medium mb-1" style={{ color: 'var(--text-primary)' }}>
          Budget / debt
        </div>
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={disclaimerAccepted}
            onChange={(e) =>
              setDraft((p) => ({ ...p, money_disclaimer_accepted: e.target.checked }))
            }
            style={{ accentColor: 'var(--accent)' }}
          />
          <span>
            Organizational only — not financial advice.
            {assistant?.has_budget || (assistant?.debt_count ?? 0) > 0
              ? ` · ${assistant?.has_budget ? 'budget set' : 'no budget'} · ${assistant?.debt_count ?? 0} debts`
              : ''}
          </span>
        </label>
      </div>
    </SettingsSection>
  )
}
