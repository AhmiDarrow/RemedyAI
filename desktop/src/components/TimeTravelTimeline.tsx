import { Panel } from './Panel'
import { useAsyncScope } from '../hooks/useAsyncScope'
import { useCallback, useEffect, useRef, useState } from 'react'
import { apiFetch } from '../api/client'
import { EmptyState } from './EmptyState'

export type TimelineStep = {
  step: number
  id: string
  kind: string
  label: string
  preview: string
  created_at?: string
  message_id: string
  tool_count?: number
  tools?: string[]
  can_restore?: boolean
  parent_user_id?: string
  assistant_preview?: string
}

interface TimeTravelTimelineProps {
  open: boolean
  onClose: () => void
  sessionId: string | null
  onRestored: () => void
}

/**
 * Interactive Time Travel browser — click a step bubble to roll chat +
 * best-effort workspace files back to that moment.
 */
export function TimeTravelTimeline({
  open,
  onClose,
  sessionId,
  onRestored,
}: TimeTravelTimelineProps) {
  const scope = useAsyncScope(`${sessionId ?? ''}:${open}`)
  const restoring = useRef(false)
  const [steps, setSteps] = useState<TimelineStep[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirmId, setConfirmId] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (restoring.current) return
    const isCurrent = scope.latest()
    if (!sessionId) {
      setSteps([])
      return
    }
    setLoading(true)
    setError(null)
    try {
      const data = await apiFetch<{ steps: TimelineStep[] }>(
        `/sessions/${sessionId}/timeline`,
      )
      if (!isCurrent()) return
      setSteps(Array.isArray(data.steps) ? data.steps : [])
    } catch (e) {
      if (!isCurrent()) return
      setError(e instanceof Error ? e.message : 'Failed to load timeline')
      setSteps([])
    } finally {
      if (isCurrent()) setLoading(false)
    }
  }, [sessionId, scope])

  useEffect(() => {
    setSteps([])
    setConfirmId(null)
    setError(null)
    setBusy(null)
    restoring.current = false
    if (open) void load()
  }, [open, load])

  const restore = async (messageId: string) => {
    if (!sessionId || restoring.current) return
    restoring.current = true
    scope.latest()
    const isCurrent = scope.capture()
    setBusy(messageId)
    setError(null)
    try {
      await apiFetch(`/sessions/${sessionId}/time-travel`, {
        method: 'POST',
        body: JSON.stringify({ message_id: messageId }),
      })
      if (!isCurrent()) return
      setConfirmId(null)
      onRestored()
      restoring.current = false
      await load()
    } catch (e) {
      if (isCurrent()) setError(e instanceof Error ? e.message : 'Restore failed')
    } finally {
      if (isCurrent()) { restoring.current = false; setBusy(null) }
    }
  }

  if (!open) return null

  const userSteps = steps.filter((s) => s.kind === 'user')

  return (
    <Panel open={open} onClose={onClose} title="Time Travel">
      <div className="px-3 py-2 text-xs leading-snug" style={{ color: 'var(--text-muted)' }}>
        Click a step to roll back chat history, best-effort workspace file
        writes, and mid-task checkpoints to that moment.
      </div>
      {error && (
        <div className="mx-3 mb-2 text-sm" style={{ color: 'var(--error)' }}>
          {error}
        </div>
      )}
      <div className="flex-1 overflow-y-auto px-3 pb-3">
        {!sessionId ? (
          <EmptyState compact title="No session" description="Open a chat to browse its timeline." />
        ) : loading ? (
          <div style={{ color: 'var(--text-muted)', fontSize: 12 }}>Loading…</div>
        ) : userSteps.length === 0 ? (
          <EmptyState
            compact
            title="No steps yet"
            description="Send a message to build the timeline."
          />
        ) : (
          <div className="relative pl-4">
            <div
              className="absolute left-[7px] top-2 bottom-2 w-px"
              style={{ background: 'var(--border)' }}
            />
            {userSteps.map((s, idx) => {
              const isLast = idx === userSteps.length - 1
              const kids = steps.filter(
                (x) => x.kind === 'assistant' && x.parent_user_id === s.message_id,
              )
              return (
                <div key={s.id} className="relative mb-3">
                  <div
                    className="absolute left-[-13px] top-2 w-2.5 h-2.5 rounded-full"
                    style={{
                      background: isLast ? 'var(--accent)' : 'var(--bg-tertiary)',
                      border: `2px solid ${isLast ? 'var(--accent)' : 'var(--border)'}`,
                    }}
                  />
                  <button
                    type="button"
                    disabled={Boolean(busy) || !s.can_restore}
                    onClick={() => setConfirmId(s.message_id)}
                    className="w-full text-left p-2 rounded transition-opacity"
                    style={{
                      background: 'var(--bg-tertiary)',
                      border: '1px solid var(--border)',
                      opacity: busy ? 0.7 : 1,
                    }}
                    title="Restore to this step"
                  >
                    <div
                      className="font-semibold text-sm"
                      style={{ color: 'var(--accent)' }}
                    >
                      {s.label}
                      {s.tool_count ? (
                        <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>
                          {' '}
                          · {s.tool_count} tools
                        </span>
                      ) : null}
                    </div>
                    <div
                      className="mt-0.5 text-sm line-clamp-3"
                      style={{ color: 'var(--text-primary)' }}
                    >
                      {s.preview}
                    </div>
                    {kids[0]?.preview && (
                      <div
                        className="mt-1 text-xs line-clamp-2"
                        style={{ color: 'var(--text-muted)' }}
                      >
                        → {kids[0].preview}
                      </div>
                    )}
                  </button>
                  {confirmId === s.message_id && (
                    <div
                      className="mt-1 p-2 rounded text-xs"
                      style={{
                        background: 'var(--bg-primary)',
                        border: '1px solid var(--border)',
                      }}
                    >
                      <div style={{ color: 'var(--text-secondary)' }}>
                        Restore to <strong>{s.label}</strong>? Later messages,
                        and checkpoints will be rolled back. Remedy will try to restore
                        workspace files; changes made outside Remedy may remain.
                      </div>
                      <div className="mt-1.5 flex gap-1">
                        <button
                          type="button"
                          disabled={!!busy}
                          onClick={() => void restore(s.message_id)}
                          className="flex-1 py-1 rounded text-sm font-medium"
                          style={{
                            background: 'var(--accent)',
                            color: 'var(--accent-foreground)',
                          }}
                        >
                          {busy === s.message_id ? 'Restoring…' : 'Restore here'}
                        </button>
                        <button
                          type="button"
                          onClick={() => setConfirmId(null)}
                          className="px-2 py-1 rounded text-sm"
                          style={{ border: '1px solid var(--border)' }}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
      <div className="px-3 py-2 border-t" style={{ borderColor: 'var(--border)' }}>
        <button
          type="button"
          disabled={loading || Boolean(busy)}
          onClick={() => void load()}
          className="w-full text-xs py-1 rounded"
          style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
        >
          Refresh timeline
        </button>
      </div>
    </Panel>
  )
}
