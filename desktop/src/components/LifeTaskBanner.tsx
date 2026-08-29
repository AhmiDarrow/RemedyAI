import { useCallback, useEffect, useRef, useState } from 'react'
import { useI18n } from '../i18n'
import {
  actLifeTask,
  getCurrentLifeTask,
  probeLifeTask,
  type LifeTaskCard,
  type LifeTaskStep,
} from '../api/partner'
import { stepStatusChip } from './PlanBanner'

export function lifeTaskHeadline(card: LifeTaskCard | null): string {
  if (!card) return ''
  const spoken = String(card.spoken || '').trim()
  if (spoken) return spoken
  const goal = String(card.goal || '').trim()
  const step = card.step
  const total = card.total
  if (step && total) {
    const title = String(card.title || 'the next step')
    return `Step ${step} of ${total} — ${title}.`
  }
  return goal ? `Toward ${goal}.` : ''
}

export function shouldShowLifeTaskBanner(card: LifeTaskCard | null): boolean {
  if (!card) return false
  const st = String(card.status || '').toLowerCase()
  if (st === 'cancelled') return false
  return Boolean(
    card.spoken
    || card.steps?.length
    || card.approval_id
    || st === 'running'
    || st === 'need_you'
    || st === 'done'
    || st === 'blocked',
  )
}

function chipForStep(s: LifeTaskStep): { key: string; label: string } {
  const st = String(s.status || 'pending').toLowerCase()
  if (st === 'need_you') return { key: 'blocked', label: 'needs you' }
  if (st === 'skipped') return { key: 'blocked', label: 'you handled' }
  return stepStatusChip(st)
}

