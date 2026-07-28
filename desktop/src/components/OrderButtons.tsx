/** ↑ / ↓ controls for sidebar reorder — compact but easy to hit. */

interface OrderButtonsProps {
  onUp: () => void
  onDown: () => void
  disableUp?: boolean
  disableDown?: boolean
  /** Extra class on the wrapper */
  className?: string
  titleUp?: string
  titleDown?: string
}

function ArrowBtn({
  label,
  title,
  disabled,
  onClick,
  glyph,
}: {
  label: string
  title: string
  disabled?: boolean
  onClick: () => void
  glyph: string
}) {
  return (
    <button
      type="button"
      className="inline-flex items-center justify-center shrink-0 rounded transition-colors disabled:cursor-not-allowed"
      style={{
        width: 16,
        height: 16,
        fontSize: 12,
        lineHeight: 1,
        fontWeight: 600,
        padding: 0,
        color: disabled
          ? 'var(--text-muted)'
          : 'var(--accent)',
        background: 'transparent',
        border: 'none',
        opacity: disabled ? 0.3 : 0.85,
      }}
      title={title}
      aria-label={label}
      disabled={disabled}
      onClick={(e) => {
        e.stopPropagation()
        if (!disabled) onClick()
      }}
      onMouseEnter={(e) => {
        if (disabled) return
        e.currentTarget.style.background =
          'color-mix(in srgb, var(--accent) 18%, transparent)'
        e.currentTarget.style.opacity = '1'
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = 'transparent'
        e.currentTarget.style.opacity = disabled ? '0.3' : '0.85'
      }}
    >
      {glyph}
    </button>
  )
}

export function OrderButtons({
  onUp,
  onDown,
  disableUp,
  disableDown,
  className = '',
  titleUp = 'Move up',
  titleDown = 'Move down',
}: OrderButtonsProps) {
  return (
    <span
      className={`inline-flex items-center gap-px shrink-0 opacity-55 group-hover:opacity-100 group-hover/header:opacity-100 transition-opacity ${className}`}
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <ArrowBtn
        label={titleUp}
        title={titleUp}
        disabled={disableUp}
        onClick={onUp}
        glyph="↑"
      />
      <ArrowBtn
        label={titleDown}
        title={titleDown}
        disabled={disableDown}
        onClick={onDown}
        glyph="↓"
      />
    </span>
  )
}
