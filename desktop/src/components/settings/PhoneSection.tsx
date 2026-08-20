/** Settings → Phone — line options and terms. Real PSTN is not on this PC yet. */

import { useCallback, useEffect, useState, type ReactNode } from 'react'
import {
  acceptTelephonyTerms,
  chooseTelephonyLine,
  getTelephonyStatus,
  type TelephonyStatus,
} from '../../api/telephony'
import { SettingsSection } from '../SettingsSection'
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

  const refresh = useCallback(() => {
    getTelephonyStatus()
      .then(setSt)
      .catch(() => {})
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
          || 'Calling a real number is not on this computer yet. The voice and turn-taking are ready; the phone line is next.'}
      </FormHint>
      {st && !st.terms.agreed ? (
        <>
          <FormHint>
            {st.terms.ask
              || 'Before Remedy can use a phone, you agree to the phone terms once.'}
          </FormHint>
          <FormActionButton disabled={busy} onClick={() => void agree()}>
            I agree to the phone terms
          </FormActionButton>
        </>
      ) : (
        <FormHint>Phone terms are agreed on this computer.</FormHint>
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
            disabled={busy}
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
