import { useMemo, useState } from 'react'
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
} from '../utils/toolProcessFormat'

interface ProcessTraceProps {
  mode: ToolProcessMode
  steps: ProcessStep[]
  /** Live turn */
  live?: boolean
  /** Start with the Process panel collapsed */
  defaultCollapsed?: boolean
}

/** Shared list viewport — same chrome at every depth level. */
const LIST_MAX_H = 'min(40vh, 22rem)'
/** Medium (and min expand) preview cap; full/full+ never truncate. */
const PREVIEW_CHARS = 720

function depthAllowsDetail(mode: ToolProcessMode): boolean {
  return mode !== 'off'
}

function depthShowsFull(mode: ToolProcessMode): boolean {
  return isFullProcessMode(mode)
}

function clipBody(text: string | undefined, mode: ToolProcessMode): string {
  if (!text) return ''
  if (depthShowsFull(mode)) return text
  if (text.length <= PREVIEW_CHARS) return text
  return `${text.slice(0, PREVIEW_CHARS)}…`
}

/**
 * Tool process trail — one shared layout for Min / Med / Full / Full+.
 * Modes only control how much detail is available, not a different UI.
 */
export function ProcessTrace({
  mode,
  steps,
  live = false,
  defaultCollapsed = false,
}: ProcessTraceProps) {
  const full = depthShowsFull(mode)
  const allowDetail = depthAllowsDetail(mode)
  const [collapsed, setCollapsed] = useState(defaultCollapsed && !live)
  const [openIds, setOpenIds] = useState<Set<string>>(() => new Set())
  const [copiedId, setCopiedId] = useState<string | null>(null)

  const stepSig = steps
    .map(
      (s) =>
        `${s.id}:${s.status}:${(s.resultText || '').length}:${(s.argsText || '').length}`,
    )
    .join('|')

  const follow = live && !collapsed
  const { setScroller, setContent, showJump, jumpLatest } = useStickToBottom({
    followActive: follow,
    alwaysOfferJump: follow,
    deps: [stepSig, mode, collapsed],
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

  const toggleStep = (id: string) => {
    if (!allowDetail || full) return
    setOpenIds((prev) => {
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
                const hasDetail =
                  allowDetail && Boolean(s.argsText || s.resultText || s.error)
                const detailOpen =
                  hasDetail
                  && (full
                    || openIds.has(s.id)
                    || (live && s.status === 'running' && allowDetail))
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
                      <button
                        type="button"
                        className="flex-1 flex items-start gap-1.5 text-left min-w-0"
                        onClick={() => hasDetail && toggleStep(s.id)}
                        disabled={!hasDetail || full}
                        style={{
                          background: 'transparent',
                          border: 'none',
                          color: 'var(--text-primary)',
                          cursor: hasDetail && !full ? 'pointer' : 'default',
                        }}
                      >
                        <span
                          className="flex-shrink-0 font-mono w-3 text-center"
                          style={{ color: statusColor }}
                        >
                          {statusIcon}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="font-medium">{s.label}</span>
                          {allowDetail && (
                            <span
                              className="ml-1.5 font-mono text-[10px]"
                              style={{ color: 'var(--text-muted)' }}
                            >
                              {s.name}
                            </span>
                          )}
                          {s.endedAt && s.startedAt && s.status !== 'running' && (
                            <span className="ml-1.5" style={{ color: 'var(--text-muted)' }}>
                              {Math.max(0, (s.endedAt - s.startedAt) / 1000).toFixed(1)}s
                            </span>
                          )}
                          {hasDetail && !full && !detailOpen && (
                            <span className="ml-1.5" style={{ color: 'var(--text-muted)' }}>
                              · details
                            </span>
                          )}
                        </span>
                      </button>
                      {allowDetail && rawDump && (
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

                    {detailOpen && (
                      <div className="mt-1 ml-4 space-y-1">
                        {s.argsText && (() => {
                          const fmt = formattedArgs.get(s.id) || {
                            text: s.argsText || '',
                          }
                          const body = clipBody(fmt.text, mode)
                          const truncated =
                            !full && (fmt.text?.length || 0) > PREVIEW_CHARS
                          return (
                            <div>
                              <div
                                className="text-[9px] font-semibold mb-0.5 uppercase tracking-wide"
                                style={{ color: 'var(--text-muted)' }}
                              >
                                {fmt.caption || 'Args'}
                                {truncated ? ' · more available in Full' : ''}
                              </div>
                              <div
                                className="rounded overflow-x-auto process-diff-wrap"
                                style={{
                                  background: 'var(--bg-primary)',
                                  border: '1px solid var(--border)',
                                  maxHeight: '12rem',
                                }}
                              >
                                <DiffCode
                                  text={body}
                                  className={fmt.className}
                                  compact
                                />
                              </div>
                            </div>
                          )
                        })()}
                        {(s.resultText || s.error) && (() => {
                          const fmt = formatToolResultDisplay(s.name, s.resultText)
                          const truncated =
                            !full
                            && !s.error
                            && (fmt.text?.length || 0) > PREVIEW_CHARS
                          return (
                            <div>
                              <div
                                className="text-[9px] font-semibold mb-0.5 uppercase tracking-wide"
                                style={{ color: 'var(--text-muted)' }}
                              >
                                {s.error ? 'Error' : 'Result'}
                                {truncated ? ' · more available in Full' : ''}
                              </div>
                              <div
                                className="rounded overflow-x-auto process-diff-wrap"
                                style={{
                                  background: 'var(--bg-primary)',
                                  border: '1px solid var(--border)',
                                  maxHeight: '12rem',
                                }}
                              >
                                {s.error ? (
                                  <pre
                                    className="text-[10px] p-1.5 m-0 whitespace-pre-wrap break-all font-mono"
                                    style={{ color: 'var(--error)', margin: 0 }}
                                  >
                                    {clipBody(s.error, mode)}
                                  </pre>
                                ) : (
                                  <DiffCode
                                    text={clipBody(fmt.text, mode)}
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
