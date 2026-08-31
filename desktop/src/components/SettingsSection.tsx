import { useEffect, useRef, useState, type ReactNode } from 'react'

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
  // Track the last force directive we actually applied. A persistent
  // `forceOpen === true` must NOT re-open a section the user just collapsed:
  // the old effect depended on `onOpenChange` (a fresh function every parent
  // render), so any re-render force-reopened the section.
  const lastForce = useRef<boolean | undefined>(undefined)

  useEffect(() => {
    if (forceOpen === lastForce.current) return
    lastForce.current = forceOpen
    if (forceOpen === true) {
      setOpen(true)
      onOpenChange?.(true)
    }
    // Intentionally NOT depending on onOpenChange — its identity changes on
    // every parent render and would re-run this effect (reopening sections).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [forceOpen])

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
      className={`settings-section${open ? ' is-open' : ''}`}
      data-section={id}
      data-keywords={`${title} ${summary || ''} ${keywords}`.toLowerCase()}
    >
      <button
        type="button"
        onClick={toggle}
        className="settings-section-head w-full flex items-center gap-2 px-3 py-2 text-left"
        aria-expanded={open}
      >
        <span className="settings-section-chevron" aria-hidden>
          {open ? '▾' : '▸'}
        </span>
        <span className="flex-1 min-w-0">
          <span className="settings-section-title">{title}</span>
          {!open && summary ? (
            <span className="settings-section-summary">{summary}</span>
          ) : null}
        </span>
      </button>
      {open && (
        <div className="settings-section-body px-3 pb-3 pt-1.5 space-y-2 text-xs">
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
