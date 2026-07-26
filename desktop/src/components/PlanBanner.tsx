import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../api/client'

type PlanStep = { id: string; title: string; detail?: string; status?: string }
type TaskPlan = {
  id: string
  title: string
  goal?: string
  status?: string
  steps?: PlanStep[]
  risks?: string[]
}

/**
 * Sticky Plan-mode card: Approve → Build, Request changes, Discard.
 * Mirrors Grok/Claude plan approval UX.
 */
export function PlanBanner({
  planMode,
  sessionId,
  onApproveBuild,
  onRequestChanges,
}: {
  planMode: boolean
  sessionId: string | null
  onApproveBuild: () => void
  onRequestChanges: (hint: string) => void
}) {
  const [plan, setPlan] = useState<TaskPlan | null>(null)
  const [err, setErr] = useState('')

  const refresh = useCallback(async () => {
    if (!sessionId) {
      setPlan(null)
      return
    }
    try {
      const data = await apiFetch<{ plan?: TaskPlan | null } | TaskPlan>(
        `/plans/latest?session_id=${encodeURIComponent(sessionId)}`,
      )
      const p =
        data && typeof data === 'object' && 'plan' in data
          ? (data as { plan?: TaskPlan | null }).plan
          : (data as TaskPlan)
      setPlan(p && p.id ? p : null)
      setErr('')
    } catch {
      // endpoint may 404 when empty
      setPlan(null)
    }
  }, [sessionId])

  useEffect(() => {
    void refresh()
    if (!planMode) return
    const t = window.setInterval(() => void refresh(), 4000)
    return () => window.clearInterval(t)
  }, [planMode, refresh])

  if (!planMode && !plan) return null

  const steps = plan?.steps || []

  return (
    <div
      className="mx-3 mt-2 mb-1 rounded-lg border px-3 py-2 text-xs shrink-0"
      style={{
        background: 'color-mix(in srgb, var(--accent) 10%, var(--bg-secondary))',
        borderColor: 'var(--accent)',
        color: 'var(--text-primary)',
      }}
    >
      <div className="flex items-center gap-2 mb-1">
        <span className="font-semibold" style={{ color: 'var(--accent)' }}>
          {planMode ? 'Plan mode' : 'Plan ready'}
        </span>
        {plan?.title && (
          <span className="truncate font-medium">{plan.title}</span>
        )}
        {plan?.status && (
          <span className="opacity-70">· {plan.status}</span>
        )}
        <button
          type="button"
          className="ml-auto opacity-70 hover:opacity-100"
          title="Refresh plan"
          onClick={() => void refresh()}
        >
          ↻
        </button>
      </div>
      {plan?.goal && (
        <div className="mb-1 opacity-90" style={{ color: 'var(--text-secondary)' }}>
          Goal: {plan.goal}
        </div>
      )}
      {steps.length > 0 && (
        <ol className="list-decimal ml-4 mb-2 space-y-0.5" style={{ color: 'var(--text-secondary)' }}>
          {steps.slice(0, 8).map((s) => (
            <li key={s.id}>{s.title}</li>
          ))}
          {steps.length > 8 && <li>…+{steps.length - 8} more</li>}
        </ol>
      )}
      {!plan && planMode && (
        <div className="mb-2" style={{ color: 'var(--text-muted)' }}>
          Research with read-only tools, then save a plan. Ask questions if anything is unclear.
        </div>
      )}
      {err && <div style={{ color: 'var(--error)' }}>{err}</div>}
      <div className="flex flex-wrap gap-1.5">
        <button
          type="button"
          className="px-2 py-1 rounded font-semibold"
          style={{ background: 'var(--accent)', color: '#fff' }}
          onClick={() => {
            onApproveBuild()
          }}
          title="Leave Plan mode and implement"
        >
          Approve → Build
        </button>
        <button
          type="button"
          className="px-2 py-1 rounded"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-secondary)',
          }}
          onClick={() =>
            onRequestChanges(
              plan
                ? `Please revise the plan "${plan.title}": `
                : 'Please revise the plan: ',
            )
          }
        >
          Request changes
        </button>
        <button
          type="button"
          className="px-2 py-1 rounded"
          style={{ color: 'var(--text-muted)' }}
          onClick={() => setPlan(null)}
        >
          Hide
        </button>
      </div>
    </div>
  )
}
