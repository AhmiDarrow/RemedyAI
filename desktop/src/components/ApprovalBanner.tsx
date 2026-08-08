import { useCallback, useEffect, useState } from 'react'
import {
  getPartnerStatus,
  resolveApproval,
  type PendingApproval,
} from '../api/partner'

interface ApprovalBannerProps {
  sessionId: string | null
  /** Called after approve so user can re-send / agent can retry */
  onResolved?: (approved: boolean, command: string) => void
}

export function ApprovalBanner({ sessionId, onResolved }: ApprovalBannerProps) {
  const [items, setItems] = useState<PendingApproval[]>([])
  const [busyId, setBusyId] = useState<string | null>(null)
  const [message, setMessage] = useState('')

  const refresh = useCallback(async () => {
    try {
      const st = await getPartnerStatus()
      setItems(st.approvals || [])
    } catch {
      // server down
    }
  }, [])

  useEffect(() => {
    void refresh()
    const id = window.setInterval(() => void refresh(), 4000)
    return () => window.clearInterval(id)
  }, [refresh, sessionId])

  const act = async (item: PendingApproval, approve: boolean) => {
    setBusyId(item.id)
    setMessage('')
    try {
      const res = await resolveApproval(item.id, approve, 'session')
      setMessage(res.hint || (approve ? 'Approved' : 'Denied'))
      await refresh()
      onResolved?.(approve, item.command)
    } catch (e: unknown) {
      setMessage(e instanceof Error ? e.message : 'Failed')
    } finally {
      setBusyId(null)
    }
  }

  if (!items.length && !message) return null

  return (
    <div
      className="mx-3 mt-2 mb-1 space-y-2"
      style={{ color: 'var(--text-primary)' }}
      role="region"
      aria-label="Pending tool approvals"
    >
      {items.map((item) => (
        <div key={item.id} className="ui-banner ui-banner-warn">
          <div
            className="font-semibold mb-1.5 flex items-center gap-1.5 text-[0.72rem] uppercase tracking-wide"
            style={{ color: 'var(--warning)' }}
          >
            <span aria-hidden>⚠</span>
            Approval required
          </div>
          <div className="mb-1.5 text-xs" style={{ color: 'var(--text-secondary)' }}>
            {item.reason}
          </div>
          <code
            className="block mb-2.5 px-2.5 py-1.5 rounded-lg break-all text-[0.7rem] font-mono"
            style={{
              background: 'color-mix(in srgb, var(--bg-tertiary) 85%, transparent)',
              color: 'var(--text-primary)',
              border: '1px solid color-mix(in srgb, var(--border) 85%, transparent)',
            }}
          >
            {item.command}
          </code>
          <div className="flex gap-2 items-center flex-wrap">
            <button
              type="button"
              disabled={busyId === item.id}
              onClick={() => void act(item, true)}
              className="ui-btn ui-btn-primary"
            >
              {busyId === item.id ? 'Working…' : 'Approve once'}
            </button>
            <button
              type="button"
              disabled={busyId === item.id}
              onClick={() => void act(item, false)}
              className="ui-btn ui-btn-secondary"
            >
              Deny
            </button>
          </div>
        </div>
      ))}
      {message && (
        <div className="text-xs px-1 font-medium" style={{ color: 'var(--success)' }}>
          {message}
        </div>
      )}
    </div>
  )
}
