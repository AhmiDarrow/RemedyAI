import { useEffect, useState, type ReactNode } from 'react'

interface SettingsSectionProps {
  id: string
  title: string
  /** Short hint when collapsed */
  summary?: string
  /** Extra keywords for Settings search */
  keywords?: string
  defaultOpen?: boolean
  /** Controlled expand (search / deep-link) */
  forceOpen?: boolean
  /** Hide when search does not match */
  hidden?: boolean
  /** Notify parent when user expands (lazy load hooks) */
  onOpenChange?: (open: boolean) => void
  children: ReactNode
}

/** Clickable category header — expand/collapse to reduce Settings clutter. */
export function SettingsSection({
  id,
  title,
  summary,
  keywords = '',
  defaultOpen = false,
  forceOpen,
  hidden = false,
  onOpenChange,
  children,
}: SettingsSectionProps) {
  const [open, setOpen] = useState(defaultOpen)

  useEffect(() => {
    if (forceOpen === true) {
      setOpen(true)
      onOpenChange?.(true)
    }
  }, [forceOpen, onOpenChange])

  if (hidden) return null

  const toggle = () => {
    setOpen((o) => {
      const next = !o
      onOpenChange?.(next)
      return next
    })
  }

  return (
    <section
      className="rounded-xl overflow-hidden"
      style={{
        border: '1px solid color-mix(in srgb, var(--border) 88%, transparent)',
        background: open
          ? 'color-mix(in srgb, var(--bg-secondary) 70%, transparent)'
          : 'transparent',
      }}
      data-section={id}
      data-keywords={`${title} ${summary || ''} ${keywords}`.toLowerCase()}
    >
      <button
        type="button"
        onClick={toggle}
        className="w-full flex items-center gap-2 px-3 py-2.5 text-left transition-colors"
        style={{
          background: open
            ? 'color-mix(in srgb, var(--accent) 6%, var(--bg-tertiary))'
            : 'transparent',
          color: 'var(--text-primary)',
        }}
        aria-expanded={open}
      >
        <span
          className="inline-flex w-4 justify-center text-[10px] flex-shrink-0"
          style={{ color: open ? 'var(--accent)' : 'var(--text-muted)' }}
          aria-hidden
        >
          {open ? '▾' : '▸'}
        </span>
        <span className="flex-1 min-w-0">
          <span className="block font-semibold text-xs tracking-tight">{title}</span>
          {!open && summary ? (
            <span
              className="block text-[10px] truncate mt-0.5 leading-snug"
              style={{ color: 'var(--text-muted)' }}
            >
              {summary}
            </span>
          ) : null}
        </span>
      </button>
      {open && (
        <div
          className="px-3 pb-3.5 pt-1.5 space-y-2 text-xs"
          style={{
            borderTop: '1px solid color-mix(in srgb, var(--border) 80%, transparent)',
          }}
        >
          {children}
        </div>
      )}
    </section>
  )
}

/** Match section against search query (title/summary/keywords). */
export function sectionMatchesSearch(
  query: string,
  title: string,
  summary?: string,
  keywords?: string,
): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  const hay = `${title} ${summary || ''} ${keywords || ''}`.toLowerCase()
  return q.split(/\s+/).every((tok) => hay.includes(tok))
}
