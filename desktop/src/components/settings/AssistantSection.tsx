/** Settings → Personal assistant — lean; privacy/OAuth in Connect modal. */

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
import type { SettingsMode } from '../../utils/settingsMode'
import { SettingsSection } from '../SettingsSection'
import { AssistantConnectDialog } from './AssistantConnectDialog'
import {
  FormActionButton,
  FormHint,
  FormLabel,
  FormLinkButton,
  FormNotice,
  FormSelect,
  FormToggle,
} from './formUi'

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
  consent_version?: string
  current_consent_version?: string
  consent_ok?: boolean
  consent_reason?: string
  /** True when prior accept is stale after scope/terms bump. */
  needs_reaccept?: boolean
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
  settingsMode?: SettingsMode
}

const ACCOUNT_PROVIDERS = [
  { id: 'google', label: 'Google (Gmail)', ready: true },
  { id: 'microsoft', label: 'Microsoft (Outlook)', ready: false },
  { id: 'yahoo', label: 'Yahoo (Ymail!)', ready: false },
] as const

type ProviderId = (typeof ACCOUNT_PROVIDERS)[number]['id']

function shortErr(e: unknown): string {
  const raw = e instanceof Error ? e.message : String(e)
  if (/privacy|account access|Accept the AI/i.test(raw)) {
    return 'Accept the notices in Connect.'
  }
  if (/not configured|client_id|not set/i.test(raw)) {
    return 'OAuth app missing — enter Client ID in Connect.'
  }
  if (/405|method not allowed|404|not found/i.test(raw)) {
    return 'Server outdated — restart Remedy.'
  }
  if (raw.length > 120) return `${raw.slice(0, 117)}…`
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
  settingsMode = 'simple',
}: AssistantSectionProps): ReactNode {
  const advanced = settingsMode === 'advanced'
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
  const [dialogOpen, setDialogOpen] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const signInReady = Boolean(google?.sign_in_ready ?? google?.app?.client_id_set)
  const googleConnected = Boolean(google?.connected)
  const selected = ACCOUNT_PROVIDERS.find((p) => p.id === provider) || ACCOUNT_PROVIDERS[0]

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

  const summary = !enabled
    ? 'Off'
    : googleConnected
      ? `${google?.email || 'Connected'}`
      : 'On'

  const runOAuth = async () => {
    setBusy(true)
    setMsg('')
    stopPoll()
    try {
      const start = await startGoogleOAuth()
      setDialogOpen(false)
      setMsg('Sign in in the browser…')
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

  const handleConnectClick = () => {
    setMsg('')
    if (!selected.ready) {
      setMsg(`${selected.label} — soon.`)
      return
    }
    if (provider !== 'google') {
      setMsg('Google (Gmail) only for now.')
      return
    }
    if (googleConnected) return
    setDialogOpen(true)
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
    <>
      <SettingsSection {...sectionProps} summary={summary}>
        <FormToggle
          checked={enabled}
          onChange={(on) => setDraft((p) => ({ ...p, enabled: on }))}
          label="Personal assistant"
          description="Mail, calendar, and brief tools when an account is connected."
        />

        {assistant?.needs_reaccept ? (
          <FormNotice tone="warn">
            <div className="flex flex-wrap items-center gap-2">
              <span className="flex-1 min-w-[10rem]">
                {assistant.consent_reason
                  || 'Privacy terms or account scopes were updated — re-accept before Connect or account tools.'}
              </span>
              <FormActionButton
                variant="primary"
                onClick={() => {
                  setMsg('')
                  setDialogOpen(true)
                }}
              >
                Review &amp; accept
              </FormActionButton>
            </div>
          </FormNotice>
        ) : null}

        {googleConnected && google?.tokens_encoding_warning ? (
          <FormNotice tone="warn">{google.tokens_encoding_warning}</FormNotice>
        ) : null}

        {googleConnected && google?.apis_warning ? (
          <FormNotice tone="warn">
            {google.apis_warning}
            {google.apis?.enable_gmail_url ? (
              <div className="mt-1">
                Enable Gmail API in Google Cloud Console, then retry tools.
              </div>
            ) : null}
          </FormNotice>
        ) : null}

        <FormLabel>Account</FormLabel>
        {googleConnected ? (
          <div className="flex flex-wrap items-center gap-2 mb-2">
            <span className="text-xs" style={{ color: 'var(--text-primary)' }}>
              {google?.email || 'Connected'}
              {google?.tokens_encoding === 'dpapi' ? ' · sealed' : ''}
            </span>
            <FormLinkButton
              onClick={() => {
                void handleDisconnect()
              }}
            >
              {busy ? '…' : 'Disconnect'}
            </FormLinkButton>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-1.5 mb-2">
            <FormSelect
              value={provider}
              onChange={(v) => setProvider(v as ProviderId)}
              disabled={busy}
              className="mb-0 min-w-[9.5rem] flex-1"
              size="sm"
            >
              {ACCOUNT_PROVIDERS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                  {!p.ready ? ' · soon' : ''}
                </option>
              ))}
            </FormSelect>
            <FormActionButton
              variant="primary"
              disabled={busy}
              onClick={handleConnectClick}
            >
              {busy ? '…' : 'Connect'}
            </FormActionButton>
          </div>
        )}
        {msg ? <FormHint>{msg}</FormHint> : null}

        <FormLabel className="mt-2">Morning brief</FormLabel>
        <div
          className="flex flex-wrap items-center gap-x-3 gap-y-1.5 mb-2 text-[10px]"
          style={{ color: 'var(--text-secondary)' }}
        >
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
            <input
              type="number"
              min={0}
              max={23}
              className="ui-input ui-input-sm w-10"
              value={Number(brief.hour_local ?? 7)}
              onChange={(e) => patchBrief('hour_local', Number(e.target.value))}
              aria-label="Brief hour (local)"
            />
            h
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

        <FormToggle
          checked={disclaimerAccepted}
          onChange={(on) => setDraft((p) => ({ ...p, money_disclaimer_accepted: on }))}
          label="Budget tools — organize only, not financial advice"
          description={
            advanced && (assistant?.has_budget || (assistant?.debt_count ?? 0) > 0)
              ? `${assistant?.debt_count ?? 0} debts tracked`
              : undefined
          }
        />
      </SettingsSection>

      <AssistantConnectDialog
        open={dialogOpen}
        providerLabel={selected.label}
        notices={assistant?.privacy}
        signInReady={signInReady}
        busy={busy}
        onClose={() => {
          if (!busy) setDialogOpen(false)
        }}
        onContinue={runOAuth}
      />
    </>
  )
}
