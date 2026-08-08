import { useCallback, useEffect, useState } from 'react'
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
  const [steps, setSteps] = useState<TimelineStep[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirmId, setConfirmId] = useState<string | null>(null)

  const load = useCallback(async () => {
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
      setSteps(Array.isArray(data.steps) ? data.steps : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load timeline')
      setSteps([])
    } finally {
      setLoading(false)
    }
  }, [sessionId])

  useEffect(() => {
    if (open) void load()
  }, [open, load])

  const restore = async (messageId: string) => {
    if (!sessionId) return
    setBusy(messageId)
    setError(null)
    try {
      await apiFetch(`/sessions/${sessionId}/time-travel`, {
        method: 'POST',
        body: JSON.stringify({ message_id: messageId }),
      })
      setConfirmId(null)
      onRestored()
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Restore failed')
    } finally {
      setBusy(null)
    }
  }

  if (!open) return null

  const userSteps = steps.filter((s) => s.kind === 'user')

  return (
    <div
      className="flex flex-col border-l overflow-hidden"
      style={{
        width: 300,
        minWidth: 300,
        background: 'color-mix(in srgb, var(--bg-secondary) 96%, var(--bg-primary))',
        borderColor: 'color-mix(in srgb, var(--border) 85%, transparent)',
      }}
      role="complementary"
      aria-label="Time travel timeline"
    >
      <div
        className="flex items-center justify-between px-3 py-2.5 border-b text-xs font-semibold tracking-tight"
        style={{
          borderColor: 'color-mix(in srgb, var(--border) 80%, transparent)',
          color: 'var(--text-primary)',
        }}
      >
        <span>Time Travel</span>
        <button
          type="button"
          onClick={onClose}
          className="ui-btn ui-btn-ghost"
          style={{ padding: '0.15rem 0.4rem' }}
          aria-label="Close timeline"
        >
          ×
        </button>
      </div>
      <div className="px-3 py-2 text-[10px] leading-snug" style={{ color: 'var(--text-muted)' }}>
        Click a step to roll back chat history, best-effort workspace file
        writes, and mid-task checkpoints to that moment.
      </div>
      {error && (
        <div className="mx-3 mb-2 text-[11px]" style={{ color: 'var(--error)' }}>
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
                    disabled={busy === s.message_id || !s.can_restore}
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
                      className="font-semibold text-[11px]"
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
                      className="mt-0.5 text-[11px] line-clamp-3"
                      style={{ color: 'var(--text-primary)' }}
                    >
                      {s.preview}
                    </div>
                    {kids[0]?.preview && (
                      <div
                        className="mt-1 text-[10px] line-clamp-2"
                        style={{ color: 'var(--text-muted)' }}
                      >
                        → {kids[0].preview}
                      </div>
                    )}
                  </button>
                  {confirmId === s.message_id && (
                    <div
                      className="mt-1 p-2 rounded text-[10px]"
                      style={{
                        background: 'var(--bg-primary)',
                        border: '1px solid var(--border)',
                      }}
                    >
                      <div style={{ color: 'var(--text-secondary)' }}>
                        Restore to <strong>{s.label}</strong>? Later messages,
                        file writes after this point, and checkpoints will be
                        undone.
                      </div>
                      <div className="mt-1.5 flex gap-1">
                        <button
                          type="button"
                          disabled={!!busy}
                          onClick={() => void restore(s.message_id)}
                          className="flex-1 py-1 rounded text-[11px] font-medium"
                          style={{
                            background: 'var(--accent)',
                            color: '#fff',
                          }}
                        >
                          {busy === s.message_id ? 'Restoring…' : 'Restore here'}
                        </button>
                        <button
                          type="button"
                          onClick={() => setConfirmId(null)}
                          className="px-2 py-1 rounded text-[11px]"
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
          onClick={() => void load()}
          className="w-full text-xs py-1 rounded"
          style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
        >
          Refresh timeline
        </button>
      </div>
    </div>
  )
}
