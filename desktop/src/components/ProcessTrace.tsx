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

/** Shared list viewport height at every depth (scroll inside, same chrome). */
const LIST_MAX_H = 'min(48vh, 28rem)'
/** Full-mode dump clip inside a step (panel still scrolls). */
const FULL_BLOCK_MAX_H = '16rem'
/** Med preview body height */
const MED_BLOCK_MAX_H = '7.5rem'
const FULL_PREVIEW_CHARS = 120_000
const MED_BODY_CHARS = 480

function clipFull(text: string | undefined): string {
  if (!text) return ''
  if (text.length <= FULL_PREVIEW_CHARS) return text
  return `${text.slice(0, FULL_PREVIEW_CHARS)}…`
}

/**
 * Tool process trail — one chrome for Min / Med / Full.
 *
 * - Min:  step labels + status
 * - Med:  labels + always-visible path/cmd/result summary
 * - Full: same rows + full args/results open; follows growth while live
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
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set())
  const [copiedId, setCopiedId] = useState<string | null>(null)

  // Re-bind panel open/closed when Min/Med/Full changes (historical traces)
  useEffect(() => {
    if (live) {
      setCollapsed(false)
      return
    }
    setCollapsed(defaultCollapsed)
  }, [mode, live, defaultCollapsed])

  const stepSig = steps
    .map(
      (s) =>
        `${s.id}:${s.status}:${(s.resultText || '').length}:${(s.argsText || '').length}`,
    )
    .join('|')

  // Follow while live and panel open — rebinds when scroller mounts
  const follow = live && !collapsed
  const { setScroller, setContent, showJump, jumpLatest } = useStickToBottom({
    followActive: follow,
    alwaysOfferJump: follow || full,
    deps: [stepSig, mode, collapsed, full],
  })

  const formattedArgs = useMemo(() => {
    const prior = new Map<string, string>()
    const map = new Map<string, ReturnType<typeof formatToolArgsDisplay>>()
    for (const s of steps) {
      map.set(s.id, formatToolArgsDisplay(s.name, s.argsText, prior))
    }
    return map
  }, [steps, stepSig])

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
    // Med: expand shows a bit more of the same preview; Full is always open
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

  return (
    <div
      className="process-trace rounded-lg overflow-hidden text-[11px] my-1 relative w-full"
      style={{
        border: '1px solid var(--border)',
        background: 'var(--bg-primary)',
        maxWidth: 'min(var(--chat-max-width), 100%)',
      }}
      data-process-mode={mode}
    >
      <button
        type="button"
        className="w-full flex items-center justify-between gap-2 px-2.5 py-1.5 text-left"
        style={{ color: 'var(--text-muted)', background: 'var(--bg-tertiary)' }}
        onClick={() => setCollapsed((c) => !c)}
        aria-expanded={!collapsed}
      >
        <span className="font-semibold tracking-wide" style={{ color: 'var(--text-secondary)' }}>
          Process
          <span className="font-normal ml-1.5" style={{ color: 'var(--text-muted)' }}>
            {summary}
          </span>
        </span>
        {collapsed ? <IconChevronDown size={12} /> : <IconChevronUp size={12} />}
      </button>

      {!collapsed && (
        <div className="relative">
          <ul
            ref={setScroller}
            className="px-2 py-1.5 overflow-y-auto"
            style={{ maxHeight: LIST_MAX_H }}
          >
            <div ref={setContent} className="space-y-1">
              {steps.map((s) => {
                const inline = stepInlineSummary(
                  s.name,
                  s.argsText,
                  s.resultText,
                  s.error,
                )
                const medBody = !full
                  ? stepMediumPreview(s.resultText, s.error, {
                      maxLines: expandedIds.has(s.id) ? 12 : 4,
                      maxChars: expandedIds.has(s.id) ? 1600 : MED_BODY_CHARS,
                    })
                  : ''
                const showMedBody = med && Boolean(medBody || s.argsText)
                const showFullBody =
                  full && Boolean(s.argsText || s.resultText || s.error)
                const statusIcon =
                  s.status === 'running' ? '…' : s.status === 'error' ? '!' : '✓'
                const statusColor =
                  s.status === 'running'
                    ? 'var(--accent)'
                    : s.status === 'error'
                      ? 'var(--error)'
                      : 'var(--success)'

                const rawDump = [
                  `// ${s.label} (${s.name})`,
                  s.argsText ? `// --- args ---\n${s.argsText}` : '',
                  s.error
                    ? `// --- error ---\n${s.error}`
                    : s.resultText
                      ? `// --- result ---\n${s.resultText}`
                      : '',
                ]
                  .filter(Boolean)
                  .join('\n\n')

                return (
                  <li
                    key={s.id}
                    className="rounded-md px-1.5 py-1"
                    style={{ background: 'var(--bg-secondary)' }}
                  >
                    <div className="flex items-start gap-1.5">
                      <div className="flex-1 flex items-start gap-1.5 min-w-0">
                        <span
                          className="flex-shrink-0 font-mono w-3 text-center pt-0.5"
                          style={{ color: statusColor }}
                        >
                          {statusIcon}
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-baseline gap-x-1.5">
                            <span className="font-medium" style={{ color: 'var(--text-primary)' }}>
                              {s.label}
                            </span>
                            {!min && (
                              <span
                                className="font-mono text-[10px]"
                                style={{ color: 'var(--text-muted)' }}
                              >
                                {s.name}
                              </span>
                            )}
                            {s.endedAt && s.startedAt && s.status !== 'running' && (
                              <span style={{ color: 'var(--text-muted)' }}>
                                {Math.max(0, (s.endedAt - s.startedAt) / 1000).toFixed(1)}s
                              </span>
                            )}
                          </div>
                          {/* Med+: always-visible one-liner (path, command, first result) */}
                          {!min && inline && (
                            <div
                              className="mt-0.5 font-mono text-[10px] truncate"
                              style={{ color: 'var(--text-secondary)' }}
                              title={inline}
                            >
                              {inline}
                            </div>
                          )}
                        </div>
                      </div>
                      {!min && rawDump && (
                        <IconBtn
                          title={copiedId === s.id ? 'Copied' : 'Copy'}
                          onClick={() => void copyBlock(s.id, rawDump)}
                          active={copiedId === s.id}
                        >
                          {copiedId === s.id ? (
                            <IconCheck size={12} />
                          ) : (
                            <IconCopy size={12} />
                          )}
                        </IconBtn>
                      )}
                    </div>

                    {/* Med: compact result always shown when present */}
                    {showMedBody && (
                      <div className="mt-1 ml-4 space-y-1">
                        {s.argsText && (() => {
                          const fmt = formattedArgs.get(s.id)
                          const cap = fmt?.caption
                          if (!cap && !expandedIds.has(s.id)) return null
                          // Show caption (e.g. Edit · path) always; body on expand or short args
                          const shortArgs =
                            !fmt?.className
                            && (fmt?.text?.length || 0) > 0
                            && (fmt?.text?.length || 0) < 200
                          if (!cap && !shortArgs && !expandedIds.has(s.id)) return null
                          return (
                            <div>
                              {cap && (
                                <div
                                  className="text-[9px] font-semibold mb-0.5"
                                  style={{ color: 'var(--text-muted)' }}
                                >
                                  {cap}
                                </div>
                              )}
                              {(shortArgs || expandedIds.has(s.id)) && fmt?.text && (
                                <div
                                  className="rounded overflow-x-auto"
                                  style={{
                                    background: 'var(--bg-primary)',
                                    border: '1px solid var(--border)',
                                    maxHeight: MED_BLOCK_MAX_H,
                                  }}
                                >
                                  <DiffCode
                                    text={
                                      expandedIds.has(s.id)
                                        ? fmt.text.slice(0, 2400)
                                        : fmt.text.slice(0, MED_BODY_CHARS)
                                    }
                                    className={fmt.className}
                                    compact
                                  />
                                </div>
                              )}
                            </div>
                          )
                        })()}
                        {medBody && (
                          <div>
                            <div
                              className="text-[9px] font-semibold mb-0.5 uppercase tracking-wide"
                              style={{ color: 'var(--text-muted)' }}
                            >
                              {s.error ? 'Error' : 'Result'}
                            </div>
                            <pre
                              className="text-[10px] p-1.5 m-0 whitespace-pre-wrap break-words font-mono rounded"
                              style={{
                                color: s.error ? 'var(--error)' : 'var(--text-secondary)',
                                background: 'var(--bg-primary)',
                                border: '1px solid var(--border)',
                                maxHeight: MED_BLOCK_MAX_H,
                                overflow: 'auto',
                                margin: 0,
                              }}
                            >
                              {medBody}
                            </pre>
                            {(s.resultText || s.error || s.argsText) && (
                              <button
                                type="button"
                                className="mt-0.5 text-[10px] px-0"
                                style={{
                                  background: 'none',
                                  border: 'none',
                                  color: 'var(--accent)',
                                  cursor: 'pointer',
                                }}
                                onClick={() => toggleExpand(s.id)}
                              >
                                {expandedIds.has(s.id) ? 'Show less' : 'Show more'}
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Full: complete dumps, always open */}
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
                          const fmt = formatToolResultDisplay(s.name, s.resultText)
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
                                    style={{ color: 'var(--error)', margin: 0 }}
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
                      </div>
                    )}
                  </li>
                )
              })}
            </div>
          </ul>

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
