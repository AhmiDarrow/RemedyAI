import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { ProcessStep, ToolProcessMode } from '../utils/toolLabels'
import { summarizeSteps } from '../utils/liveSegments'
import {
  formatElapsed,
  groupSteps,
  liveStatusLine,
  progressPercent,
  shortError,
  stepTarget,
  toolShortName,
  type StripProgress,
} from '../utils/workTimeline'
import { useStickToBottom } from '../hooks/useStickToBottom'
import { useI18n } from '../i18n'
import { formatCount } from '../i18n/locale'
import { useAnchorToggle } from './feedScroll'
import { ProcessTrace } from './ProcessTrace'

/**
 * All background work for one turn, in one quiet strip:
 *   ● Working · 36 steps · 2 errors · 0:06            ▸
 *   › Bash  powershell -c "Get-ChildItem …"
 * Collapsed it is a single summary line (plus the live status line while the
 * turn runs). Expanded it is a height-capped timeline of tool rows (short
 * target, result icon, errors in muted red); thinking is its own expandable
 * item. Assistant prose never renders here — that belongs in the chat bubble.
 */
export interface WorkStripProps {
  steps: ProcessStep[]
  thinking?: string
  /** Turn still running. */
  live?: boolean
  /** Reply text has started streaming (hides the live status line). */
  replying?: boolean
  progress?: StripProgress | null
  /** Known wall time for the work, when the caller has it. */
  elapsedMs?: number | null
  mode?: ToolProcessMode
  defaultOpen?: boolean
  sessionId?: string | null
  /** Extra live items for the open body (todos). */
  extras?: ReactNode
}

/** Long step lists render only their tail until asked. */
export const STEP_WINDOW = 60

function useTicker(active: boolean, ms = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const id = window.setInterval(() => setNow(Date.now()), ms)
    return () => window.clearInterval(id)
  }, [active, ms])
  return now
}

const StepRow = memo(function StepRow({ step }: { step: ProcessStep }) {
  const [open, setOpen] = useState(false)
  const target = stepTarget(step)
  const err = step.status === 'error' ? shortError(step.error || step.resultText) : ''
  const detail = step.status === 'error' ? step.error || step.resultText : step.resultText
  return (
    <li className={`ws-step is-${step.status}`} data-status={step.status}>
      <button
        type="button"
        className="ws-step-row"
        aria-expanded={detail ? open : undefined}
        onClick={() => detail && setOpen((o) => !o)}
        title={target || step.label}
      >
        <span className="ws-icon" aria-label={step.status}>
          {step.status === 'running' ? '◌' : step.status === 'error' ? '✕' : '✓'}
        </span>
        <span className="ws-tool">{toolShortName(step.name)}</span>
        <span className="ws-target">{target || step.label}</span>
      </button>
      {err && <div className="ws-err">{err}</div>}
      {open && detail && <pre className="ws-detail">{detail.slice(0, 1200)}</pre>}
    </li>
  )
})

function ThinkingItem({ text, live }: { text: string; live?: boolean }) {
  const { resolved } = useI18n()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement | null>(null)
  const anchor = useAnchorToggle()
  return (
    <div className="ws-thinking" ref={ref} data-testid="ws-thinking">
      <button
        type="button"
        className="ws-thinking-toggle"
        aria-expanded={open}
        onClick={() => anchor(ref.current, () => setOpen((o) => !o))}
      >
        <span aria-hidden>{open ? '▾' : '▸'}</span> {live ? 'Thinking' : 'Thought'}
        <span className="ws-dim"> · {formatCount(text.length, resolved)} chars</span>
      </button>
      {open && <div className="ws-thinking-body">{text}</div>}
    </div>
  )
}

