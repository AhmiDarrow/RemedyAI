/** Settings → Personal assistant — simple account connect + brief + budget. */

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
  onAccountsChanged?: () => void
}

/** Mail / calendar platforms shown in the account dropdown. */
const ACCOUNT_PROVIDERS = [
  {
    id: 'google',
    label: 'Google (Calendar)',
    ready: true,
  },
  {
    id: 'microsoft',
    label: 'Microsoft (Outlook)',
    ready: false,
  },
  {
    id: 'yahoo',
    label: 'Yahoo Mail',
    ready: false,
  },
] as const

type ProviderId = (typeof ACCOUNT_PROVIDERS)[number]['id']

const inputStyle = {
  borderColor: 'var(--border)',
  background: 'var(--bg-input, var(--bg-elevated))',
  color: 'var(--text-primary)',
} as const

function shortErr(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e)
  if (/client_id not set|not configured|not set/i.test(raw)) {
    return 'Google sign-in isn’t available in this build yet. Restart after update, or set REMEDY_GOOGLE_OAUTH_CLIENT_ID.'
  }
  if (/405|method not allowed|404|not found/i.test(raw)) {
    return 'Server outdated — restart Remedy.'
  }
  if (raw.length > 140) return `${raw.slice(0, 137)}…`
  return raw
}

/** Standard OAuth popup (system browser window). Fallback: default external open. */
async function openOAuthPopup(url: string): Promise<void> {
  const trimmed = (url || '').trim()
  if (!trimmed) return
  try {
    // Prefer OS browser — Google expects a real browser for OAuth, not a form paste.
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

  const [google, setGoogle] = useState<GoogleAuthStatus | null>(null)
  const [provider, setProvider] = useState<ProviderId>('google')
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

  const googleConnected = Boolean(google?.connected)
  const summary = !enabled
    ? 'Off'
    : googleConnected
      ? `On · ${google?.email || 'Google'}`
      : 'On'

  const selected = ACCOUNT_PROVIDERS.find((p) => p.id === provider) || ACCOUNT_PROVIDERS[0]

  const handleConnect = async () => {
    setMsg('')
    if (!selected.ready) {
      setMsg(`${selected.label} — coming soon.`)
      return
    }
    if (provider !== 'google') {
      setMsg('Only Google is available right now.')
      return
    }
    if (googleConnected) {
      setMsg(google?.email ? `Already connected as ${google.email}` : 'Already connected.')
      return
    }

    setBusy(true)
    stopPoll()
    try {
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

      {/* Accounts — dropdown + Connect (no Client ID UI) */}
      <div className="mb-2 text-[10px]" style={{ color: 'var(--text-secondary)' }}>
        <div className="font-medium mb-1" style={{ color: 'var(--text-primary)' }}>
          Accounts
        </div>

        {googleConnected ? (
          <div className="flex flex-wrap items-center gap-2 mb-1.5">
            <span style={{ color: 'var(--text-primary)' }}>
              Google · {google?.email || 'connected'}
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
        {msg ? (
          <div className="mt-1" style={{ color: 'var(--text-muted)' }}>
            {msg}
          </div>
        ) : null}
      </div>

      {/* Daily brief — kept as requested */}
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

      {/* Budget */}
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
