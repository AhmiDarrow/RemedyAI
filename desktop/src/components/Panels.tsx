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

type SkillRow = {
  name: string
  description: string
  version: string
  status?: string
  tags?: string[]
  effort_weight?: number
  effort_band?: string | null
  auto_generated?: boolean
  quarantine?: boolean
  success_rate?: number | null
  related?: string[]
  lifecycle?: string | null
}

function statusColor(status?: string): string {
  switch ((status || '').toLowerCase()) {
    case 'active':
      return 'var(--success, #3ecf8e)'
    case 'validated':
      return 'var(--accent)'
    case 'discovered':
      return 'var(--warning, #e6b84d)'
    case 'disabled':
    case 'deprecated':
      return 'var(--text-muted)'
    default:
      return 'var(--text-secondary)'
  }
}

export function SkillsPanel({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const [skills, setSkills] = useState<SkillRow[]>([])
  const [loading, setLoading] = useState(false)
  const [filter, setFilter] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = () => {
    setLoading(true)
    setError(null)
    const q = filter.trim() ? `?q=${encodeURIComponent(filter.trim())}` : ''
    fetch(`/api/skills${q}`)
      .then((r) => r.json())
      .then((d) => setSkills(Array.isArray(d) ? d : []))
      .catch(() => {
        setSkills([])
        setError('Failed to load skills')
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (!open) return
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const setStatus = async (name: string, status: string) => {
    setBusy(name)
    setError(null)
    try {
      const r = await fetch(`/api/skills/${encodeURIComponent(name)}/status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      })
      if (!r.ok) throw new Error(await r.text())
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Status update failed')
    } finally {
      setBusy(null)
    }
  }

  const feedback = async (name: string, success: boolean) => {
    setBusy(name)
    try {
      await fetch(`/api/skills/${encodeURIComponent(name)}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ success }),
      })
      await load()
    } catch {
      /* ignore */
    } finally {
      setBusy(null)
    }
  }

  return (
    <Panel open={open} onClose={onClose} title="Skills (agent packs)">
      <div className="mb-2 flex gap-1 items-center">
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && load()}
          placeholder="Search skills…"
          className="flex-1 text-xs px-2 py-1 rounded"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
          }}
        />
        <button
          type="button"
          onClick={load}
          className="text-xs px-2 py-1 rounded"
          style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
        >
          Search
        </button>
      </div>
      <p className="mb-2" style={{ color: 'var(--text-muted)', fontSize: '0.7rem' }}>
        Learned skills start on probation; hard-won ones are protected. Activate full
        instructions in chat with the skill_activate tool.
      </p>
      {error && (
        <div className="mb-2 text-xs" style={{ color: 'var(--danger, #f66)' }}>
          {error}
        </div>
      )}
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
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-medium" style={{ color: 'var(--accent)' }}>
                {s.name}
              </span>
              <span
                className="text-xs px-1.5 rounded"
                style={{
                  color: statusColor(s.status),
                  border: `1px solid ${statusColor(s.status)}`,
                  fontSize: '0.65rem',
                }}
              >
                {s.status || 'unknown'}
              </span>
              {(s.effort_weight ?? 0) >= 0.62 && (
                <span
                  className="text-xs px-1.5 rounded"
                  style={{
                    color: 'var(--warning, #e6b84d)',
                    border: '1px solid var(--warning, #e6b84d)',
                    fontSize: '0.65rem',
                  }}
                >
                  hard-won
                </span>
              )}
              {s.auto_generated && (
                <span style={{ color: 'var(--text-muted)', fontSize: '0.65rem' }}>learned</span>
              )}
              {s.quarantine && (
                <span style={{ color: 'var(--danger, #f66)', fontSize: '0.65rem' }}>
                  quarantine
                </span>
              )}
            </div>
            <div className="mt-0.5" style={{ color: 'var(--text-secondary)', fontSize: '0.8rem' }}>
              {s.description}
            </div>
            <div className="mt-1 flex flex-wrap gap-2" style={{ color: 'var(--text-muted)', fontSize: '0.7rem' }}>
              <span>v{s.version}</span>
              {s.effort_band && <span>effort: {s.effort_band}</span>}
              {s.success_rate != null && (
                <span>rate: {Math.round(Number(s.success_rate) * 100)}%</span>
              )}
              {s.related && s.related.length > 0 && (
                <span>related: {s.related.slice(0, 3).join(', ')}</span>
              )}
            </div>
            <div className="mt-1.5 flex flex-wrap gap-1">
              <button
                type="button"
                disabled={busy === s.name}
                onClick={() => setStatus(s.name, 'active')}
                className="text-xs px-1.5 py-0.5 rounded"
                style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
                title="Force ACTIVE (trusted)"
              >
                Activate
              </button>
              <button
                type="button"
                disabled={busy === s.name}
                onClick={() => setStatus(s.name, 'disabled')}
                className="text-xs px-1.5 py-0.5 rounded"
                style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
              >
                Disable
              </button>
              {s.quarantine && (
                <button
                  type="button"
                  disabled={busy === s.name}
                  onClick={() => setStatus(s.name, 'validated')}
                  className="text-xs px-1.5 py-0.5 rounded"
                  style={{ border: '1px solid var(--border)', color: 'var(--accent)' }}
                  title="Leave quarantine (validated)"
                >
                  Trust
                </button>
              )}
              <button
                type="button"
                disabled={busy === s.name}
                onClick={() => feedback(s.name, true)}
                className="text-xs px-1.5 py-0.5 rounded"
                style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
                title="Record success feedback"
              >
                ✓
              </button>
              <button
                type="button"
                disabled={busy === s.name}
                onClick={() => feedback(s.name, false)}
                className="text-xs px-1.5 py-0.5 rounded"
                style={{ border: '1px solid var(--border)', color: 'var(--text-secondary)' }}
                title="Record failure feedback"
              >
                ✗
              </button>
            </div>
          </div>
        ))
      )}
    </Panel>
  )
}