function Timeline({ steps, live }: { steps: ProcessStep[]; live?: boolean }) {
  const [showAll, setShowAll] = useState(false)
  const hidden = showAll ? 0 : Math.max(0, steps.length - STEP_WINDOW)
  const shown = hidden ? steps.slice(hidden) : steps
  const groups = useMemo(() => groupSteps(shown), [shown])
  const last = steps[steps.length - 1]
  // The inner list follows its own newest step under the same rules as the feed.
  const { setScroller, setContent } = useStickToBottom({
    followActive: live,
    // A finished turn reads top-down; a live one follows its newest step.
    startAtBottom: Boolean(live),
    deps: [steps.length, last?.status],
  })
  return (
    <div className="ws-timeline" ref={setScroller} data-testid="ws-timeline">
      <ol className="ws-groups" ref={setContent}>
        {hidden > 0 && (
          <li className="ws-more">
            <button type="button" onClick={() => setShowAll(true)}>
              Show {hidden} earlier step{hidden === 1 ? '' : 's'}
            </button>
          </li>
        )}
        {groups.map((g) => (
          <li key={g.steps[0]!.id} className="ws-group">
            <ul className="ws-steps">
              {g.steps.map((s) => (
                <StepRow key={s.id} step={s} />
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </div>
  )
}

export const WorkStrip = memo(function WorkStrip({
  steps,
  thinking = '',
  live = false,
  replying = false,
  progress = null,
  elapsedMs = null,
  mode = 'off',
  defaultOpen = false,
  sessionId = null,
  extras,
}: WorkStripProps) {
  const [open, setOpen] = useState(defaultOpen)
  const wasLive = useRef(live)
  useEffect(() => {
    if (wasLive.current && !live) setOpen(false)
    wasLive.current = live
  }, [live])
  const [rawTrace, setRawTrace] = useState(false)
  const ref = useRef<HTMLElement | null>(null)
  const anchor = useAnchorToggle()
  const sum = useMemo(() => summarizeSteps(steps), [steps])
  const now = useTicker(live)
  const thought = thinking.trim()
  // Nothing in the background (plain chat reply): no strip at all.
  if (!steps.length && !thought && (!live || replying)) return null

  const start = steps.length ? Math.min(...steps.map((s) => s.startedAt)) : null
  const elapsed = elapsedMs ?? (live && start != null ? now - start : null)
  const pct = live && !replying ? progressPercent(progress) : null
  const status = liveStatusLine(steps, { progress, thinking: Boolean(thought) })
  // Open, the timeline itself is the status; replying, the bubble is.
  const showStatus = live && !replying && !open

  const parts: string[] = []
  if (sum.total) parts.push(`${sum.total} step${sum.total === 1 ? '' : 's'}`)
  const title = sum.total || !thought ? (live ? 'Working' : 'Worked') : live ? 'Thinking' : 'Thought'
  return (
    <section
      ref={ref}
      className={`work-strip${live ? ' is-live' : ''}${open ? ' is-open' : ''}`}
      data-testid="work-strip"
      aria-label="Background work"
    >
      <button
        type="button"
        className="ws-head"
        aria-expanded={open}
        onClick={() => anchor(ref.current, () => setOpen((o) => !o))}
      >
        {live ? (
          <span className="live-stream-dot" aria-hidden />
        ) : (
          <span className="ws-head-icon" aria-hidden>
            ⋯
          </span>
        )}
        <span className="ws-title">{title}</span>
        {parts.map((p) => (
          <span key={p} className="ws-meta">
            · {p}
          </span>
        ))}
        {sum.errors > 0 && (
          <span className="ws-meta ws-meta-err">
            · {sum.errors} error{sum.errors === 1 ? '' : 's'}
          </span>
        )}
        {elapsed != null && <span className="ws-meta">· {formatElapsed(elapsed)}</span>}
        <span className="ws-chevron" aria-hidden>
          {open ? '▾' : '▸'}
        </span>
      </button>
      {pct != null && (
        <div
          className="ws-progress"
          role="progressbar"
          aria-valuenow={Math.round(pct)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={progress?.label || 'Progress'}
        >
          <span style={{ width: `${pct}%` }} />
        </div>
      )}
      {showStatus && (
        <div className="ws-status" data-testid="ws-status" aria-live="polite">
          <span aria-hidden className="ws-status-caret">
            ›
          </span>
          {status.tool && <span className="ws-tool">{status.tool}</span>}
          <span className="ws-status-text">{status.text}</span>
        </div>
      )}
      {open && (
        <div className="ws-body">
          {thought && <ThinkingItem text={thought} live={live} />}
          {extras}
          {steps.length > 0 && <Timeline steps={steps} live={live} />}
          {steps.length > 0 && !live && (
            <button type="button" className="ws-raw" onClick={() => setRawTrace((r) => !r)}>
              {rawTrace ? 'Hide full output' : 'Full output…'}
            </button>
          )}
          {rawTrace && !live && (
            <ProcessTrace mode={mode === 'off' ? 'full' : mode} steps={steps} sessionId={sessionId} />
          )}
        </div>
      )}
    </section>
  )
})
