/** Settings → Personal assistant — account connect + brief + budget. */

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
import { updateSettings } from '../../api/settings'
import { SettingsSection } from '../SettingsSection'

export type AssistantDraft = {
  enabled?: boolean
  timezone?: string
  money_disclaimer_accepted?: boolean
  privacy_ai_accepted?: boolean
  account_access_accepted?: boolean
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
  privacy_ai_accepted?: boolean
  account_access_accepted?: boolean
  privacy?: {
    privacy_ai_short?: string
    privacy_ai_full?: string
    privacy_ai_checkbox?: string
    account_connect_checkbox?: string
    google_scopes_plain?: string
  }
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

const ACCOUNT_PROVIDERS = [
  { id: 'google', label: 'Google (Gmail)', ready: true },
  { id: 'microsoft', label: 'Microsoft (Outlook)', ready: false },
  { id: 'yahoo', label: 'Yahoo (Ymail!)', ready: false },
] as const

type ProviderId = (typeof ACCOUNT_PROVIDERS)[number]['id']

const REDIRECT = 'http://127.0.0.1:7400/api/assistant/google/callback'

const inputStyle = {
  borderColor: 'var(--border)',
  background: 'var(--bg-input, var(--bg-elevated))',
  color: 'var(--text-primary)',
} as const

function shortErr(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e)
  if (/privacy|account access|Accept the AI/i.test(raw)) {
    return 'Accept Privacy & AI notices below, then Connect.'
  }
  if (/not configured|client_id not set|not set/i.test(raw)) {
    return 'Google OAuth app not set on this install yet — expand “Set up once” below.'
  }
  if (/405|method not allowed|404|not found/i.test(raw)) {
    return 'Server outdated — restart Remedy.'
  }
  if (raw.length > 140) return `${raw.slice(0, 137)}…`
  return raw
}

