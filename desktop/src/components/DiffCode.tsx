import { useMemo } from 'react'
import { classifyDiffLine, shouldRenderAsDiff, type DiffLineKind } from '../utils/diffHighlight'

interface DiffCodeProps {
  text: string
  className?: string
  /** Compact style for tool process panels */
  compact?: boolean
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
      <pre className={compact ? 'diff-code-plain compact' : 'diff-code-plain'}>
        <code className={className || undefined}>{text}</code>
      </pre>
    )
  }

  return (
    <pre
      className={compact ? 'diff-code compact' : 'diff-code'}
      aria-label="diff"
    >
      <code className={className || 'language-diff'}>
        {lines.map((line, i) => {
          // Preserve trailing empty line from split only if original ended with \n
          if (i === lines.length - 1 && line === '' && !text.endsWith('\n')) {
            return null
          }
          const kind: DiffLineKind = classifyDiffLine(line)
          return (
            <span key={i} className={`diff-line diff-line-${kind}`}>
              {line.length === 0 ? ' ' : line}
              {'\n'}
            </span>
          )
        })}
      </code>
    </pre>
  )
}
