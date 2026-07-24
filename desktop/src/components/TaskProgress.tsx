import { useEffect, useMemo, useState } from 'react'
import type { ActiveTool } from '../hooks/useMessages'
import { toolLabel } from '../utils/toolLabels'

export type TaskProgressInfo = {
  /** 0–100 when known; omit for indeterminate */
  percent?: number | null
  /** Short label, e.g. "comfyui · generate" */
  label?: string
  /** Optional ETA string from the backend */
  eta?: string | null
  /** Optional step counts for multi-step jobs */
  step?: number | null
  total?: number | null
}

interface TaskProgressProps {
  streaming: boolean
  activeTools?: ActiveTool[]
  /** Explicit job progress when the server reports it */
  progress?: TaskProgressInfo | null
  /** When true, list tool names under the bar */
  showToolDetails?: boolean
}

function clampPct(n: number): number {
  return Math.max(0, Math.min(100, n))
}

/**
 * Generic progress for any long-running work (tools, jobs, model wait).
 * Shown whenever a chat turn is active — not partner-branded.
 * Prefer server % when present; otherwise derive from tool step completion.
 */
export function TaskProgress({
  streaming,
  activeTools = [],
  progress = null,
  showToolDetails = false,
}: TaskProgressProps) {
  const [elapsed, setElapsed] = useState(0)
  const running = useMemo(
    () => activeTools.filter((t) => t.status === 'running'),
    [activeTools],
  )
  const doneCount = useMemo(
    () => activeTools.filter((t) => t.status === 'done').length,
    [activeTools],
  )
  const totalTools = activeTools.length
  const hasTools = totalTools > 0

  const derived = useMemo(() => {
    // Server wins when it reports a real percent.
    if (progress?.percent != null && Number.isFinite(progress.percent)) {
      return {
        pct: clampPct(Number(progress.percent)),
        determinate: true as const,
      }
    }
    // Multi-step tools: estimate from completed + partial credit for in-flight.
    if (totalTools > 0) {
      const partial = running.length > 0 ? 0.35 * running.length : 0
      const raw = ((doneCount + partial) / totalTools) * 100
      // Keep bar moving while tools still run (never sit at 100 early).
      const capped =
        running.length > 0 || doneCount < totalTools
          ? Math.min(raw, 96)
          : 100
      return { pct: clampPct(capped), determinate: true as const }
    }
    // Server step counts without percent.
    if (
      progress?.step != null
      && progress?.total != null
      && progress.total > 0
    ) {
      const raw = (Number(progress.step) / Number(progress.total)) * 100
      return { pct: clampPct(raw), determinate: true as const }
    }
    return { pct: null as number | null, determinate: false as const }
  }, [progress, totalTools, doneCount, running.length])

  useEffect(() => {
    if (!streaming) {
      setElapsed(0)
      return
    }
    const t0 = Date.now()
    const id = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - t0) / 1000))
    }, 400)
    return () => window.clearInterval(id)
  }, [streaming, running.map((t) => t.name).join(',')])

  if (!streaming) return null

  const stepLabel =
    progress?.step != null && progress?.total != null && progress.total > 0
      ? `${progress.step}/${progress.total}`
      : hasTools
        ? `${doneCount}/${totalTools}`
        : null

  const label =
    progress?.label
    || (running.length === 1
      ? toolLabel(running[0].name)
      : running.length > 1
        ? `${running.length} steps`
        : hasTools && doneCount === totalTools
          ? 'Finishing…'
          : hasTools
            ? 'Working…'
            : 'Working…')

  const mm = Math.floor(elapsed / 60)
  const ss = String(elapsed % 60).padStart(2, '0')
  const timeLabel = `${mm}:${ss}`
  const { pct, determinate } = derived

  return (
    <div className="px-4 py-2 flex justify-start w-full">
      <div
        className="w-full max-w-[min(var(--chat-max-width),100%)] rounded-lg px-3 py-2 space-y-1.5"
        style={{
          background: 'var(--bg-tertiary)',
          border: '1px solid var(--border)',
        }}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={determinate && pct != null ? Math.round(pct) : undefined}
        aria-busy={!determinate}
        aria-label={label}
      >
        <div className="flex items-center justify-between gap-2 text-[11px]">
          <span className="truncate font-medium" style={{ color: 'var(--text-primary)' }}>
            {label}
            {stepLabel ? (
              <span className="font-normal" style={{ color: 'var(--text-muted)' }}>
                {' '}· {stepLabel}
              </span>
            ) : null}
          </span>
          <span className="flex-shrink-0 font-mono tabular-nums" style={{ color: 'var(--text-muted)' }}>
            {determinate && pct != null ? `${Math.round(pct)}%` : timeLabel}
            {progress?.eta ? ` · ${progress.eta}` : ''}
            {!determinate || pct == null ? '' : ` · ${timeLabel}`}
          </span>
        </div>

        <div
          className="h-1.5 rounded-full overflow-hidden relative"
          style={{ background: 'var(--bg-secondary)' }}
        >
          {determinate && pct != null ? (
            <div
              className="h-full rounded-full transition-[width] duration-300 ease-out"
              style={{
                width: `${pct}%`,
                background: 'var(--accent)',
              }}
            />
          ) : (
            <div
              className="h-full rounded-full absolute top-0 left-0"
              style={{
                width: '35%',
                background: 'var(--accent)',
                animation: 'remedy-progress-slide 1.35s ease-in-out infinite',
              }}
            />
          )}
        </div>

        {showToolDetails && hasTools && (
          <div className="flex flex-wrap gap-1 pt-0.5">
            {activeTools.map((t, i) => (
              <span
                key={`${t.name}-${i}`}
                className="text-[10px] px-1.5 py-0.5 rounded font-mono"
                style={{
                  border: `1px solid ${t.status === 'running' ? 'var(--accent)' : 'var(--border)'}`,
                  color: t.status === 'running' ? 'var(--accent)' : 'var(--text-muted)',
                }}
              >
                {t.status === 'running' ? '…' : t.status === 'error' ? '!' : '✓'}{' '}
                {toolLabel(t.name)}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
