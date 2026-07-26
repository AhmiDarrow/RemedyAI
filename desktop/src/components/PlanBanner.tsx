import { useCallback, useEffect, useState } from 'react'
import { fetchLatestPlan, type TaskPlan } from '../api/plans'

/**
 * Sticky Plan-mode card: Approve → Build, Request changes, Discard.
 * Session-scoped — never shows another chat's plan.
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
  const [loading, setLoading] = useState(false)

  const refresh = useCallback(async () => {
    if (!sessionId) {
      setPlan(null)
      return
    }
    setLoading(true)
    try {
      const p = await fetchLatestPlan(sessionId)
      setPlan(p)
    } finally {
      setLoading(false)
    }
  }, [sessionId])

  // Session switch: drop chrome immediately (avoid flash of previous plan).
  useEffect(() => {
    setPlan(null)
  }, [sessionId])

  // Load when entering Plan mode or after session settles.
  useEffect(() => {
    if (!sessionId) return
    if (!planMode) {
      // Build mode: keep last plan for this session as "Plan ready" only if we already have it.
      // Re-fetch once so Hide + re-open after reload still works.
      void refresh()
      return
    }
    void refresh()
    const t = window.setInterval(() => void refresh(), 8000)
    return () => window.clearInterval(t)
  }, [planMode, sessionId, refresh])

  if (!planMode && !plan) return null
  if (!sessionId) return null

  const steps = plan?.steps || []

  return (
    <div
      className="mx-3 mt-2 mb-1 rounded-lg border px-3 py-2 text-xs shrink-0"
      style={{
        background: 'color-mix(in srgb, var(--accent) 10%, var(--bg-secondary))',
        borderColor: 'var(--accent)',
        color: 'var(--text-primary)',
      }}
      data-plan-banner
      data-plan-mode={planMode ? 'true' : 'false'}
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
          disabled={loading}
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
          {loading
            ? 'Loading plan…'
            : 'No plan for this session yet. Research with read-only tools, then save a plan. Ask questions if anything is unclear.'}
        </div>
      )}
      <div className="flex flex-wrap gap-1.5">
        <button
          type="button"
          className="px-2 py-1 rounded font-semibold"
          style={{ background: 'var(--accent)', color: '#fff' }}
          onClick={() => onApproveBuild()}
          title="Leave Plan mode and implement"
          disabled={!plan && planMode}
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
      <div className="mt-1.5 text-[10px]" style={{ color: 'var(--text-muted)' }}>
        Toggle Plan/Build: <kbd className="opacity-80">Ctrl+B</kbd> or{' '}
        <kbd className="opacity-80">Shift+Tab</kbd>
      </div>
    </div>
  )
}
