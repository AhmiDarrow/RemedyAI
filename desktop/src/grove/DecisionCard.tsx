/** Grove's decision card — one question at a time.
 *
 * Every approval (a command, a side job, a routine, a payment step) arrives
 * here as a single card docked above the talk bar: what Remedy wants to do in
 * plain words, then Yes / No / Explain. A queue shows as "1 of 3", never a
 * wall of cards. "Later" tucks it into a pill; a new decision brings it back.
 *
 * Payment, password and send checkpoints get their own look and only ever
 * offer a one-time yes — no mode can waive them, and nothing resolves them in
 * bulk. */

import { useCallback, useEffect, useRef, useState } from 'react'
import { listApprovals, resolveApproval, type PendingApproval } from '../api/partner'
import { approvalHeadline } from '../utils/approvalText'
import { explainCommand, isGenericReason, keepShownFirst, orderDecisions } from './headHelpers'

/** A card that just took the front can't be answered for this long, so a
 * click or key meant for the card before it lands on nothing. */
export const DECISION_ARM_MS = 500

export interface DecisionCardProps {
  serverReady: boolean
  /** Called after a decision so the surface can refresh what it shows. */
  onResolved?: () => void
  /** Reports how many decisions are waiting (for "needs you" pills). */
  onCount?: (n: number) => void
  /** Tucked-away state lives with the surface so switching rooms (which
   * remounts this card) does not pop a dismissed card back open. */
  tucked: boolean
  onTucked: (tucked: boolean) => void
  /** Decision ids the owner has already been shown (owned by the surface). */
  seen: Set<string>
}

