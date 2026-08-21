import { useEffect, useMemo, useState } from 'react'
import {
  isFullProcessMode,
  type ProcessStep,
  type ToolProcessMode,
} from '../utils/toolLabels'
import { IconBtn, IconCheck, IconChevronDown, IconChevronUp, IconCopy } from './icons'
import { useStickToBottom } from '../hooks/useStickToBottom'
import { DiffCode } from './DiffCode'
import {
  formatToolArgsDisplay,
  formatToolResultDisplay,
  stepInlineSummary,
  stepMediumPreview,
} from '../utils/toolProcessFormat'

interface ProcessTraceProps {
  mode: ToolProcessMode
  steps: ProcessStep[]
  /** Live turn */
  live?: boolean
  /** Start with the Process panel collapsed */
  defaultCollapsed?: boolean
}

/** Viewport height — tighter on Min so chat stays primary. */
const LIST_MAX_H_MIN = 'min(22vh, 11rem)'
const LIST_MAX_H_MED = 'min(36vh, 20rem)'
const LIST_MAX_H_FULL = 'min(48vh, 28rem)'
/** Full-mode dump clip inside a step. */
const FULL_BLOCK_MAX_H = '14rem'
const MED_BLOCK_MAX_H = '6.5rem'
const FULL_PREVIEW_CHARS = 120_000
const MED_BODY_CHARS = 480
/** Min: show only this many recent chip runs (rest collapsed behind “earlier”). */
const MIN_RECENT_CHIPS = 18
/** Med list: collapse consecutive same-tool runs. */
const GROUP_MIN_COUNT = 2

function clipFull(text: string | undefined): string {
  if (!text) return ''
  if (text.length <= FULL_PREVIEW_CHARS) return text
  return `${text.slice(0, FULL_PREVIEW_CHARS)}…`
}

type StepRun = {
  key: string
  name: string
  label: string
  steps: ProcessStep[]
  /** Worst status in the run */
  status: ProcessStep['status']
}

/** Collapse consecutive same-name steps into runs (done/error only; running stays solo). */
function groupConsecutiveRuns(steps: ProcessStep[]): StepRun[] {
  const runs: StepRun[] = []
  for (const s of steps) {
    const prev = runs[runs.length - 1]
    const canMerge =
      prev
      && prev.name === s.name
      && s.status !== 'running'
      && prev.status !== 'running'
    if (canMerge && prev) {
      prev.steps.push(s)
      if (s.status === 'error') prev.status = 'error'
    } else {
      runs.push({
        key: s.id,
        name: s.name,
        label: s.label,
        steps: [s],
        status: s.status,
      })
    }
  }
  return runs
}

function tallyByLabel(steps: ProcessStep[]): { label: string; name: string; n: number; err: number }[] {
  const map = new Map<string, { label: string; name: string; n: number; err: number }>()
  for (const s of steps) {
    const k = s.name
    const cur = map.get(k) || { label: s.label, name: s.name, n: 0, err: 0 }
    cur.n += 1
    if (s.status === 'error') cur.err += 1
    map.set(k, cur)
  }
  return [...map.values()].sort((a, b) => b.n - a.n)
}

function statusGlyph(status: ProcessStep['status']): string {
  if (status === 'running') return '…'
  if (status === 'error') return '!'
  return '✓'
}

function statusColor(status: ProcessStep['status']): string {
  if (status === 'running') return 'var(--accent)'
  if (status === 'error') return 'var(--error)'
  return 'var(--success)'
}

/**
 * Tool process trail — one chrome for Min / Med / Full.
 *
 * - Min:  header counts + dense recent chips (no second tally strip; no TaskProgress chips)
 * - Med:  consecutive same-tool runs grouped; human label + path one-liner + short result
 * - Full: every step listed (not grouped) with complete args/results — no hidden dumps
 */
