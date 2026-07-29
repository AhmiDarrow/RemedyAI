/** Settings → Personal assistant — Google Calendar OAuth, brief prefs, money disclaimer. */

import { useCallback, useEffect, useRef, useState, type Dispatch, type ReactNode, type SetStateAction } from 'react'
import {
  disconnectGoogle,
  getGoogleStatus,
  pollGoogleOAuth,
  saveGoogleApp,
  startGoogleOAuth,
  type GoogleAuthStatus,
} from '../../api/assistant'
import { openExternalUrl } from '../../api/auth'
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
  /** Called after Google connect/disconnect so parent can reload settings. */
  onAccountsChanged?: () => void
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
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

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

  const providers = assistant?.providers_planned || [
    { id: 'google', name: 'Google (Calendar now · Gmail soon)', status: 'planned' },
    { id: 'microsoft', name: 'Microsoft (Outlook/Hotmail)', status: 'planned' },
    { id: 'yahoo', name: 'Yahoo Mail', status: 'planned' },
  ]

  const googleConnected = Boolean(google?.connected)
  const summaryParts = enabled
    ? [
        'On',
        googleConnected ? `Google ${google?.email || 'ok'}` : 'Google off',
        `budget ${assistant?.has_budget ? 'set' : '—'}`,
        `${assistant?.debt_count ?? 0} debts`,
      ].join(' · ')
    : 'Off'

  const handleSaveClient = async () => {
    setBusy(true)
    setMsg('')
    try {
      const body: { client_id?: string; client_secret?: string } = {}
      if (clientId.trim()) body.client_id = clientId.trim()
      if (clientSecret.trim()) body.client_secret = clientSecret.trim()
      if (!body.client_id && !body.client_secret) {
        setMsg('Enter Client ID (and secret if your Google app uses one).')
        return
      }
      await saveGoogleApp(body)
      setClientSecret('')
      setMsg('Google OAuth app saved locally (DPAPI on Windows).')
      await refreshGoogle()
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleConnect = async () => {
    setBusy(true)
    setMsg('')
    stopPoll()
    try {
      // Persist client id first if typed
      if (clientId.trim()) {
        await saveGoogleApp({
          client_id: clientId.trim(),
          ...(clientSecret.trim() ? { client_secret: clientSecret.trim() } : {}),
        })
        setClientSecret('')
      }
      const start = await startGoogleOAuth()
      setMsg(start.message || 'Complete sign-in in the browser…')
      void openExternalUrl(start.auth_url)
      const state = start.state
      pollRef.current = setInterval(() => {
        void (async () => {
          try {
            const poll = await pollGoogleOAuth(state)
            if (poll.status === 'connected' || poll.credentials?.connected) {
              stopPoll()
              setBusy(false)
              setMsg(`Connected${poll.email || poll.credentials?.email ? ` as ${poll.email || poll.credentials?.email}` : ''}.`)
              await refreshGoogle()
              onAccountsChanged?.()
            } else if (poll.status === 'error') {
              stopPoll()
              setBusy(false)
              setMsg(poll.error || 'Google sign-in failed')
            }
          } catch {
            /* keep polling */
          }
        })()
      }, 2000)
    } catch (e: unknown) {
      setBusy(false)
      setMsg(e instanceof Error ? e.message : String(e))
    }
  }

  const handleDisconnect = async () => {
    setBusy(true)
    setMsg('')
    stopPoll()
    try {
      await disconnectGoogle()
      setMsg('Google disconnected.')
      await refreshGoogle()
      onAccountsChanged?.()
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <SettingsSection {...sectionProps} summary={summaryParts}>
      <div className="text-[10px] leading-snug mb-2" style={{ color: 'var(--text-muted)' }}>
        Official Google sign-in for Calendar (not computer-use login). Gmail and Microsoft/Yahoo
        mail come next. Budget tools stay local.
      </div>

      <label className="flex items-center gap-2 mb-2 cursor-pointer">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setDraft((p) => ({ ...p, enabled: e.target.checked }))}
          style={{ accentColor: 'var(--accent)' }}
        />
        <span style={{ color: 'var(--text-primary)' }}>Personal assistant features enabled</span>
      </label>

      <div
        className="rounded border p-2 text-[10px] leading-snug mb-2"
        style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
      >
        <div className="font-medium mb-1" style={{ color: 'var(--text-primary)' }}>
          Google Calendar
        </div>
        <p className="m-0 mb-1.5" style={{ color: 'var(--text-muted)' }}>
          1) Google Cloud Console → OAuth client (Desktop, or Web with redirect{' '}
          <code style={{ color: 'var(--accent)' }}>
            {google?.app?.redirect_uri || 'http://127.0.0.1:7400/api/assistant/google/callback'}
          </code>
          ). Enable Calendar API. 2) Paste Client ID below. 3) Connect.
        </p>
        {googleConnected ? (
          <div className="mb-1.5" style={{ color: 'var(--text-primary)' }}>
            Connected{google?.email ? ` · ${google.email}` : ''} · calendar.events
          </div>
        ) : (
          <div className="mb-1.5" style={{ color: 'var(--text-muted)' }}>
            {google?.app?.client_id_set
              ? 'Client ID saved — not connected yet'
              : google?.setup_hint || 'Client ID not set'}
          </div>
        )}
        {!googleConnected && (
          <div className="space-y-1 mb-1.5">
            <input
              type="text"
              placeholder="OAuth Client ID"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              className="w-full rounded border px-1.5 py-1 text-[10px] outline-none"
              style={{
                borderColor: 'var(--border)',
                background: 'var(--bg-input, var(--bg-elevated))',
                color: 'var(--text-primary)',
              }}
            />
            <input
              type="password"
              placeholder="Client secret (optional for some Desktop clients)"
              value={clientSecret}
              onChange={(e) => setClientSecret(e.target.value)}
              className="w-full rounded border px-1.5 py-1 text-[10px] outline-none"
              style={{
                borderColor: 'var(--border)',
                background: 'var(--bg-input, var(--bg-elevated))',
                color: 'var(--text-primary)',
              }}
            />
          </div>
        )}
        <div className="flex flex-wrap gap-1.5">
          {!googleConnected && (
            <>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  void handleSaveClient()
                }}
                className="rounded px-2 py-1 text-[10px] border"
                style={{
                  borderColor: 'var(--border)',
                  color: 'var(--text-secondary)',
                  background: 'var(--bg-elevated)',
                }}
              >
                Save app
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  void handleConnect()
                }}
                className="rounded px-2 py-1 text-[10px]"
                style={{
                  background: 'var(--accent)',
                  color: 'var(--accent-fg, #fff)',
                }}
              >
                {busy ? 'Waiting…' : 'Connect Google'}
              </button>
            </>
          )}
          {googleConnected && (
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                void handleDisconnect()
              }}
              className="rounded px-2 py-1 text-[10px] border"
              style={{
                borderColor: 'var(--border)',
                color: 'var(--text-secondary)',
                background: 'var(--bg-elevated)',
              }}
            >
              Disconnect
            </button>
          )}
        </div>
        {msg && (
          <div className="mt-1.5" style={{ color: 'var(--text-muted)' }}>
            {msg}
          </div>
        )}
        <div className="mt-1" style={{ color: 'var(--text-muted)' }}>
          Chat tools: <code>calendar_list_events</code>, <code>calendar_create_event</code>,{' '}
          <code>assistant_brief</code>
        </div>
      </div>

      <div className="mb-2">
        <div className="text-[10px] font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>
          Other providers
        </div>
        <ul className="text-[10px] space-y-0.5 m-0 pl-3" style={{ color: 'var(--text-muted)' }}>
          {providers
            .filter((p) => p.id !== 'google')
            .map((p) => (
              <li key={p.id}>
                {p.name} — <em>{p.status}</em>
              </li>
            ))}
        </ul>
        {(assistant?.accounts || []).length > 0 && (
          <ul className="text-[10px] mt-1 m-0 pl-3" style={{ color: 'var(--text-primary)' }}>
            {assistant!.accounts!.map((a) => (
              <li key={a.id || a.provider}>
                {a.provider}: {a.status} {a.email || ''}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="mb-2">
        <div className="text-[10px] font-medium mb-1" style={{ color: 'var(--text-secondary)' }}>
          Daily brief (opt-in)
        </div>
        <label className="flex items-center gap-2 mb-1 cursor-pointer">
          <input
            type="checkbox"
            checked={Boolean(brief.enabled)}
            onChange={(e) => patchBrief('enabled', e.target.checked)}
            style={{ accentColor: 'var(--accent)' }}
          />
          <span style={{ color: 'var(--text-primary)' }}>Enable scheduled brief</span>
        </label>
        <label className="flex items-center gap-2 text-[10px]" style={{ color: 'var(--text-secondary)' }}>
          Hour (local)
          <input
            type="number"
            min={0}
            max={23}
            className="w-12 rounded border px-1 py-0.5 text-[10px]"
            style={{
              borderColor: 'var(--border)',
              background: 'var(--bg-input, var(--bg-elevated))',
              color: 'var(--text-primary)',
            }}
            value={Number(brief.hour_local ?? 7)}
            onChange={(e) => patchBrief('hour_local', Number(e.target.value))}
          />
        </label>
        <div className="flex flex-wrap gap-2 mt-1 text-[10px]" style={{ color: 'var(--text-secondary)' }}>
          {(
            [
              ['include_calendar', 'Calendar'],
              ['include_mail', 'Mail'],
              ['include_goals', 'Goals'],
              ['include_budget', 'Budget'],
              ['messenger_delivery', 'Messenger delivery'],
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

      <div
        className="rounded border p-2 text-[10px] leading-snug mb-1"
        style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
      >
        <strong style={{ color: 'var(--text-primary)' }}>Money tools</strong>
        <p className="m-0 mt-1">
          {assistant?.money_disclaimer ||
            'Budgeting and debt tracking help you organize numbers you enter — not personalized financial, tax, or legal advice.'}
        </p>
        <label className="flex items-center gap-2 mt-2 cursor-pointer">
          <input
            type="checkbox"
            checked={disclaimerAccepted}
            onChange={(e) =>
              setDraft((p) => ({ ...p, money_disclaimer_accepted: e.target.checked }))
            }
            style={{ accentColor: 'var(--accent)' }}
          />
          <span style={{ color: 'var(--text-primary)' }}>I understand — organizational tools only</span>
        </label>
        <div className="mt-1" style={{ color: 'var(--text-muted)' }}>
          Local data: budget {assistant?.has_budget ? 'yes' : 'no'} · debts{' '}
          {assistant?.debt_count ?? 0} · bills {assistant?.bill_count ?? 0}. Chat tools:{' '}
          <code>budget_set</code>, <code>debt_upsert</code>, <code>assistant_brief</code>, …
        </div>
      </div>
    </SettingsSection>
  )
}