export function DecisionCard({
  serverReady,
  onResolved,
  onCount,
  tucked,
  onTucked: setTucked,
  seen,
}: DecisionCardProps) {
  const [items, setItems] = useState<PendingApproval[]>([])
  // Which decision the owner opened details for (resets as the queue moves).
  const [explainId, setExplainId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [flash, setFlash] = useState<{ text: string; error?: boolean } | null>(null)
  const cardRef = useRef<HTMLDivElement | null>(null)
  // The decision on screen, and the one that has been there long enough to
  // be answered. A press only ever acts on that one.
  const shownRef = useRef<string | null>(null)
  const [armedId, setArmedId] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!serverReady) return
    try {
      const all = keepShownFirst(orderDecisions(await listApprovals()), shownRef.current)
      setItems(all)
      onCount?.(all.length)
      // A decision the owner has not seen yet brings the card back.
      const fresh = all.some((i) => !seen.has(i.id))
      all.forEach((i) => seen.add(i.id))
      if (fresh) setTucked(false)
    } catch {
      /* server down — keep what we have */
    }
  }, [serverReady, onCount, setTucked, seen])

  useEffect(() => {
    void refresh()
    const t = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return
      void refresh()
    }, 4000)
    return () => clearInterval(t)
  }, [refresh])

  useEffect(() => {
    if (!flash) return
    const t = setTimeout(() => setFlash(null), flash.error ? 5000 : 2400)
    return () => clearTimeout(t)
  }, [flash])

  const item = items[0]
  const itemId = item?.id ?? null
  useEffect(() => {
    shownRef.current = itemId
    setArmedId(null)
    if (!itemId) return
    const t = setTimeout(() => setArmedId(itemId), DECISION_ARM_MS)
    return () => clearTimeout(t)
  }, [itemId])
  const armed = !!item && armedId === item.id
  const explain = !!item && explainId === item.id
  const toggleExplain = () => setExplainId((cur) => (item && cur !== item.id ? item.id : null))

  const decide = useCallback(
    async (approve: boolean, scope: 'once' | 'session' = 'once') => {
      if (!item || busy || armedId !== item.id) return
      setBusy(true)
      try {
        const res = await resolveApproval(item.id, approve, approve ? scope : 'once')
        setFlash({ text: res.hint || (approve ? 'Okay — going ahead.' : 'Okay — I won’t.') })
        setItems((cur) => cur.filter((i) => i.id !== item.id))
        onResolved?.()
        void refresh()
      } catch (e) {
        setFlash({ text: e instanceof Error ? e.message : 'That didn’t go through — try again.', error: true })
      } finally {
        setBusy(false)
      }
    },
    [item, busy, armedId, onResolved, refresh],
  )

  const onKey = (e: React.KeyboardEvent) => {
    const tag = (e.target as HTMLElement).tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA') return
    const k = e.key.toLowerCase()
    if (k === 'y') {
      e.preventDefault()
      void decide(true)
    } else if (k === 'n') {
      e.preventDefault()
      void decide(false)
    } else if (k === 'e' || k === '?') {
      e.preventDefault()
      toggleExplain()
    } else if (k === 'escape') {
      e.preventDefault()
      setTucked(true)
    }
  }

  if (!item) {
    return flash ? (
      <div className={`dc-flash${flash.error ? ' err' : ''}`} role={flash.error ? 'alert' : 'status'}>
        {flash.text}
      </div>
    ) : null
  }

  if (tucked) {
    return (
      <button type="button" className="dc-pill" onClick={() => setTucked(false)} aria-label="Show the decision waiting for you">
        <span className="dc-pill-dot" aria-hidden />
        {items.length === 1 ? '1 decision waiting' : `${items.length} decisions waiting`}
      </button>
    )
  }

  const sensitive = !!item.sensitive
  const headline = approvalHeadline(item) || 'Remedy needs your OK to continue.'
  return (
    <div
      ref={cardRef}
      className={`dc${sensitive ? ' sensitive' : ''}`}
      role="alertdialog"
      aria-modal="false"
      aria-labelledby={`dc-h-${item.id}`}
      aria-describedby={`dc-d-${item.id}`}
      onKeyDown={onKey}
      tabIndex={-1}
      data-testid="decision-card"
      data-sensitive={sensitive ? 'true' : undefined}
    >
      <div className="dc-top">
        <span className="dc-kicker">
          <span aria-hidden>{sensitive ? '🔒' : '✋'}</span>
          {sensitive ? 'Your call — money, password or send' : 'Remedy is asking'}
        </span>
        {items.length > 1 && <span className="dc-count">1 of {items.length}</span>}
        <button type="button" className="dc-later" onClick={() => setTucked(true)} title="Decide later (Esc)">
          Later
        </button>
      </div>
      <h3 className="dc-headline" id={`dc-h-${item.id}`}>
        {headline}
      </h3>
      <div id={`dc-d-${item.id}`} className="dc-sub">
        {sensitive
          ? 'Nothing happens until you say yes. This step always asks, in every mode.'
          : item.blocking
            ? 'Remedy is paused here until you decide.'
            : 'Remedy will pick this up once you decide.'}
      </div>
      {explain && (
        <div className="dc-explain">
          {!isGenericReason(item.reason) && item.reason !== headline && <p>{item.reason}</p>}
          <p className="dc-explain-label">Exactly what Remedy would do:</p>
          {(() => {
            const ex = explainCommand(item.command)
            return typeof ex === 'string' ? (
              <code>{ex}</code>
            ) : (
              <dl className="dc-fields">
                {ex.map((r) => (
                  <div key={r.label}>
                    <dt>{r.label}</dt>
                    <dd>{r.value}</dd>
                  </div>
                ))}
              </dl>
            )
          })()}
        </div>
      )}
      <div className="dc-actions">
        <button type="button" className="dc-yes" disabled={busy || !armed} onClick={() => void decide(true)}>
          {busy ? 'Working…' : sensitive ? 'Yes, go ahead' : 'Yes'}
          <kbd aria-hidden>Y</kbd>
        </button>
        <button type="button" className="dc-no" disabled={busy || !armed} onClick={() => void decide(false)}>
          {sensitive ? 'Not now' : 'No'}
          <kbd aria-hidden>N</kbd>
        </button>
        <button type="button" className="dc-explain-btn" aria-expanded={explain} onClick={toggleExplain}>
          {explain ? 'Hide details' : 'Explain'}
          <kbd aria-hidden>E</kbd>
        </button>
        {!sensitive && (
          <button
            type="button"
            className="dc-allow"
            disabled={busy || !armed}
            onClick={() => void decide(true, 'session')}
            title="Don't ask again for this in this conversation"
          >
            Allow for this chat
          </button>
        )}
      </div>
    </div>
  )
}
