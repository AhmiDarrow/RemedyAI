import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  approvePlan,
  cancelPlan,
  fetchLatestPlan,
  isPlanActionable,
  shouldShowPlanBanner,
  type PlanStep,
  type TaskPlan,
} from '../api/plans'
import { useI18n } from '../i18n'

export const PLAN_STREAMING_HINT = 'Remedy is still working on the plan…'

/** Step status → chip class / label. Backend emits pending|active|done|skipped. */
export function stepStatusChip(status?: string | null): { key: string; label: string } {
  const s = String(status || 'pending').toLowerCase()
  if (s === 'done' || s === 'complete' || s === 'completed') return { key: 'done', label: 'done' }
  if (s === 'active' || s === 'running' || s === 'in_progress') return { key: 'active', label: 'active' }
  if (s === 'blocked' || s === 'skipped' || s === 'failed') return { key: 'blocked', label: s }
  return { key: 'draft', label: s === 'pending' ? 'draft' : s }
}

/**
 * "Option A:" / "Option B —" style choices inside the plan text. Returns the
 * distinct labels in first-seen order so they can render as quick-reply chips.
 */
export function extractPlanOptions(plan: TaskPlan | null): string[] {
  if (!plan) return []
  const parts: string[] = [plan.title || '', plan.goal || '']
  for (const s of plan.steps || []) parts.push(s.title || '', s.detail || '')
  for (const r of plan.risks || []) parts.push(r)
  const seen = new Set<string>()
  const out: string[] = []
  const re = /(?:^|\n)\s*(?:[-*•#>\d.)\s]*)\s*\**\s*Option\s+([A-Z]|\d{1,2})\s*\**\s*[:\-–—(]/gi
  for (const text of parts) {
    let m: RegExpExecArray | null
    re.lastIndex = 0
    while ((m = re.exec(text)) !== null) {
      const label = `Option ${m[1].toUpperCase()}`
      if (!seen.has(label)) {
        seen.add(label)
        out.push(label)
      }
    }
  }
  return out
}

/** Cheap fingerprint so a changed plan re-renders even when the id is stable. */
function planSignature(p: TaskPlan | null): string {
  if (!p) return ''
  return [p.id, p.status, p.title, (p.steps || []).map((s) => `${s.id}:${s.status || ''}:${s.title}`).join('|')].join('#')
}

/**
 * Sticky Plan-mode card: Approve → Build, Request changes, Cancel plan.
 * Session-scoped — never shows another chat's plan.
 * Terminal plans (done / cancelled) do not stick in Build mode.
 *
 * Live updates: parent bumps `refreshSignal` whenever a plan_* tool finishes
 * in the stream (plan_save / plan_step_status) and when streaming ends; a slow
 * 8 s poll in Plan mode is the safety net.
 */
export function PlanBanner({
  planMode,
  sessionId,
  streaming = false,
  refreshSignal = 0,
  onApproveBuild,
  onRequestChanges,
  onCancelled,
  onPlanChange,
}: {
  planMode: boolean
  sessionId: string | null
  /** True while a turn is streaming in this session (disables plan actions). */
  streaming?: boolean
  /** Bump to force a refetch (plan tool finished, turn ended). */
  refreshSignal?: number
  /** `edit` is true when the user held Shift — pre-fill instead of send. */
  onApproveBuild: (opts: { edit: boolean }) => void
  onRequestChanges: (hint: string) => void
  /** After durable cancel (status=cancelled). */
  onCancelled?: () => void
  /** Latest actionable plan for this session (null when none). */
  onPlanChange?: (plan: TaskPlan | null) => void
}) {
  const { t } = useI18n()
  const [plan, setPlanState] = useState<TaskPlan | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const sigRef = useRef('')
  const onPlanChangeRef = useRef(onPlanChange)
  onPlanChangeRef.current = onPlanChange

  const setPlan = useCallback((p: TaskPlan | null) => {
    const sig = planSignature(p)
    if (sig === sigRef.current) return
    sigRef.current = sig
    setPlanState(p)
    onPlanChangeRef.current?.(p)
  }, [])

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
  }, [sessionId, setPlan])

  // Session switch: drop chrome immediately (avoid flash of previous plan).
  useEffect(() => {
    setPlan(null)
    setError(null)
  }, [sessionId, setPlan])

  // Load when entering Plan mode or after session settles; slow poll in Plan mode.
  useEffect(() => {
    if (!sessionId) return
    void refresh()
    if (!planMode) return
    const t = window.setInterval(() => void refresh(), 8000)
    return () => window.clearInterval(t)
  }, [planMode, sessionId, refresh])

  // Event-driven refresh: plan tool finished / turn ended.
  useEffect(() => {
    if (!sessionId || refreshSignal === 0) return
    void refresh()
  }, [refreshSignal, sessionId, refresh])

  const options = useMemo(() => extractPlanOptions(plan), [plan])

  if (!sessionId) return null
  if (!shouldShowPlanBanner(plan, planMode)) return null

  const steps: PlanStep[] = plan?.steps || []
  const status = String(plan?.status || 'draft').toLowerCase()
  const showApprove = Boolean(plan?.id) && isPlanActionable(status)
  const midBuild = status === 'approved' || status === 'active'
  const locked = busy || streaming
  const doneCount = steps.filter((s) => stepStatusChip(s.status).key === 'done').length

  const handleApprove = async (edit: boolean) => {
    if (!plan?.id || locked || !isPlanActionable(status)) return
    setBusy(true)
    setError(null)
    try {
      // Persist approval for drafts; already-approved/active just enter Build.
      if (status === 'draft') {
        const updated = await approvePlan(plan.id)
        if (updated) setPlan(updated)
      }
      onApproveBuild({ edit })
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
      ? t('plan.modeProgress')
      : t('plan.mode')
    : t('plan.ready')

  const approveTitle = streaming
    ? PLAN_STREAMING_HINT
    : midBuild
      ? 'Continue in Build mode (Shift+click to edit the message first)'
      : 'Approve the plan and send "Implement the approved plan" (Shift+click to edit first)'

  return (
    <div
      className="ui-banner ui-banner-plan mx-3 mt-2 mb-1 text-xs shrink-0"
      style={{ color: 'var(--text-primary)' }}
      data-plan-banner
      data-plan-mode={planMode ? 'true' : 'false'}
      data-plan-status={status}
      data-plan-streaming={streaming ? 'true' : 'false'}
    >
      <div className="flex items-center gap-2 mb-1.5 min-w-0">
        <span
          className="font-semibold uppercase tracking-wide text-[0.68rem] shrink-0"
          style={{ color: 'var(--accent)' }}
        >
          {headerLabel}
        </span>
        {plan?.title && (
          <span className="truncate font-medium min-w-0" title={plan.title}>
            {plan.title}
          </span>
        )}
        {plan?.status && (
          <span className={`plan-chip plan-chip-${stepStatusChip(status === 'draft' ? 'pending' : status).key} shrink-0`}>
            {plan.status}
          </span>
        )}
        {steps.length > 0 && (
          <span className="opacity-70 text-[0.7rem] tabular-nums shrink-0">
            {doneCount}/{steps.length}
          </span>
        )}
        {streaming && (
          <span
            className="ml-auto text-[0.7rem] shrink-0 flex items-center gap-1"
            style={{ color: 'var(--text-muted)' }}
            title={PLAN_STREAMING_HINT}
          >
            <span className="live-stream-dot" aria-hidden />
            {t('plan.working')}
          </span>
        )}
        <button
          type="button"
          className={`${streaming ? '' : 'ml-auto '}ui-btn ui-btn-ghost shrink-0`}
          style={{ padding: '0.15rem 0.4rem', fontSize: '0.7rem' }}
          title="Refresh plan"
          aria-label="Refresh plan"
          onClick={() => void refresh()}
          disabled={loading || busy}
        >
          ↻
        </button>
      </div>
      {planMode && (
        <div className="mb-1.5 text-[0.7rem]" style={{ color: 'var(--text-muted)' }} data-plan-hint>
          {t('plan.hint')} <kbd className="opacity-80">Ctrl+B</kbd>
        </div>
      )}
      {plan?.goal && plan.goal !== plan.title && (
        <div className="mb-1.5 opacity-90 break-words" style={{ color: 'var(--text-secondary)' }}>
          Goal: {plan.goal}
        </div>
      )}
      {steps.length > 0 && (
        <ol className="plan-steps mb-2.5" style={{ color: 'var(--text-secondary)' }}>
          {steps.slice(0, 8).map((s, i) => {
            const chip = stepStatusChip(s.status)
            const block = String(s.block_reason || '').trim()
            const seen = String(s.observed || '').trim()
            const blockLabel =
              block === 'need_you'
                ? 'needs you'
                : block === 'couldnt_verify'
                  ? "couldn't verify"
                  : block === 'env_changed'
                    ? 'environment changed'
                    : block === 'tool_failed'
                      ? 'tool failed'
                      : block === 'skipped'
                        ? 'skipped'
                        : block
            return (
              <li key={s.id || i} className="plan-step" data-step-status={chip.key}>
                <span className="plan-step-n tabular-nums">{i + 1}.</span>
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="plan-step-title min-w-0 break-words" title={s.detail || s.intended || undefined}>
                    {s.title}
                  </span>
                  {seen ? (
                    <span className="text-[0.68rem] opacity-80 break-words" style={{ color: 'var(--text-muted)' }}>
                      {seen}
                    </span>
                  ) : null}
                </span>
                {blockLabel ? (
                  <span className="plan-chip plan-chip-blocked shrink-0">{blockLabel}</span>
                ) : (
                  <span className={`plan-chip plan-chip-${chip.key}`}>{chip.label}</span>
                )}
              </li>
            )
          })}
          {steps.length > 8 && (
            <li className="plan-step" style={{ color: 'var(--text-muted)' }}>
              <span className="plan-step-n" />
              <span>…+{steps.length - 8} more</span>
            </li>
          )}
        </ol>
      )}
      {!plan && planMode && (
        <div className="mb-2.5" style={{ color: 'var(--text-muted)' }}>
          {loading
            ? t('plan.loading')
            : streaming
              ? t('plan.researching')
              : t('plan.empty')}
        </div>
      )}
      {options.length > 0 && (
        <div className="flex flex-wrap items-center gap-1 mb-2" data-plan-options>
          <span className="text-[0.7rem]" style={{ color: 'var(--text-muted)' }}>
            Pick:
          </span>
          {options.map((o) => (
            <button
              key={o}
              type="button"
              className="plan-option-chip"
              title={`Insert "${o} is fine" into the composer`}
              onClick={() => onRequestChanges(`${o} is fine`)}
              disabled={streaming}
            >
              {o}
            </button>
          ))}
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
            onClick={(e) => void handleApprove(Boolean(e.shiftKey))}
            title={approveTitle}
            aria-disabled={locked || !plan?.id}
            disabled={locked || !plan?.id}
            data-plan-approve
          >
            {midBuild ? t('plan.continue') : t('plan.approve')}
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
            title={streaming ? PLAN_STREAMING_HINT : 'Pre-fill a revision request in the composer'}
            disabled={locked}
            data-plan-request-changes
          >
            {t('plan.revise')}
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
          {plan?.id ? t('plan.cancel') : t('plan.quit')}
        </button>
      </div>
      <div className="mt-1.5 text-[10px]" style={{ color: 'var(--text-muted)' }}>
        Toggle Plan/Build: <kbd className="opacity-80">Ctrl+B</kbd> or{' '}
        <kbd className="opacity-80">Shift+Tab</kbd>
        {showApprove ? ' · Shift+click Approve to edit the message first' : null}
        {isPlanActionable(status) && plan?.id
          ? ' · Cancel plan quits for good (not just hide)'
          : null}
      </div>
    </div>
  )
}
