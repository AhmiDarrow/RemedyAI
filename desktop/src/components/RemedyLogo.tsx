/**
 * Remedy circuit-R monogram icon (not the wordmark name logo).
 * Uses /icon.png — same art as the app/taskbar icon.
 */

interface RemedyLogoProps {
  size?: number
  className?: string
  /** Soft rounded tile behind the icon */
  framed?: boolean
  title?: string
}

/** Prefer the circuit-R icon; fall back to favicon if icon.png missing. */
const R_ICON_SRC = '/icon.png'

export function RemedyLogo({
  size = 28,
  className = '',
  framed = false,
  title = 'Remedy',
}: RemedyLogoProps) {
  const img = (
    <img
      src={R_ICON_SRC}
      alt={title}
      width={size}
      height={size}
      draggable={false}
      className={className}
      style={{
        width: size,
        height: size,
        objectFit: 'contain',
        display: 'block',
      }}
      onError={(e) => {
        // favicon.png is also the R monogram in public/
        const el = e.currentTarget
        if (!el.src.endsWith('favicon.png')) el.src = '/favicon.png'
      }}
    />
  )

  if (!framed) return img

  return (
    <div
      className="flex items-center justify-center flex-shrink-0 rounded-2xl overflow-hidden"
      style={{
        width: size + 16,
        height: size + 16,
        background:
          'linear-gradient(145deg, color-mix(in srgb, var(--accent) 22%, var(--bg-tertiary)), var(--bg-tertiary))',
        border: '1px solid var(--border)',
        boxShadow: '0 8px 24px color-mix(in srgb, var(--accent) 28%, transparent)',
      }}
      aria-hidden
    >
      {img}
    </div>
  )
}
