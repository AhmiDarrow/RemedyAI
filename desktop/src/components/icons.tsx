/** Shared SVG icons for compact, themed UI actions. */

type IconProps = {
  size?: number
  className?: string
}

const base = (size: number) => ({
  width: size,
  height: size,
  viewBox: '0 0 16 16',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.5,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true as const,
})

export function IconCopy({ size = 14 }: IconProps) {
  return (
    <svg {...base(size)}>
      <rect x="5.5" y="5.5" width="7" height="7" rx="1.2" />
      <path d="M3.5 10.5V3.5A1 1 0 0 1 4.5 2.5h7" />
    </svg>
  )
}

export function IconCheck({ size = 14 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M3.5 8.5 6.5 11.5 12.5 4.5" />
    </svg>
  )
}

export function IconEdit({ size = 14 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M11.5 2.5 13.5 4.5 6 12H4v-2l7.5-7.5z" />
      <path d="M10 4 12 6" />
    </svg>
  )
}

export function IconRefresh({ size = 14 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M13 8a5 5 0 1 1-1.2-3.3" />
      <path d="M13 3.5V6.5H10" />
    </svg>
  )
}

export function IconChevronDown({ size = 14 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M4 6.5 8 10.5 12 6.5" />
    </svg>
  )
}

export function IconChevronUp({ size = 14 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M4 9.5 8 5.5 12 9.5" />
    </svg>
  )
}

export function IconSend({ size = 14 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M2.5 8 13.5 2.5 9 13.5 7.5 9 2.5 8z" />
    </svg>
  )
}

export function IconStop({ size = 14 }: IconProps) {
  return (
    <svg {...base(size)}>
      <rect x="4" y="4" width="8" height="8" rx="1" fill="currentColor" stroke="none" />
    </svg>
  )
}

export function IconPaperclip({ size = 14 }: IconProps) {
  return (
    <svg {...base(size)}>
      <path d="M11 7.5 7.2 11.3a2.2 2.2 0 0 1-3.1-3.1L8.5 3.8a1.5 1.5 0 0 1 2.1 2.1L6.3 10.2" />
    </svg>
  )
}

export function IconBtn({
  title,
  onClick,
  children,
  active,
  muted,
}: {
  title: string
  onClick: () => void
  children: React.ReactNode
  active?: boolean
  muted?: boolean
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      onClick={onClick}
      className="inline-flex items-center justify-center rounded"
      style={{
        width: 22,
        height: 22,
        padding: 0,
        background: active ? 'color-mix(in srgb, var(--accent) 18%, transparent)' : 'transparent',
        color: muted
          ? 'var(--text-muted)'
          : active
            ? 'var(--accent)'
            : 'var(--text-secondary)',
        border: '1px solid transparent',
        cursor: 'pointer',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = 'var(--border)'
        e.currentTarget.style.background = 'var(--bg-tertiary)'
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = 'transparent'
        e.currentTarget.style.background = active
          ? 'color-mix(in srgb, var(--accent) 18%, transparent)'
          : 'transparent'
      }}
    >
      {children}
    </button>
  )
}
