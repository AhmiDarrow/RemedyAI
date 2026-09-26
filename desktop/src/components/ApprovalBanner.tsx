import { useAsyncScope } from '../hooks/useAsyncScope'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useI18n } from '../i18n'
import {
  listApprovals,
  resolveApproval,
  type PendingApproval,
} from '../api/partner'
import { approvalHeadline } from '../utils/approvalText'

interface ApprovalBannerProps {
  sessionId: string | null
  /** Called after approve so user can re-send / agent can retry */
  onResolved?: (approved: boolean, command: string) => void
}

export function ApprovalBanner({ sessionId, onResolved }: ApprovalBannerProps) {
  const { t } = useI18n()
  const scope = useAsyncScope(sessionId)
  const actionBusy = useRef(false)
  const [messageError, setMessageError] = useState(false)
  const [items, setItems] = useState<PendingApproval[]>([])
  const [busyId, setBusyId] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const msgTimer = useRef<number | null>(null)

  const flashMsg = (msg: string, ms: number) => {
    setMessage(msg)
    if (msgTimer.current != null) window.clearTimeout(msgTimer.current)
    msgTimer.current = window.setTimeout(() => {
      msgTimer.current = null
      setMessage((cur) => (cur === msg ? '' : cur))
    }, ms)
  }

  const refresh = useCallback(async () => {
    if (actionBusy.current) return
    const isCurrent = scope.latest()
    try {
      // List every pending item, then keep this chat's plus blocking / hive
      // Asks so an owner looking at another tab still sees a waiter.
      const items = await listApprovals()
      if (!isCurrent()) return
      const focused = (sessionId || '').trim()
      setItems(
        items.filter((i) => {
          if (i.tool_name === 'life_drive') return false
          if (!focused) return true
          const sid = (i.session_id || '').trim()
          if (!sid || sid === focused) return true
          if (i.blocking) return true
          const origin = String(i.origin || i.channel || '').toLowerCase()
          return origin.startsWith('hive:')
        }),
      )
    } catch {
      // server down
    }
  }, [sessionId, scope])

  useEffect(() => {
    setItems([])
    setMessage('')
    setBusyId(null)
    actionBusy.current = false
    void refresh()
    const id = window.setInterval(() => void refresh(), 4000)
    return () => {
      window.clearInterval(id)
      if (msgTimer.current != null) window.clearTimeout(msgTimer.current)
    }
  }, [refresh, sessionId])

  const act = async (
    item: PendingApproval,
    approve: boolean,
    approvalScope: 'session' | 'always' | 'once' = 'once',
  ) => {
    if (actionBusy.current) return
    actionBusy.current = true
    scope.latest()
    const isCurrent = scope.capture()
    setMessageError(false)
    setBusyId(item.id)
    setMessage('')
    try {
      const res = await resolveApproval(item.id, approve, approve ? approvalScope : 'once')
      if (!isCurrent()) return
      const msg = res.hint || (approve ? t('approval.approved') : t('approval.denied'))
      flashMsg(msg, 2800)
      actionBusy.current = false
      await refresh()
      if (isCurrent()) onResolved?.(approve, item.command)
    } catch (e: unknown) {
      if (!isCurrent()) return
      setMessageError(true)
      const err = e instanceof Error ? e.message : 'Failed'
      flashMsg(err, 4500)
    } finally {
      if (isCurrent()) { actionBusy.current = false; setBusyId(null) }
    }
  }

  if (!items.length && !message) return null

  return (
    <div
      className="mx-3 mt-2 mb-1 space-y-2"
      style={{ color: 'var(--text-primary)' }}
      role="region"
      aria-label={t('approval.region')}
    >
      {/* One card per item, each answered on its own. There is deliberately no
          approve-all control: sensitive checkpoints (payment / credentials /
          vault) must never be resolved in bulk. */}
      {items.map((item) => (
        <div
          key={item.id}
          className="ui-banner ui-banner-warn"
          data-sensitive={item.sensitive ? 'true' : undefined}
          style={
            item.sensitive
              ? {
                  borderColor: 'color-mix(in srgb, var(--warning) 70%, var(--border))',
                  boxShadow: '0 0 0 1px color-mix(in srgb, var(--warning) 35%, transparent)',
                }
              : undefined
          }
        >
          <div
            className="font-semibold mb-1.5 flex items-center gap-1.5 text-[0.72rem] uppercase tracking-wide"
            style={{ color: 'var(--warning)' }}
          >
            <span aria-hidden>{item.sensitive ? '💳' : '⚠'}</span>
            {item.sensitive ? t('approval.payment') : t('approval.required')}
          </div>
          {/* Plain-language headline first (Grove premise) with the origin
              channel when the request came from elsewhere; raw reason/command
              demoted to a details line. */}
          <div className="mb-1.5 text-sm" style={{ color: 'var(--text-primary)' }}>
            {approvalHeadline(item)}
          </div>
          {item.sensitive && (
            <div className="mb-1.5 text-[0.72rem]" style={{ color: 'var(--warning)' }}>
              {t('approval.paymentNote')}
            </div>
          )}
          <details className="mb-2.5">
            <summary
              className="text-[0.7rem] cursor-pointer select-none"
              style={{ color: 'var(--text-muted)' }}
            >
              {t('approval.details')}
            </summary>
            <div className="mt-1 text-[0.7rem]" style={{ color: 'var(--text-secondary)' }}>
              {item.reason}
            </div>
            <code
              className="block mt-1 px-2.5 py-1.5 rounded-lg break-all text-[0.7rem] font-mono"
              style={{
                background: 'color-mix(in srgb, var(--bg-tertiary) 85%, transparent)',
                color: 'var(--text-primary)',
                border: '1px solid color-mix(in srgb, var(--border) 85%, transparent)',
              }}
            >
              {item.command}
            </code>
          </details>
          <div className="flex gap-2 items-center flex-wrap">
            <button
              type="button"
              disabled={busyId !== null}
              onClick={() => void act(item, true, 'once')}
              className="ui-btn ui-btn-primary"
            >
              {busyId === item.id
                ? t('approval.working')
                : item.sensitive
                  ? t('approval.yes')
                  : t('approval.once')}
            </button>
            <button
              type="button"
              disabled={busyId !== null}
              onClick={() => void act(item, false)}
              className="ui-btn ui-btn-secondary"
            >
              {item.sensitive ? t('approval.notNow') : t('approval.deny')}
            </button>
          </div>
        </div>
      ))}
      {message && (
        <div role={messageError ? 'alert' : 'status'} className="text-sm px-1 font-medium" style={{ color: messageError ? 'var(--error)' : 'var(--success)' }}>
          {message}
        </div>
      )}
    </div>
  )
}