export function ProcessTrace({
  mode,
  steps,
  live = false,
  defaultCollapsed = false,
}: ProcessTraceProps) {
  const full = isFullProcessMode(mode)
  const med = mode === 'medium'
  const min = mode === 'off'
  const [collapsed, setCollapsed] = useState(defaultCollapsed && !live)
  const [showAllMin, setShowAllMin] = useState(false)
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set())

  const [copiedId, setCopiedId] = useState<string | null>(null)

  useEffect(() => {
    if (live) {
      setCollapsed(false)
      return
    }
    setCollapsed(defaultCollapsed)
  }, [mode, live, defaultCollapsed])

  // Reset “show all” when a new live turn starts (step list shrinks)
  useEffect(() => {
    if (steps.length < MIN_RECENT_CHIPS) setShowAllMin(false)
  }, [steps.length])

  /**
   * Full = one row per step (complete dumps).
   * Med/Min = collapse consecutive identical tools into runs.
   */
  const runs = useMemo(() => {
    if (full) {
      return steps.map(
        (s): StepRun => ({
          key: s.id,
          name: s.name,
          label: s.label,
          steps: [s],
          status: s.status,
        }),
      )
    }
    return groupConsecutiveRuns(steps)
  }, [steps, full])
  const tallies = useMemo(() => tallyByLabel(steps), [steps])

  const stepSig = steps
    .map(
      (s) =>
        `${s.id}:${s.status}:${(s.resultText || '').length}:${(s.argsText || '').length}`,
    )
    .join('|')

  const follow = live && !collapsed
  const listMaxH = min ? LIST_MAX_H_MIN : med ? LIST_MAX_H_MED : LIST_MAX_H_FULL
  const { setScroller, setContent, showJump, jumpLatest } = useStickToBottom({
    followActive: follow,
    alwaysOfferJump: follow || full,
    startAtBottom: live,
    deps: [stepSig, mode, collapsed, full, showAllMin],
  })

  // steps already changes identity when the stream updates; stepSig is only
  // for stick-to-bottom follow deps, not this memo.
  const formattedArgs = useMemo(() => {
    const prior = new Map<string, string>()
    const map = new Map<string, ReturnType<typeof formatToolArgsDisplay>>()
    for (const s of steps) {
      map.set(s.id, formatToolArgsDisplay(s.name, s.argsText, prior))
    }
    return map
  }, [steps])

  if (steps.length === 0) return null

  const running = steps.filter((s) => s.status === 'running').length
  const done = steps.filter((s) => s.status === 'done').length
  const failed = steps.filter((s) => s.status === 'error').length
  const summary =
    running > 0
      ? `${running} running · ${done} done`
      : failed
        ? `${done} done · ${failed} error`
        : `${done} step${done === 1 ? '' : 's'}`

  const toggleExpand = (id: string) => {
    if (full || min) return
    setExpandedIds((prev) => {
      const n = new Set(prev)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
  }

  const copyBlock = async (id: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopiedId(id)
      window.setTimeout(() => setCopiedId(null), 1200)
    } catch {
      /* */
    }
  }

  // Min: only recent runs as chips unless “show all”
  const minHidden = min && !showAllMin && runs.length > MIN_RECENT_CHIPS
  const minVisibleRuns = minHidden
    ? runs.slice(runs.length - MIN_RECENT_CHIPS)
    : runs
  const minEarlierCount = minHidden
    ? runs.slice(0, runs.length - MIN_RECENT_CHIPS).reduce((n, r) => n + r.steps.length, 0)
    : 0

  return (
    <div
      className="process-trace rounded-xl overflow-hidden text-[11px] my-1 relative w-full"
      style={{
        border: '1px solid color-mix(in srgb, var(--border) 88%, transparent)',
        background: 'color-mix(in srgb, var(--bg-primary) 88%, var(--bg-secondary))',
        maxWidth: 'min(var(--chat-max-width), 100%)',
        boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
      }}
      data-process-mode={mode}
    >
      <button
        type="button"
        className="w-full flex items-center justify-between gap-2 px-2.5 py-1.5 text-left"
        style={{
          color: 'var(--text-muted)',
          background: 'color-mix(in srgb, var(--bg-tertiary) 75%, transparent)',
        }}
        onClick={() => setCollapsed((c) => !c)}
        aria-expanded={!collapsed}
      >
        <span className="font-semibold tracking-wide min-w-0 truncate" style={{ color: 'var(--text-secondary)' }}>
          Process
          <span className="font-normal ml-1.5" style={{ color: 'var(--text-muted)' }}>
            {summary}
          </span>
          {/* One place for type counts — not repeated as a second chip row */}
          {min && tallies.length > 0 && (
            <span className="font-normal ml-1.5 opacity-80" style={{ color: 'var(--text-muted)' }}>
              · {tallies.slice(0, 4).map((t) => `${t.label} ${t.n}`).join(' · ')}
              {tallies.length > 4 ? '…' : ''}
            </span>
          )}
        </span>
        {collapsed ? <IconChevronDown size={12} /> : <IconChevronUp size={12} />}
      </button>

      {!collapsed && (
        <div className="relative">
          {/*
            Min: chips only (no separate tally strip — counts live in the header).
            Med/Full: grouped rows below.
          */}
          <div
            ref={setScroller}
            className="px-2 py-1.5 overflow-y-auto"
            style={{ maxHeight: listMaxH }}
          >
            <div ref={setContent}>
              {/* ─── MIN: dense chip trail ─── */}
              {min && (
                <div className="space-y-1.5">
                  {minEarlierCount > 0 && (
                    <button
                      type="button"
                      className="text-[10px] px-1 py-0.5 rounded"
                      style={{
                        background: 'var(--bg-secondary)',
                        border: '1px solid var(--border)',
                        color: 'var(--accent)',
                        cursor: 'pointer',
                      }}
                      onClick={() => setShowAllMin(true)}
                    >
                      +{minEarlierCount} earlier
                    </button>
                  )}
                  {showAllMin && runs.length > MIN_RECENT_CHIPS && (
                    <button
                      type="button"
                      className="text-[10px] px-1 py-0.5"
                      style={{
                        background: 'none',
                        border: 'none',
                        color: 'var(--text-muted)',
                        cursor: 'pointer',
                      }}
                      onClick={() => setShowAllMin(false)}
                    >
                      Show recent only
                    </button>
                  )}
                  <div className="process-min-chips flex flex-wrap gap-1 content-start">
                    {minVisibleRuns.map((run) => {
                      const n = run.steps.length
                      const label = n > 1 ? `${run.label} ×${n}` : run.label
                      const runningNow = run.status === 'running'
                      return (
                        <span
                          key={run.key}
                          className="process-min-chip inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] leading-tight"
                          style={{
                            background: runningNow
                              ? 'color-mix(in srgb, var(--accent) 12%, var(--bg-secondary))'
                              : 'var(--bg-secondary)',
                            border: `1px solid ${
                              runningNow
                                ? 'color-mix(in srgb, var(--accent) 35%, var(--border))'
                                : 'var(--border)'
                            }`,
                            color: 'var(--text-secondary)',
                          }}
                          title={`${run.label} (${run.name})${n > 1 ? ` ×${n}` : ''}`}
                        >
                          <span
                            className="font-mono text-[10px] w-2.5 text-center flex-shrink-0"
                            style={{ color: statusColor(run.status) }}
                          >
                            {statusGlyph(run.status)}
                          </span>
                          <span className="truncate max-w-[9.5rem]">{label}</span>
                        </span>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* ─── MED: grouped runs · FULL: every step ─── */}
              {!min && (
                <ul className="space-y-1 list-none m-0 p-0">
                  {runs.map((run) => {
                    const s = run.steps[run.steps.length - 1]
                    const grouped = med && run.steps.length >= GROUP_MIN_COUNT
                    const expandKey = run.key
                    // One-liner: last path/cmd in the run (Med only — Full has dumps)
                    let bestInline = ''
                    if (med) {
                      for (let i = run.steps.length - 1; i >= 0; i--) {
                        const st = run.steps[i]
                        const line = stepInlineSummary(
                          st.name,
                          st.argsText,
                          st.resultText,
                          st.error,
                        )
                        if (line) {
                          bestInline = line
                          break
                        }
                      }
                    }
                    const medBody = med
                      ? stepMediumPreview(s.resultText, s.error, {
                          maxLines: expandedIds.has(expandKey) ? 12 : 3,
                          maxChars: expandedIds.has(expandKey) ? 1600 : MED_BODY_CHARS,
                        })
                      : ''
                    // Med: one-liner is enough until “Show more”; avoid args caption + body doubling path
                    const showMedExpandable =
                      med
                      && Boolean(medBody || s.error || (s.argsText && (s.resultText || s.error)))
                    const showFullBody =
                      full && Boolean(s.argsText || s.resultText || s.error || s.status === 'running')
                    const rowLabel =
                      grouped ? `${run.label} ×${run.steps.length}` : run.label

                    const rawDump = [
                      `// ${run.label} (${run.name})${grouped ? ` ×${run.steps.length}` : ''}`,
                      ...run.steps.flatMap((st, i) => {
                        const parts = [
                          st.argsText
                            ? `// --- args${grouped ? ` [${i + 1}]` : ''} ---\n${st.argsText}`
                            : '',
                          st.error
                            ? `// --- error ---\n${st.error}`
                            : st.resultText
                              ? `// --- result ---\n${st.resultText}`
                              : '',
                        ]
                        return parts.filter(Boolean)
                      }),
                    ]
                      .filter(Boolean)
                      .join('\n\n')

                    return (
                      <li
                        key={run.key}
                        className="rounded-md px-1.5 py-1"
                        style={{ background: 'var(--bg-secondary)' }}
                      >
                        <div className="flex items-start gap-1.5">
                          <div className="flex-1 flex items-start gap-1.5 min-w-0">
                            <span
                              className="flex-shrink-0 font-mono w-3 text-center pt-0.5"
                              style={{ color: statusColor(run.status) }}
                            >
                              {statusGlyph(run.status)}
                            </span>
                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-baseline gap-x-1.5">
                                <span
                                  className="font-medium"
                                  style={{ color: 'var(--text-primary)' }}
                                >
                                  {rowLabel}
                                </span>
                                {/* Full only: raw tool id for debugging; Med stays human labels */}
                                {full && (
                                  <span
                                    className="font-mono text-[10px]"
                                    style={{ color: 'var(--text-muted)' }}
                                  >
                                    {s.name}
                                  </span>
                                )}
                                {s.endedAt && s.startedAt && s.status !== 'running' && (
                                  <span style={{ color: 'var(--text-muted)' }}>
                                    {Math.max(
                                      0,
                                      (s.endedAt - s.startedAt) / 1000,
                                    ).toFixed(1)}
                                    s
                                  </span>
                                )}
                              </div>
                              {/* Med: path/command one-liner only (not repeated in Full dumps) */}
                              {med && bestInline && (
                                <div
                                  className="mt-0.5 font-mono text-[10px] truncate"
                                  style={{ color: 'var(--text-secondary)' }}
                                  title={bestInline}
                                >
                                  {bestInline}
                                </div>
                              )}
                            </div>
                          </div>
                          {rawDump && (
                            <IconBtn
                              title={copiedId === expandKey ? 'Copied' : 'Copy'}
                              onClick={() => void copyBlock(expandKey, rawDump)}
                              active={copiedId === expandKey}
                            >
                              {copiedId === expandKey ? (
                                <IconCheck size={12} />
                              ) : (
                                <IconCopy size={12} />
                              )}
                            </IconBtn>
                          )}
                        </div>

                        {/* Med: short result on demand — no second path line from args caption */}
                        {showMedExpandable && (
                          <div className="mt-1 ml-4 space-y-1">
                            {(medBody || s.error) && (
                              <div>
                                {expandedIds.has(expandKey) || s.error ? (
                                  <>
                                    <div
                                      className="text-[9px] font-semibold mb-0.5 uppercase tracking-wide"
                                      style={{ color: 'var(--text-muted)' }}
                                    >
                                      {s.error ? 'Error' : 'Result'}
                                    </div>
                                    <pre
                                      className="text-[10px] p-1.5 m-0 whitespace-pre-wrap break-words font-mono rounded"
                                      style={{
                                        color: s.error
                                          ? 'var(--error)'
                                          : 'var(--text-secondary)',
                                        background: 'var(--bg-primary)',
                                        border: '1px solid var(--border)',
                                        maxHeight: MED_BLOCK_MAX_H,
                                        overflow: 'auto',
                                        margin: 0,
                                      }}
                                    >
                                      {s.error
                                        ? s.error.slice(0, 1600)
                                        : medBody}
                                    </pre>
                                  </>
                                ) : null}
                                {!s.error && (s.resultText || s.argsText) && (
                                  <button
                                    type="button"
                                    className="mt-0.5 text-[10px] px-0"
                                    style={{
                                      background: 'none',
                                      border: 'none',
                                      color: 'var(--accent)',
                                      cursor: 'pointer',
                                    }}
                                    onClick={() => toggleExpand(expandKey)}
                                  >
                                    {expandedIds.has(expandKey)
                                      ? 'Show less'
                                      : 'Show result'}
                                  </button>
                                )}
                              </div>
                            )}
                          </div>
                        )}

                        {/* Full: complete dumps for this single step */}
                        {showFullBody && (
                          <div className="mt-1 ml-4 space-y-1">
                            {s.argsText && (() => {
                              const fmt = formattedArgs.get(s.id) || {
                                text: s.argsText || '',
                              }
                              return (
                                <div>
                                  <div
                                    className="text-[9px] font-semibold mb-0.5 uppercase tracking-wide"
                                    style={{ color: 'var(--text-muted)' }}
                                  >
                                    {fmt.caption || 'Args'}
                                  </div>
                                  <div
                                    className="rounded overflow-x-auto process-diff-wrap"
                                    style={{
                                      background: 'var(--bg-primary)',
                                      border: '1px solid var(--border)',
                                      maxHeight: FULL_BLOCK_MAX_H,
                                    }}
                                  >
                                    <DiffCode
                                      text={clipFull(fmt.text)}
                                      className={fmt.className}
                                      compact
                                    />
                                  </div>
                                </div>
                              )
                            })()}
                            {(s.resultText || s.error) && (() => {
                              const fmt = formatToolResultDisplay(
                                s.name,
                                s.resultText,
                              )
                              return (
                                <div>
                                  <div
                                    className="text-[9px] font-semibold mb-0.5 uppercase tracking-wide"
                                    style={{ color: 'var(--text-muted)' }}
                                  >
                                    {s.error ? 'Error' : 'Result'}
                                  </div>
                                  <div
                                    className="rounded overflow-x-auto process-diff-wrap"
                                    style={{
                                      background: 'var(--bg-primary)',
                                      border: '1px solid var(--border)',
                                      maxHeight: FULL_BLOCK_MAX_H,
                                    }}
                                  >
                                    {s.error ? (
                                      <pre
                                        className="text-[10px] p-1.5 m-0 whitespace-pre-wrap break-all font-mono"
                                        style={{
                                          color: 'var(--error)',
                                          margin: 0,
                                        }}
                                      >
                                        {clipFull(s.error)}
                                      </pre>
                                    ) : (
                                      <DiffCode
                                        text={clipFull(fmt.text)}
                                        className={fmt.className}
                                        compact
                                      />
                                    )}
                                  </div>
                                </div>
                              )
                            })()}
                            {s.status === 'running' && !s.resultText && !s.error && (
                              <div
                                className="text-[10px] italic"
                                style={{ color: 'var(--text-muted)' }}
                              >
                                Running…
                              </div>
                            )}
                          </div>
                        )}
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>
          </div>

          {showJump && (
            <button
              type="button"
              className="scroll-latest-fab process-jump"
              onClick={jumpLatest}
              title="Jump to latest process output"
              aria-label="Jump to latest process output"
            >
              ↓
            </button>
          )}
        </div>
      )}
    </div>
  )
}
