import { useState } from 'react'
import type { ProcessStep, ToolProcessMode } from '../utils/toolLabels'
import { IconBtn, IconCheck, IconChevronDown, IconChevronUp, IconCopy } from './icons'
import { useStickToBottom } from '../hooks/useStickToBottom'

interface ProcessTraceProps {
  mode: ToolProcessMode
  steps: ProcessStep[]
  /** Live turn (expanded by default when streaming) */
  live?: boolean
  /** After turn: start collapsed */
  defaultCollapsed?: boolean
}

const MEDIUM_PREVIEW = 400

function previewText(text: string | undefined, mode: ToolProcessMode): string {
  if (!text) return ''
  if (mode === 'full') return text
  if (text.length <= MEDIUM_PREVIEW) return text
  return `${text.slice(0, MEDIUM_PREVIEW)}…`
}

/**
 * Provider process timeline with stick-to-bottom inside the frame
 * (thinking/tools/raw dumps follow unless user scrolls up).
 */
export function ProcessTrace({
  mode,
  steps,
  live = false,
  defaultCollapsed = false,
}: ProcessTraceProps) {
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

  if (mode === 'off' || steps.length === 0) return null

  const running = steps.filter((s) => s.status === 'running').length
  const done = steps.filter((s) => s.status === 'done').length
  const failed = steps.filter((s) => s.status === 'error').length
  const summary =
    running > 0
      ? `${running} running · ${done} done`
      : failed
        ? `${done} done · ${failed} error`
        : `${done} step${done === 1 ? '' : 's'}`

  const allOpen = mode === 'full'

  const toggleStep = (id: string) => {
    if (allOpen) return
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
      className="rounded-md overflow-hidden text-[11px] my-1 relative"
      style={{
        border: '1px solid var(--border)',
        background: 'var(--bg-primary)',
        maxWidth: mode === 'full' ? '100%' : 'min(var(--chat-max-width), 100%)',
      }}
    >
      <button
        type="button"
        className="w-full flex items-center justify-between gap-2 px-2 py-1 text-left"
        style={{ color: 'var(--text-muted)', background: 'var(--bg-tertiary)' }}
        onClick={() => {
          setCollapsed((c) => {
            const next = !c
            // Expand → resume follow on next paint via followActive
            return next
          })
        }}
        aria-expanded={!collapsed}
      >
        <span className="font-semibold tracking-wide" style={{ color: 'var(--text-secondary)' }}>
          Process
          <span className="font-normal ml-1.5" style={{ color: 'var(--text-muted)' }}>
            {summary}
            {mode === 'full' ? ' · full raw' : mode === 'medium' ? ' · medium' : ''}
          </span>
        </span>
        {collapsed ? <IconChevronDown size={12} /> : <IconChevronUp size={12} />}
      </button>

      {!collapsed && (
        <div className="relative">
          <ul
            ref={setScroller}
            className="px-2 py-1.5 overflow-y-auto"
            style={{ maxHeight: mode === 'full' ? 'min(70vh, 40rem)' : '20rem' }}
          >
            <div ref={setContent} className="space-y-2">
              {steps.map((s) => {
                const detailOpen =
                  allOpen
                  || openIds.has(s.id)
                  || (live && s.status === 'running')
                const hasDetail = Boolean(s.argsText || s.resultText || s.error)
                const showArgs = (mode === 'full' || mode === 'medium') && s.argsText
                const argsShown =
                  mode === 'full'
                    ? showArgs
                    : showArgs && (s.argsText?.length || 0) <= 800
                const showResult =
                  (mode === 'medium' || mode === 'full') && (s.resultText || s.error)
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
                    className="rounded px-1.5 py-1"
                    style={{ background: 'var(--bg-secondary)' }}
                  >
                    <div className="flex items-start gap-1.5">
                      <button
                        type="button"
                        className="flex-1 flex items-start gap-1.5 text-left min-w-0"
                        onClick={() => hasDetail && toggleStep(s.id)}
                        disabled={!hasDetail || allOpen}
                        style={{
                          background: 'transparent',
                          border: 'none',
                          color: 'var(--text-primary)',
                          cursor: hasDetail && !allOpen ? 'pointer' : 'default',
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
                          <span
                            className="ml-1.5 font-mono text-[10px]"
                            style={{ color: 'var(--text-muted)' }}
                          >
                            {s.name}
                          </span>
                          {s.endedAt && s.startedAt && s.status !== 'running' && (
                            <span className="ml-1.5" style={{ color: 'var(--text-muted)' }}>
                              {Math.max(0, (s.endedAt - s.startedAt) / 1000).toFixed(1)}s
                            </span>
                          )}
                          {mode === 'full' && s.resultText && (
                            <span className="ml-1.5" style={{ color: 'var(--text-muted)' }}>
                              {s.resultText.length.toLocaleString()} chars
                            </span>
                          )}
                        </span>
                      </button>
                      {mode === 'full' && rawDump && (
                        <IconBtn
                          title={copiedId === s.id ? 'Copied' : 'Copy full raw'}
                          onClick={() => void copyBlock(s.id, rawDump)}
                          active={copiedId === s.id}
                        >
                          {copiedId === s.id ? <IconCheck size={12} /> : <IconCopy size={12} />}
                        </IconBtn>
                      )}
                    </div>

                    {detailOpen && (argsShown || showResult) && (
                      <div className="mt-1 ml-4 space-y-1">
                        {argsShown && (
                          <div>
                            <div
                              className="text-[9px] font-semibold mb-0.5 uppercase tracking-wide"
                              style={{ color: 'var(--text-muted)' }}
                            >
                              Args / code
                            </div>
                            <pre
                              className="text-[10px] p-1.5 rounded overflow-x-auto whitespace-pre-wrap break-all font-mono"
                              style={{
                                background: 'var(--bg-primary)',
                                border: '1px solid var(--border)',
                                color: 'var(--text-secondary)',
                                margin: 0,
                                maxHeight: mode === 'full' ? 'none' : '12rem',
                              }}
                            >
                              {previewText(s.argsText, mode)}
                            </pre>
                          </div>
                        )}
                        {showResult && (
                          <div>
                            <div
                              className="text-[9px] font-semibold mb-0.5 uppercase tracking-wide"
                              style={{ color: 'var(--text-muted)' }}
                            >
                              {s.error ? 'Error' : 'Result / stdout'}
                            </div>
                            <pre
                              className="text-[10px] p-1.5 rounded overflow-x-auto whitespace-pre-wrap break-all font-mono"
                              style={{
                                background: 'var(--bg-primary)',
                                border: '1px solid var(--border)',
                                color: s.error ? 'var(--error)' : 'var(--text-secondary)',
                                margin: 0,
                                maxHeight: mode === 'full' ? 'none' : '12rem',
                              }}
                            >
                              {previewText(s.error || s.resultText, mode)}
                            </pre>
                          </div>
                        )}
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
