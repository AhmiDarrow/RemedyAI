/** Shared empty / error / offline placeholder for panels and rails. */

export function EmptyState({
  title,
  description,
  actionLabel,
  onAction,
  tone = 'muted',
  compact = false,
}: {
  title: string
  description?: string
  actionLabel?: string
  onAction?: () => void
  tone?: 'muted' | 'error' | 'accent'
  compact?: boolean
}) {
  const color =
    tone === 'error'
      ? 'var(--error)'
      : tone === 'accent'
        ? 'var(--accent)'
        : 'var(--text-muted)'

  return (
    <div
      className={`ui-empty-state flex flex-col items-center justify-center text-center ${
        compact ? 'px-3 py-6 gap-1.5' : 'px-4 py-10 gap-2'
      }`}
      role="status"
    >
      <div
        className="text-sm font-semibold tracking-tight"
        style={{ color: tone === 'muted' ? 'var(--text-primary)' : color }}
      >
        {title}
      </div>
      {description ? (
        <div
          className="text-xs leading-relaxed max-w-[16rem]"
          style={{ color: 'var(--text-muted)' }}
        >
          {description}
        </div>
      ) : null}
      {actionLabel && onAction ? (
        <button
          type="button"
          className="ui-btn ui-btn-secondary mt-1"
          style={{ fontSize: '0.7rem', padding: '0.35rem 0.7rem' }}
          onClick={onAction}
        >
          {actionLabel}
        </button>
      ) : null}
    </div>
  )
}
