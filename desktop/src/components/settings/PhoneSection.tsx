/** Settings → Phone — line options and terms. Real PSTN is not on this PC yet. */

import { useCallback, useEffect, useState, type ReactNode } from 'react'
import {
  acceptTelephonyTerms,
  chooseTelephonyLine,
  getTelephonyStatus,
  type TelephonyStatus,
} from '../../api/telephony'
import { SettingsSection } from '../SettingsSection'
import { SettingsFlow } from './SettingsFlow'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import { openExternalUrl } from '../../api/auth'
import phoneTerms from '../../../../docs/TELEPHONY_TERMS.md?raw'
import { FormActionButton, FormHint, FormNotice } from './formUi'

type SectionProps = {
  id: string
  title: string
  summary: string
  keywords: string
  forceOpen?: boolean
  hidden?: boolean
  onOpenChange?: (open: boolean) => void
}

export function PhoneSection({
  sectionProps,
}: {
  sectionProps: SectionProps
}): ReactNode {
  const [st, setSt] = useState<TelephonyStatus | null>(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setSt(await getTelephonyStatus())
    } catch (err) {
      setMsg(err instanceof Error ? err.message : 'Could not load phone settings.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const agree = async () => {
    setBusy(true)
    setMsg('')
    try {
      await acceptTelephonyTerms(true)
      await refresh()
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const pick = async (name: string) => {
    setBusy(true)
    setMsg('')
    try {
      const r = await chooseTelephonyLine(name)
      if (!r.ok) setMsg(r.error || 'That line is not on this computer.')
      await refresh()
    } catch (err) {
      setMsg(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <SettingsSection {...sectionProps}>
      <FormHint>
        {st?.message
          || (loading ? 'Loading phone settings…' : 'Phone status is unavailable. Try again to check your setup.')}
      </FormHint>
      {loading ? <FormHint>Checking phone setup…</FormHint> : !st ? (
        <FormActionButton onClick={() => { setMsg(''); void refresh() }}>Retry phone settings</FormActionButton>
      ) : (
        <SettingsFlow title="Phone terms"
          summary={st.terms.agreed && !st.terms.stale ? 'Accepted on this computer' : 'Review before enabling calls'} busy={busy}>
          <div className="settings-terms"><ReactMarkdown
            urlTransform={url => url === '../LICENSE'
              ? 'https://github.com/AhmiDarrow/RemedyAI/blob/master/LICENSE'
              : url === './THIRD_PARTY.md'
                ? 'https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/THIRD_PARTY.md'
                : defaultUrlTransform(url)}
            components={{ a: ({ href, children }) => <a href={href} onClick={event => {
              event.preventDefault()
              if (href) void openExternalUrl(href)
            }}>{children}</a> }}
          >{phoneTerms}</ReactMarkdown></div>
          {!st.terms.agreed || st.terms.stale ? <>
            <FormHint>{st.terms.ask || 'Read the terms above before agreeing.'}</FormHint>
            <FormActionButton disabled={busy} onClick={() => void agree()}>
              I agree to the phone terms
            </FormActionButton>
          </> : <FormHint>Phone terms are agreed on this computer.</FormHint>}
        </SettingsFlow>
      )}
      {(st?.lines || [])
        .filter((l) => l.achievable)
        .map((l) => (
          <button
            key={l.name}
            type="button"
            className="w-full text-left rounded-lg px-3 py-2 mb-1"
            style={{
              background:
                st?.chosen === l.name
                  ? 'color-mix(in srgb, var(--accent) 12%, var(--bg-primary))'
                  : 'var(--bg-tertiary)',
              border:
                st?.chosen === l.name
                  ? '1.5px solid var(--accent)'
                  : '1px solid var(--border)',
              color: 'var(--text-primary)',
            }}
            disabled={busy || loading || !st?.terms.agreed || st.terms.stale}
            aria-pressed={st?.chosen === l.name}
            onClick={() => void pick(l.name)}
          >
            <span className="block text-xs font-semibold">{l.title}</span>
            <span
              className="block text-[10px] mt-0.5"
              style={{ color: 'var(--text-muted)' }}
            >
              {l.summary} Cost: {l.cost}. {l.catch}
            </span>
          </button>
        ))}
      {msg ? <FormNotice tone="error">{msg}</FormNotice> : null}
    </SettingsSection>
  )
}