async function openOAuthPopup(url: string): Promise<void> {
  const trimmed = (url || '').trim()
  if (!trimmed) return
  try {
    await openExternalUrl(trimmed)
  } catch {
    if (typeof window !== 'undefined') {
      window.open(trimmed, 'remedy_oauth', 'popup=yes,width=520,height=720')
    }
  }
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
  const privacyAiAccepted =
    draft.privacy_ai_accepted !== undefined
      ? draft.privacy_ai_accepted
      : Boolean(assistant?.privacy_ai_accepted)
  const accountAccessAccepted =
    draft.account_access_accepted !== undefined
      ? draft.account_access_accepted
      : Boolean(assistant?.account_access_accepted)

  const notices = assistant?.privacy
  const [privacyOpen, setPrivacyOpen] = useState(false)
  const [google, setGoogle] = useState<GoogleAuthStatus | null>(null)
  const [provider, setProvider] = useState<ProviderId>('google')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [setupOpen, setSetupOpen] = useState(false)
  const [devClientId, setDevClientId] = useState('')
  const [devSecret, setDevSecret] = useState('')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const signInReady = Boolean(google?.sign_in_ready ?? google?.app?.client_id_set)
  const googleConnected = Boolean(google?.connected)

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
      // Auto-open one-time setup when product OAuth is missing
      if (!g.sign_in_ready && !g.app?.client_id_set && !g.connected) {
        setSetupOpen(true)
      }
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

  const summary = !enabled
    ? 'Off'
    : googleConnected
      ? `On · ${google?.email || 'Google'}`
      : 'On'

  const selected = ACCOUNT_PROVIDERS.find((p) => p.id === provider) || ACCOUNT_PROVIDERS[0]

  const handleSaveApp = async () => {
    const id = devClientId.trim()
    if (!id) {
      setMsg('Paste the OAuth Client ID from Google Cloud Console.')
      return
    }
    setBusy(true)
    setMsg('')
    try {
      await saveGoogleApp({
        client_id: id,
        ...(devSecret.trim() ? { client_secret: devSecret.trim() } : {}),
        redirect_uri: REDIRECT,
      })
      setDevSecret('')
      setMsg('Saved. Click Connect to sign in with Google.')
      setSetupOpen(false)
      await refreshGoogle()
    } catch (e: unknown) {
      setMsg(shortErr(e))
    } finally {
      setBusy(false)
    }
  }

  const persistConsent = async (patch: {
    privacy_ai_accepted?: boolean
    account_access_accepted?: boolean
    money_disclaimer_accepted?: boolean
  }) => {
    await updateSettings({ assistant: patch })
  }

  const handleConnect = async () => {
    setMsg('')
    if (!selected.ready) {
      setMsg(`${selected.label} — coming soon.`)
      return
    }
    if (provider !== 'google') {
      setMsg('Only Google (Gmail) is available right now.')
      return
    }
    if (googleConnected) {
      setMsg(google?.email ? `Already connected as ${google.email}` : 'Already connected.')
      return
    }
    if (!privacyAiAccepted || !accountAccessAccepted) {
      setMsg('Accept Privacy & AI + account access below, then Connect.')
      return
    }
    if (!signInReady) {
      setSetupOpen(true)
      setMsg('Set up Google OAuth once below, then Connect.')
      return
    }

    setBusy(true)
    stopPoll()
    try {
      // Ensure consent is on disk before OAuth (not only in UI draft)
      await persistConsent({
        privacy_ai_accepted: true,
        account_access_accepted: true,
      })
      const start = await startGoogleOAuth()
      setMsg('Complete sign-in in the browser window…')
      await openOAuthPopup(start.auth_url)
      const state = start.state
      pollRef.current = setInterval(() => {
        void (async () => {
          try {
            const poll = await pollGoogleOAuth(state)
            if (poll.status === 'connected' || poll.credentials?.connected) {
              stopPoll()
              setBusy(false)
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
      if (/not configured|not set/i.test(String(e instanceof Error ? e.message : e))) {
        setSetupOpen(true)
      }
    }
  }

  const handleDisconnect = async () => {
    setBusy(true)
    setMsg('')
    stopPoll()
    try {
      await disconnectGoogle()
      setMsg('Disconnected')
      await refreshGoogle()
      onAccountsChanged?.()
    } catch (e: unknown) {
      setMsg(shortErr(e))
    } finally {
      setBusy(false)
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

      {/* Privacy & AI — required before Connect */}
      <div
        className="mb-2 rounded border p-1.5 text-[10px] leading-snug"
        style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
      >
        <div className="font-medium mb-1" style={{ color: 'var(--text-primary)' }}>
          Privacy & AI
        </div>
        <p className="m-0 mb-1" style={{ color: 'var(--text-muted)' }}>
          {notices?.privacy_ai_short ||
            'Tokens stay on this PC. Chat may send tool results (not tokens) to your chosen AI provider.'}
        </p>
        <button
          type="button"
          className="p-0 border-0 bg-transparent underline cursor-pointer text-[10px] mb-1"
          style={{ color: 'var(--accent)' }}
          onClick={() => setPrivacyOpen((v) => !v)}
        >
          {privacyOpen ? 'Hide full notice' : 'Read full privacy notice'}
        </button>
        {privacyOpen ? (
          <pre
            className="m-0 mb-1.5 whitespace-pre-wrap font-sans text-[9px] max-h-40 overflow-y-auto rounded p-1"
            style={{
              background: 'var(--bg-elevated)',
              color: 'var(--text-muted)',
            }}
          >
            {notices?.privacy_ai_full || ''}
          </pre>
        ) : null}
        <label className="flex items-start gap-2 cursor-pointer mb-1">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={privacyAiAccepted}
            onChange={(e) => {
              const v = e.target.checked
              setDraft((p) => ({ ...p, privacy_ai_accepted: v }))
              void persistConsent({ privacy_ai_accepted: v }).catch(() => {
                /* save with Settings if offline */
              })
            }}
            style={{ accentColor: 'var(--accent)' }}
          />
          <span style={{ color: 'var(--text-primary)' }}>
            {notices?.privacy_ai_checkbox ||
              'I understand Remedy is an AI assistant; tokens stay local; tool results may go to my AI provider.'}
          </span>
        </label>
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={accountAccessAccepted}
            onChange={(e) => {
              const v = e.target.checked
              setDraft((p) => ({ ...p, account_access_accepted: v }))
              void persistConsent({ account_access_accepted: v }).catch(() => {})
            }}
            style={{ accentColor: 'var(--accent)' }}
          />
          <span style={{ color: 'var(--text-primary)' }}>
            {notices?.account_connect_checkbox ||
              'I allow official OAuth access for mail/calendar tools I use (Disconnect anytime).'}
          </span>
        </label>
        {notices?.google_scopes_plain ? (
          <p className="m-0 mt-1" style={{ color: 'var(--text-muted)' }}>
            Google access: {notices.google_scopes_plain}
          </p>
        ) : null}
      </div>

      <div className="mb-2 text-[10px]" style={{ color: 'var(--text-secondary)' }}>
        <div className="font-medium mb-1" style={{ color: 'var(--text-primary)' }}>
          Accounts
        </div>

        {googleConnected ? (
          <div className="flex flex-wrap items-center gap-2 mb-1.5">
            <span style={{ color: 'var(--text-primary)' }}>
              Google (Gmail) · {google?.email || 'connected'}
            </span>
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
        ) : null}

        <div className="flex flex-wrap items-center gap-1.5">
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value as ProviderId)}
            disabled={busy}
            className="rounded border px-1.5 py-1 text-[10px] outline-none min-w-[10rem]"
            style={inputStyle}
          >
            {ACCOUNT_PROVIDERS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
                {!p.ready ? ' — soon' : ''}
              </option>
            ))}
          </select>
          <button
            type="button"
            disabled={busy || (provider === 'google' && googleConnected)}
            onClick={() => {
              void handleConnect()
            }}
            className="rounded px-2.5 py-1 text-[10px]"
            style={{
              background: 'var(--accent)',
              color: 'var(--accent-fg, #fff)',
              opacity: busy || (provider === 'google' && googleConnected) ? 0.6 : 1,
            }}
          >
            {busy ? 'Waiting…' : 'Connect'}
          </button>
        </div>

        {/* One-time product OAuth setup — only when this install has no client */}
        {!googleConnected && !signInReady ? (
          <div className="mt-1.5">
            <button
              type="button"
              className="text-[10px] p-0 border-0 bg-transparent cursor-pointer underline"
              style={{ color: 'var(--accent)' }}
              onClick={() => setSetupOpen((v) => !v)}
            >
              {setupOpen ? 'Hide setup' : 'Set up once (required for Google sign-in)'}
            </button>
            {setupOpen ? (
              <div
                className="mt-1 rounded border p-1.5 space-y-1"
                style={{ borderColor: 'var(--border)' }}
              >
                <p className="m-0 leading-snug" style={{ color: 'var(--text-muted)' }}>
                  Google requires a free OAuth client for <em>this app</em> (one-time). Create
                  Desktop/Web client with redirect{' '}
                  <code className="text-[9px]" style={{ color: 'var(--accent)' }}>
                    {REDIRECT}
                  </code>
                  , enable Gmail + Calendar APIs, paste Client ID here. End users never do this
                  when the build ships with credentials.
                </p>
                <input
                  type="text"
                  placeholder="OAuth Client ID"
                  value={devClientId}
                  onChange={(e) => setDevClientId(e.target.value)}
                  className="w-full rounded border px-1.5 py-1 text-[10px] outline-none"
                  style={inputStyle}
                  autoComplete="off"
                />
                <input
                  type="password"
                  placeholder="Client secret (if Google shows one)"
                  value={devSecret}
                  onChange={(e) => setDevSecret(e.target.value)}
                  className="w-full rounded border px-1.5 py-1 text-[10px] outline-none"
                  style={inputStyle}
                  autoComplete="off"
                />
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    void handleSaveApp()
                  }}
                  className="rounded px-2 py-1 text-[10px] border"
                  style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
                >
                  Save & enable Connect
                </button>
              </div>
            ) : null}
          </div>
        ) : null}

        {msg ? (
          <div className="mt-1" style={{ color: 'var(--text-muted)' }}>
            {msg}
          </div>
        ) : null}
      </div>

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
