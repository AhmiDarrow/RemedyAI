import { useMemo, type CSSProperties } from 'react'
import { classifyDiffLine, shouldRenderAsDiff, type DiffLineKind } from '../utils/diffHighlight'

interface DiffCodeProps {
  text: string
  className?: string
  /** Compact style for tool process panels */
  compact?: boolean
}

/** Inline styles so theme CSS cannot wash out red/green (user-reported black dumps). */
const LINE_STYLE: Record<DiffLineKind, CSSProperties> = {
  add: {
    display: 'block',
    padding: '0 0.75rem',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    borderLeft: '3px solid #3fb950',
    background: 'rgba(63, 185, 80, 0.16)',
    color: '#3fb950',
  },
  del: {
    display: 'block',
    padding: '0 0.75rem',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    borderLeft: '3px solid #f85149',
    background: 'rgba(248, 81, 73, 0.14)',
    color: '#f85149',
  },
  hunk: {
    display: 'block',
    padding: '0 0.75rem',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    borderLeft: '3px solid var(--accent, #6c8cff)',
    background: 'rgba(108, 140, 255, 0.12)',
    color: 'var(--accent, #6c8cff)',
    fontWeight: 600,
  },
  meta: {
    display: 'block',
    padding: '0 0.75rem',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    borderLeft: '3px solid var(--border, #444)',
    color: 'var(--text-muted, #9a9ab0)',
    background: 'transparent',
  },
  ctx: {
    display: 'block',
    padding: '0 0.75rem',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    borderLeft: '3px solid transparent',
    color: 'var(--text-secondary, #c8c8d4)',
  },
}

/**
 * Unified-diff viewer: red removals, green additions, muted meta/hunk lines.
 * Falls back to plain pre when content is not a diff.
 */
export function DiffCode({ text, className, compact }: DiffCodeProps) {
  const asDiff = useMemo(() => shouldRenderAsDiff(text, className), [text, className])
  const lines = useMemo(() => text.replace(/\r\n/g, '\n').split('\n'), [text])

  if (!asDiff) {
    return (
      <pre
        className={compact ? 'diff-code-plain compact' : 'diff-code-plain'}
        style={{
          margin: 0,
          padding: compact ? '0.35rem 0.75rem' : '0.55rem 0.85rem',
          color: 'var(--text-secondary)',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        <code className={className || undefined} style={{ background: 'none', color: 'inherit' }}>
          {text}
        </code>
      </pre>
    )
  }

  return (
    <pre
      className={compact ? 'diff-code compact' : 'diff-code'}
      aria-label="diff"
      style={{
        margin: 0,
        padding: compact ? '0.25rem 0' : '0.4rem 0',
        fontSize: compact ? '0.65rem' : '0.78rem',
        lineHeight: 1.45,
        fontFamily:
          "'Cascadia Code', 'Fira Code', 'JetBrains Mono', ui-monospace, monospace",
      }}
    >
      <code className={className || 'language-diff'} style={{ background: 'none', display: 'block' }}>
        {lines.map((line, i) => {
          // Preserve trailing empty line from split only if original ended with \n
          if (i === lines.length - 1 && line === '' && !text.endsWith('\n')) {
            return null
          }
          const kind: DiffLineKind = classifyDiffLine(line)
          return (
            <span key={i} className={`diff-line diff-line-${kind}`} style={LINE_STYLE[kind]}>
              {line.length === 0 ? ' ' : line}
              {'\n'}
            </span>
          )
        })}
      </code>
    </pre>
  )
}
