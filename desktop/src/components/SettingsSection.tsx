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
      className="rounded-lg overflow-hidden"
      style={{ border: '1px solid var(--border)' }}
      data-section={id}
      data-keywords={`${title} ${summary || ''} ${keywords}`.toLowerCase()}
    >
      <button
        type="button"
        onClick={toggle}
        className="w-full flex items-center gap-2 px-2.5 py-2 text-left transition-colors"
        style={{
          background: open ? 'var(--bg-tertiary)' : 'transparent',
          color: 'var(--text-primary)',
        }}
        aria-expanded={open}
      >
        <span
          className="inline-flex w-4 justify-center text-[10px] flex-shrink-0"
          style={{ color: 'var(--text-muted)' }}
          aria-hidden
        >
          {open ? '▼' : '▶'}
        </span>
        <span className="flex-1 min-w-0">
          <span className="block font-semibold text-xs">{title}</span>
          {!open && summary ? (
            <span
              className="block text-[10px] truncate mt-0.5"
              style={{ color: 'var(--text-muted)' }}
            >
              {summary}
            </span>
          ) : null}
        </span>
      </button>
      {open && (
        <div
          className="px-2.5 pb-3 pt-1 space-y-2 text-xs"
          style={{ borderTop: '1px solid var(--border)' }}
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