export function LifeTaskBanner({
  sessionId,
  refreshSignal = 0,
  onSpeak,
  onExplain,
}: {
  sessionId: string | null
  refreshSignal?: number
  onSpeak?: (text: string) => void
  onExplain?: (text: string) => void
}) {
  const { t } = useI18n()
  const [card, setCard] = useState<LifeTaskCard | null>(null)
  const [busy, setBusy] = useState(false)
  const [explain, setExplain] = useState('')
  const [message, setMessage] = useState('')
  const spokenRef = useRef('')

  const refresh = useCallback(async () => {
    try {
      const data = await getCurrentLifeTask(sessionId)
      setCard(data.task || null)
    } catch {
      // server down
    }
  }, [sessionId])

  useEffect(() => {
    void refresh()
    const id = window.setInterval(() => void refresh(), 1500)
    return () => window.clearInterval(id)
  }, [refresh, sessionId])

  const autoHandoff = Boolean(card?.handoff?.auto) && String(card?.status || '') === 'need_you'
  useEffect(() => {
    if (!autoHandoff) return
    let cancelled = false
    const tick = async () => {
      try {
        const res = await probeLifeTask({
          sessionId,
          taskId: card?.task_id,
        })
        if (cancelled) return
        if (res.task) setCard(res.task)
        if (res.spoken && res.cleared) setMessage(res.spoken)
      } catch {
        // server down
      }
    }
    void tick()
    const id = window.setInterval(() => void tick(), 2500)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [autoHandoff, sessionId, card?.task_id])

  useEffect(() => {
    if (refreshSignal === 0) return
    void refresh()
  }, [refreshSignal, refresh])

  useEffect(() => {
    const spoken = lifeTaskHeadline(card)
    if (spoken && spoken !== spokenRef.current) {
      spokenRef.current = spoken
      onSpeak?.(spoken)
    }
  }, [card, onSpeak])

  const act = async (action: 'yes' | 'no' | 'explain') => {
    setBusy(true)
    setMessage('')
    try {
      const res = await actLifeTask(action, {
        sessionId,
        taskId: card?.task_id,
        approvalId: card?.approval_id,
      })
      if (action === 'explain') {
        const text = String(res.spoken || '')
        setExplain(text)
        onExplain?.(text)
      } else {
        setExplain('')
        setMessage(String(res.spoken || (action === 'yes' ? t('lifeTask.yes') : t('lifeTask.no'))))
      }
      if (res.task) setCard(res.task)
      else await refresh()
    } catch (e: unknown) {
      setMessage(e instanceof Error ? e.message : 'Failed')
    } finally {
      setBusy(false)
    }
  }

  if (!shouldShowLifeTaskBanner(card) && !message) return null
  const steps = card?.steps || []
  const st = String(card?.status || '').toLowerCase()
  const choices = card?.choices || []
  const showAsk = choices.includes('yes') || st === 'need_you' || card?.kind === 'plan_gate'
  const headline = lifeTaskHeadline(card)
  const stepLabel =
    card?.step != null && card?.total != null && card.total > 0
      ? `${card.step}/${card.total}`
      : steps.length
        ? `${steps.filter((s) => chipForStep(s).key === 'done').length}/${steps.length}`
        : null

  return (
    <div
      className="ui-banner ui-banner-plan mx-3 mt-2 mb-1 text-xs shrink-0"
      style={{ color: 'var(--text-primary)' }}
      data-life-task-banner
      data-life-task-status={st}
      role="region"
      aria-label={t('lifeTask.region')}
    >
      <div className="flex items-center gap-2 mb-1.5 min-w-0">
        <span
          className="font-semibold uppercase tracking-wide text-[0.68rem] shrink-0"
          style={{ color: 'var(--accent)' }}
        >
          {st === 'need_you'
            ? t('lifeTask.needsYou')
            : st === 'done'
              ? t('lifeTask.done')
              : st === 'blocked'
                ? t('lifeTask.blocked')
                : t('lifeTask.working')}
        </span>
        {card?.goal ? (
          <span className="truncate font-medium min-w-0" title={card.goal}>
            {card.goal}
          </span>
        ) : null}
        {stepLabel ? (
          <span className="opacity-70 text-[0.7rem] tabular-nums shrink-0 ml-auto">
            {t('lifeTask.step')} {stepLabel}
          </span>
        ) : null}
      </div>
      {headline ? (
        <div className="mb-1.5 text-sm" style={{ color: 'var(--text-primary)' }}>
          {headline}
        </div>
      ) : null}
      {steps.length > 0 && (
        <ol className="plan-steps mb-2.5" style={{ color: 'var(--text-secondary)' }}>
          {steps.slice(0, 8).map((s, i) => {
            const chip = chipForStep(s)
            const seen = String(s.observed || '').trim()
            return (
              <li key={`${s.title}-${i}`} className="plan-step" data-step-status={chip.key}>
                <span className="plan-step-n tabular-nums">{i + 1}.</span>
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="plan-step-title min-w-0 break-words">{s.title}</span>
                  {seen ? (
                    <span className="text-[0.68rem] opacity-80 break-words" style={{ color: 'var(--text-muted)' }}>
                      {seen}
                    </span>
                  ) : null}
                </span>
                <span className={`plan-chip plan-chip-${chip.key}`}>{chip.label}</span>
              </li>
            )
          })}
        </ol>
      )}
      {explain ? (
        <div
          className="mb-2.5 text-[0.75rem] whitespace-pre-wrap break-words"
          style={{ color: 'var(--text-secondary)' }}
          data-life-task-explain
        >
          {explain}
        </div>
      ) : null}
      {message ? (
        <div className="mb-2 text-xs" style={{ color: 'var(--success)' }}>
          {message}
        </div>
      ) : null}
      <div className="flex flex-wrap gap-1.5">
        {showAsk && (
          <button
            type="button"
            className="ui-btn ui-btn-primary"
            disabled={busy}
            onClick={() => void act('yes')}
            data-life-task-yes
          >
            {busy ? t('lifeTask.working') : t('lifeTask.yes')}
          </button>
        )}
        {showAsk && (
          <button
            type="button"
            className="ui-btn ui-btn-secondary"
            disabled={busy}
            onClick={() => void act('no')}
            data-life-task-no
          >
            {t('lifeTask.no')}
          </button>
        )}
        <button
          type="button"
          className="ui-btn ui-btn-ghost"
          disabled={busy}
          onClick={() => void act('explain')}
          data-life-task-explain-btn
        >
          {t('lifeTask.explain')}
        </button>
      </div>
    </div>
  )
}
