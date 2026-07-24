import { useState, useEffect, useRef } from 'react'

interface PanelProps {
  open: boolean
  onClose: () => void
  title: string
  children: React.ReactNode
}

/** Side panel with basic focus trap + Escape to close (a11y). */
export function Panel({ open, onClose, title, children }: PanelProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const prevFocus = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!open) return
    prevFocus.current = document.activeElement as HTMLElement | null
    closeRef.current?.focus()

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
        return
      }
      if (e.key !== 'Tab' || !rootRef.current) return
      const focusables = rootRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      )
      const list = [...focusables].filter((el) => !el.hasAttribute('disabled') && el.offsetParent !== null)
      if (list.length === 0) return
      const first = list[0]!
      const last = list[list.length - 1]!
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      prevFocus.current?.focus?.()
    }
  }, [open, onClose])

  return (
    <div
      ref={rootRef}
      role="complementary"
      aria-label={title}
      aria-hidden={!open}
      className="flex flex-col border-l transition-all overflow-hidden"
      style={{
        width: open ? 280 : 0,
        minWidth: open ? 280 : 0,
        background: 'var(--bg-secondary)',
        borderColor: 'var(--border)',
        transition: 'width 0.2s ease, min-width 0.2s ease',
      }}
    >
      <div
        className="flex items-center justify-between px-3 py-2 border-b text-xs font-medium"
        style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
      >
        <span>{title}</span>
        <button
          ref={closeRef}
          onClick={onClose}
          className="px-1 rounded"
          style={{ color: 'var(--text-muted)' }}
          aria-label={`Close ${title}`}
        >
          {'\u00D7'}
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-2 text-xs">
        {children}
      </div>
    </div>
  )
}

export function MemoryPanel({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const [entries, setEntries] = useState<{ id: string; title: string; content: string; type: string }[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    fetch('/api/memory/search?query=&limit=20')
      .then((r) => r.json())
      .then((d) => setEntries(d.results || []))
      .catch(() => setEntries([]))
      .finally(() => setLoading(false))
  }, [open])

  return (
    <Panel open={open} onClose={onClose} title="Memory">
      {loading ? (
        <div style={{ color: 'var(--text-muted)' }}>Loading...</div>
      ) : entries.length === 0 ? (
        <div style={{ color: 'var(--text-muted)' }}>No entries</div>
      ) : (
        entries.map((e) => (
          <div
            key={e.id}
            className="mb-2 p-2 rounded"
            style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)' }}
          >
            <div className="font-medium truncate" style={{ color: 'var(--text-primary)' }}>
              {e.title}
            </div>
            <div className="mt-0.5" style={{ color: 'var(--text-secondary)' }}>
              {e.content.slice(0, 120)}
            </div>
            <div className="mt-1" style={{ color: 'var(--text-muted)', fontSize: '0.7rem' }}>
              {e.type}
            </div>
          </div>
        ))
      )}
    </Panel>
  )
}

export function SkillsPanel({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const [skills, setSkills] = useState<{ name: string; description: string; version: string }[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    fetch('/api/skills')
      .then((r) => r.json())
      .then((d) => setSkills(d || []))
      .catch(() => setSkills([]))
      .finally(() => setLoading(false))
  }, [open])

  return (
    <Panel open={open} onClose={onClose} title="Skills (agent packs)">
      {loading ? (
        <div style={{ color: 'var(--text-muted)' }}>Loading...</div>
      ) : skills.length === 0 ? (
        <div style={{ color: 'var(--text-muted)' }}>No skills loaded</div>
      ) : (
        skills.map((s) => (
          <div
            key={s.name}
            className="mb-2 p-2 rounded"
            style={{ background: 'var(--bg-tertiary)', border: '1px solid var(--border)' }}
          >
            <div className="font-medium" style={{ color: 'var(--accent)' }}>{s.name}</div>
            <div className="mt-0.5" style={{ color: 'var(--text-secondary)' }}>
              {s.description}
            </div>
            <div className="mt-1" style={{ color: 'var(--text-muted)', fontSize: '0.7rem' }}>
              v{s.version}
            </div>
          </div>
        ))
      )}
    </Panel>
  )
}
