import { useState, useEffect } from 'react'

interface PanelProps {
  open: boolean
  onClose: () => void
  title: string
  children: React.ReactNode
}

export function Panel({ open, onClose, title, children }: PanelProps) {
  return (
    <div
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
          onClick={onClose}
          className="px-1 rounded"
          style={{ color: 'var(--text-muted)' }}
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
    <Panel open={open} onClose={onClose} title="Skills">
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
