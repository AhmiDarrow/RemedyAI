import { getServerUrl } from '../../api/client'
/** Modal: privacy + optional app OAuth setup + Connect — keeps Settings lean. */

import { useState, type ReactNode } from 'react'
import { saveGoogleApp } from '../../api/assistant'
import { updateSettings } from '../../api/settings'
import { RemedyLogo } from '../RemedyLogo'

const REDIRECT = () => getServerUrl() + '/api/assistant/google/callback'

export type ConnectNotices = {
  privacy_ai_short?: string
  privacy_ai_full?: string
  privacy_ai_checkbox?: string
  account_connect_checkbox?: string
  google_scopes_plain?: string
}

export interface AssistantConnectDialogProps {
  open: boolean
  providerLabel: string
  notices?: ConnectNotices | null
  signInReady: boolean
  busy?: boolean
  onClose: () => void
  onContinue: () => void | Promise<void>
}

export function AssistantConnectDialog({
  open,
  providerLabel,
  notices,
  signInReady,
  busy = false,
  onClose,
  onContinue,
}: AssistantConnectDialogProps): ReactNode {
  const [privacyAi, setPrivacyAi] = useState(true)
  const [accountAccess, setAccountAccess] = useState(true)
  const [showFull, setShowFull] = useState(false)
  const [clientId, setClientId] = useState('')
  const [clientSecret, setClientSecret] = useState('')
  const [err, setErr] = useState('')
  const [working, setWorking] = useState(false)

  if (!open) return null

  const canContinue = privacyAi && accountAccess && (signInReady || Boolean(clientId.trim()))

  const submit = async () => {
    setErr('')
    if (!privacyAi || !accountAccess) {
      setErr('Accept both notices to continue.')
      return
    }
    setWorking(true)
    try {
      await updateSettings({
        assistant: {
          privacy_ai_accepted: true,
          account_access_accepted: true,
        },
      })
      if (!signInReady) {
        const id = clientId.trim()
        if (!id) {
          setErr('Client ID required for this install.')
          setWorking(false)
          return
        }
        await saveGoogleApp({
          client_id: id,
          ...(clientSecret.trim() ? { client_secret: clientSecret.trim() } : {}),
          redirect_uri: REDIRECT(),
        })
      }
      await onContinue()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setWorking(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center p-4 ui-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="pa-connect-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !working && !busy) onClose()
      }}
    >
      <div
        className="ui-surface w-full max-w-md p-5 max-h-[90vh] overflow-y-auto"
        style={{ color: 'var(--text-primary)' }}
      >
        <div className="flex items-center gap-3 mb-3">
          <RemedyLogo size={32} framed />
          <div>
            <div id="pa-connect-title" className="font-semibold text-sm">
              Connect {providerLabel}
            </div>
            <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
              Secure · local-first · official sign-in
            </div>
          </div>
        </div>

        <p className="text-[11px] leading-snug m-0 mb-3" style={{ color: 'var(--text-secondary)' }}>
          {notices?.privacy_ai_short ||
            'Tokens stay on this PC. Chat may send tool results (not tokens) to your chosen AI provider.'}
        </p>

        <button
          type="button"
          className="text-[11px] p-0 border-0 bg-transparent underline cursor-pointer mb-2"
          style={{ color: 'var(--accent)' }}
          onClick={() => setShowFull((v) => !v)}
        >
          {showFull ? 'Less' : 'Privacy details'}
        </button>
        {showFull ? (
          <pre
            className="m-0 mb-3 whitespace-pre-wrap font-sans text-[10px] max-h-36 overflow-y-auto rounded-lg p-2"
            style={{ background: 'var(--bg-primary)', color: 'var(--text-muted)' }}
          >
            {notices?.privacy_ai_full || ''}
            {notices?.google_scopes_plain
              ? `\n\nAccess: ${notices.google_scopes_plain}`
              : ''}
          </pre>
        ) : null}

        <label className="flex items-start gap-2 cursor-pointer mb-2 text-[11px]">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={privacyAi}
            onChange={(e) => setPrivacyAi(e.target.checked)}
            style={{ accentColor: 'var(--accent)' }}
          />
          <span>
            {notices?.privacy_ai_checkbox ||
              'I understand Remedy is an AI assistant; tokens stay local; tool results may go to my AI provider.'}
          </span>
        </label>
        <label className="flex items-start gap-2 cursor-pointer mb-3 text-[11px]">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={accountAccess}
            onChange={(e) => setAccountAccess(e.target.checked)}
            style={{ accentColor: 'var(--accent)' }}
          />
          <span>
            {notices?.account_connect_checkbox ||
              'Allow official OAuth for mail/calendar tools (Disconnect anytime).'}
          </span>
        </label>

        {!signInReady && (
          <div className="mb-3 space-y-1.5">
            <div className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
              This install needs an OAuth Client ID (one-time). Redirect:{' '}
              <code className="text-[9px]">{REDIRECT()}</code>
            </div>
            <input
              type="text"
              placeholder="Client ID"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              className="ui-input"
              autoComplete="off"
            />
            <input
              type="password"
              placeholder="Client secret (if any)"
              value={clientSecret}
              onChange={(e) => setClientSecret(e.target.value)}
              className="ui-input"
              autoComplete="off"
            />
          </div>
        )}

        {err ? (
          <div className="text-[11px] mb-2" style={{ color: 'var(--error)' }}>
            {err}
          </div>
        ) : null}

        <div className="flex gap-2 justify-end">
          <button
            type="button"
            disabled={working || busy}
            className="ui-btn ui-btn-secondary"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={!canContinue || working || busy}
            className="ui-btn ui-btn-primary"
            onClick={() => {
              void submit()
            }}
          >
            {working || busy ? 'Working…' : 'Continue'}
          </button>
        </div>
      </div>
    </div>
  )
}
