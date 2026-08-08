import { useCallback, useEffect, useState } from 'react'
import {
  approvePlan,
  cancelPlan,
  fetchLatestPlan,
  isPlanActionable,
  shouldShowPlanBanner,
  type TaskPlan,
} from '../api/plans'

/**
 * Sticky Plan-mode card: Approve → Build, Request changes, Cancel plan.
 * Session-scoped — never shows another chat's plan.
 * Terminal plans (done / cancelled) do not stick in Build mode.
 */
export function PlanBanner({
  planMode,
  sessionId,
  onApproveBuild,
  onRequestChanges,
  onCancelled,
}: {
  planMode: boolean
  sessionId: string | null
  onApproveBuild: () => void
  onRequestChanges: (hint: string) => void
  /** After durable cancel (status=cancelled). */
  onCancelled?: () => void
}) {
  const [plan, setPlan] = useState<TaskPlan | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!sessionId) {
      setPlan(null)
      return
    }
    setLoading(true)
    setError(null)
    try {
      // Actionable-only so done/cancelled do not reappear after finish/quit.
      const p = await fetchLatestPlan(sessionId, { actionableOnly: true })
      setPlan(p)
    } finally {
      setLoading(false)
    }
  }, [sessionId])

  // Session switch: drop chrome immediately (avoid flash of previous plan).
  useEffect(() => {
    setPlan(null)
    setError(null)
  }, [sessionId])

  // Load when entering Plan mode or after session settles.
  useEffect(() => {
    if (!sessionId) return
    void refresh()
    if (!planMode) return
    const t = window.setInterval(() => void refresh(), 8000)
    return () => window.clearInterval(t)
  }, [planMode, sessionId, refresh])

  if (!sessionId) return null
  if (!shouldShowPlanBanner(plan, planMode)) return null

  const steps = plan?.steps || []
  const status = String(plan?.status || 'draft').toLowerCase()
  const showApprove = Boolean(plan?.id) && isPlanActionable(status)
  const midBuild = status === 'approved' || status === 'active'

  const handleApprove = async () => {
    if (!plan?.id || busy || !isPlanActionable(status)) return
    setBusy(true)
    setError(null)
    try {
      // Persist approval for drafts; already-approved/active just enter Build.
      if (status === 'draft') {
        const updated = await approvePlan(plan.id)
        if (updated) setPlan(updated)
      }
      onApproveBuild()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not approve plan')
    } finally {
      setBusy(false)
    }
  }

  const handleCancel = async () => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      if (plan?.id) {
        await cancelPlan(plan.id)
      }
      setPlan(null)
      onCancelled?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not cancel plan')
    } finally {
      setBusy(false)
    }
  }

  // Build-mode + approved/active is hidden by shouldShowPlanBanner; remaining
  // Build-mode cases are drafts still waiting for Approve ("Plan ready").
  const headerLabel = planMode
    ? midBuild
      ? 'Plan mode · in progress'
      : 'Plan mode'
    : 'Plan ready'

  return (
    <div
      className="ui-banner ui-banner-plan mx-3 mt-2 mb-1 text-xs shrink-0"
      style={{ color: 'var(--text-primary)' }}
      data-plan-banner
      data-plan-mode={planMode ? 'true' : 'false'}
      data-plan-status={status}
    >
      <div className="flex items-center gap-2 mb-1.5">
        <span
          className="font-semibold uppercase tracking-wide text-[0.68rem]"
          style={{ color: 'var(--accent)' }}
        >
          {headerLabel}
        </span>
        {plan?.title && (
          <span className="truncate font-medium">{plan.title}</span>
        )}
        {plan?.status && (
          <span className="opacity-70 text-[0.7rem]">· {plan.status}</span>
        )}
        <button
          type="button"
          className="ml-auto ui-btn ui-btn-ghost"
          style={{ padding: '0.15rem 0.4rem', fontSize: '0.7rem' }}
          title="Refresh plan"
          onClick={() => void refresh()}
          disabled={loading || busy}
        >
          ↻
        </button>
      </div>
      {plan?.goal && (
        <div className="mb-1.5 opacity-90" style={{ color: 'var(--text-secondary)' }}>
          Goal: {plan.goal}
        </div>
      )}
      {steps.length > 0 && (
        <ol className="list-decimal ml-4 mb-2.5 space-y-0.5" style={{ color: 'var(--text-secondary)' }}>
          {steps.slice(0, 8).map((s) => (
            <li key={s.id}>{s.title}</li>
          ))}
          {steps.length > 8 && <li>…+{steps.length - 8} more</li>}
        </ol>
      )}
      {!plan && planMode && (
        <div className="mb-2.5" style={{ color: 'var(--text-muted)' }}>
          {loading
            ? 'Loading plan…'
            : 'No plan for this session yet. Research with read-only tools, then save a plan. Ask questions if anything is unclear.'}
        </div>
      )}
      {error && (
        <div className="mb-2" style={{ color: 'var(--error, #f66)' }} role="alert">
          {error}
        </div>
      )}
      <div className="flex flex-wrap gap-1.5">
        {showApprove && (
          <button
            type="button"
            className="ui-btn ui-btn-primary"
            onClick={() => void handleApprove()}
            title={
              midBuild
                ? 'Continue in Build mode'
                : 'Approve plan, leave Plan mode, and implement'
            }
            disabled={busy || !plan?.id}
          >
            {midBuild ? 'Continue → Build' : 'Approve → Build'}
          </button>
        )}
        {planMode && (
          <button
            type="button"
            className="ui-btn ui-btn-secondary"
            onClick={() =>
              onRequestChanges(
                plan
                  ? `Please revise the plan "${plan.title}": `
                  : 'Please revise the plan: ',
              )
            }
            disabled={busy}
          >
            Request changes
          </button>
        )}
        <button
          type="button"
          className="ui-btn ui-btn-danger"
          onClick={() => void handleCancel()}
          title={
            plan?.id
              ? 'Cancel this plan permanently (status=cancelled). It will not reappear.'
              : 'Leave Plan mode'
          }
          disabled={busy}
        >
          {plan?.id ? 'Cancel plan' : 'Quit plan mode'}
        </button>
      </div>
      <div className="mt-1.5 text-[10px]" style={{ color: 'var(--text-muted)' }}>
        Toggle Plan/Build: <kbd className="opacity-80">Ctrl+B</kbd> or{' '}
        <kbd className="opacity-80">Shift+Tab</kbd>
        {isPlanActionable(status) && plan?.id
          ? ' · Cancel plan quits for good (not just hide)'
          : null}
      </div>
    </div>
  )
}
