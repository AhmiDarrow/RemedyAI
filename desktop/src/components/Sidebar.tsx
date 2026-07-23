import type { ChatSession } from '../types'

interface SidebarProps {
  sessions: ChatSession[]
  activeId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
}

export function Sidebar({ sessions, activeId, onSelect, onNew, onDelete }: SidebarProps) {
  return (
    <div
      className="flex flex-col border-r"
      style={{
        width: 240,
        background: 'var(--bg-secondary)',
        borderColor: 'var(--border)',
      }}
    >
      <div className="p-3 border-b" style={{ borderColor: 'var(--border)' }}>
        <button
          onClick={onNew}
          className="w-full text-left px-3 py-2 rounded-md text-sm font-medium transition-colors"
          style={{
            background: 'var(--accent)',
            color: '#fff',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--accent-hover)')}
          onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--accent)')}
        >
          + New Session
        </button>
      </div>

      <div className="flex-1 overflow-y-auto py-1">
        {sessions.map((s) => (
          <div
            key={s.id}
            className="group flex items-center gap-2 px-3 py-2 cursor-pointer text-sm transition-colors relative"
            style={{
              background: s.id === activeId ? 'var(--bg-tertiary)' : 'transparent',
              color: s.id === activeId ? 'var(--text-primary)' : 'var(--text-secondary)',
              borderLeft: s.id === activeId ? '3px solid var(--accent)' : '3px solid transparent',
            }}
            onClick={() => onSelect(s.id)}
            onMouseEnter={(e) => {
              if (s.id !== activeId) e.currentTarget.style.background = 'var(--bg-tertiary)'
            }}
            onMouseLeave={(e) => {
              if (s.id !== activeId) e.currentTarget.style.background = 'transparent'
            }}
          >
            <span className="truncate flex-1 min-w-0">{s.title || 'New Session'}</span>
            <span
              className="text-xs flex-shrink-0 w-6 text-right opacity-50 group-hover:opacity-100"
              style={{ color: 'var(--text-muted)' }}
            >
              {s.message_count}
            </span>
            {/* Always reserve width so hover does not shift the row */}
            <button
              className="flex-shrink-0 w-5 text-xs rounded opacity-0 pointer-events-none group-hover:opacity-60 group-hover:pointer-events-auto hover:!opacity-100"
              style={{ color: 'var(--error)' }}
              onClick={(e) => {
                e.stopPropagation()
                onDelete(s.id)
              }}
              title="Delete"
            >
              x
            </button>
          </div>
        ))}

        {sessions.length === 0 && (
          <div className="px-3 py-6 text-center text-sm" style={{ color: 'var(--text-muted)' }}>
            No sessions yet
          </div>
        )}
      </div>
    </div>
  )
}
