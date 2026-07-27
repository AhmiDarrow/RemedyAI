import { useCallback } from 'react'
import { dismissLibrarySuggest, type LibrarySuggest } from '../api/skillsLibrary'

/** Soft one-line tip: library pack may help — Install via Skills (never auto-install). */
export function LibrarySuggestChip({
  suggestion,
  sessionId,
  onOpenLibrary,
  onDismiss,
}: {
  suggestion: LibrarySuggest | null
  sessionId?: string | null
  onOpenLibrary?: (skillId: string, name: string) => void
  onDismiss?: () => void
}) {
  const dismiss = useCallback(async () => {
    if (!suggestion?.id) {
      onDismiss?.()
      return
    }
    try {
      await dismissLibrarySuggest(suggestion.id, sessionId || undefined)
    } catch {
      /* still clear UI */
    }
    onDismiss?.()
  }, [suggestion, sessionId, onDismiss])

  if (!suggestion?.id || !suggestion.name) return null

  const desc = (suggestion.description || '').trim()
  const short = desc.length > 90 ? desc.slice(0, 87) + '…' : desc

  return (
    <div
      className="flex items-center gap-2 px-3 py-1.5 mx-2 mb-1 text-xs rounded-lg border"
      style={{
        background: 'var(--surface-2, var(--bg-elevated, #1a1a22))',
        borderColor: 'var(--border, #333)',
        color: 'var(--text, #e8e8ef)',
      }}
      role="status"
    >
      <span style={{ opacity: 0.85, flex: 1, minWidth: 0 }}>
        <strong style={{ color: 'var(--accent, #7c9cff)' }}>Library</strong>
        {' · '}
        <span className="font-medium">{suggestion.name}</span>
        {short ? (
          <span style={{ opacity: 0.75 }}> — {short}</span>
        ) : null}
      </span>
      <button
        type="button"
        className="shrink-0 px-2 py-0.5 rounded font-semibold"
        style={{ background: 'var(--accent, #7c9cff)', color: '#fff' }}
        onClick={() => onOpenLibrary?.(suggestion.id, suggestion.name)}
      >
        Open Library
      </button>
      <button
        type="button"
        className="shrink-0 px-1.5 py-0.5 rounded opacity-70 hover:opacity-100"
        aria-label="Dismiss library suggestion"
        onClick={() => void dismiss()}
      >
        ×
      </button>
    </div>
  )
}
