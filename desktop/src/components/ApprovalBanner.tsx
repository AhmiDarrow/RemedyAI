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
      className="mx-4 mt-2 mb-1 space-y-2"
      style={{ color: 'var(--text-primary)' }}
    >
      {items.map((item) => (
        <div
          key={item.id}
          className="rounded-lg px-3 py-2.5 text-xs"
          style={{
            background: 'var(--bg-secondary)',
            border: '1px solid var(--warning)',
          }}
        >
          <div className="font-semibold mb-1" style={{ color: 'var(--warning)' }}>
            Approval required
          </div>
          <div className="mb-1" style={{ color: 'var(--text-secondary)' }}>
            {item.reason}
          </div>
          <code
            className="block mb-2 px-2 py-1 rounded break-all"
            style={{
              background: 'var(--bg-tertiary)',
              color: 'var(--text-primary)',
              border: '1px solid var(--border)',
            }}
          >
            {item.command}
          </code>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={busyId === item.id}
              onClick={() => void act(item, true)}
              className="px-3 py-1 rounded font-medium"
              style={{ background: 'var(--accent)', color: '#fff' }}
            >
              Approve once
            </button>
            <button
              type="button"
              disabled={busyId === item.id}
              onClick={() => void act(item, false)}
              className="px-3 py-1 rounded font-medium"
              style={{
                background: 'var(--bg-tertiary)',
                color: 'var(--text-primary)',
                border: '1px solid var(--border)',
              }}
            >
              Deny
            </button>
            <span className="ml-auto self-center" style={{ color: 'var(--text-muted)' }}>
              id {item.id}
            </span>
          </div>
        </div>
      ))}
      {message && (
        <div className="text-xs px-1" style={{ color: 'var(--success)' }}>
          {message}
        </div>
      )}
    </div>
  )
}
