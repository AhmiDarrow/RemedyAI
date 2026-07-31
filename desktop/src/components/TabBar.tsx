import type { ChatSession } from '../types'

interface TabBarProps {
  tabs: ChatSession[]
  activeId: string | null
  onSelect: (id: string) => void
  onClose: (id: string) => void
  onNew: () => void
  onExport?: (id: string) => void
}

export function TabBar({ tabs, activeId, onSelect, onClose, onNew, onExport }: TabBarProps) {
  if (tabs.length === 0) return null

  return (
    <div
      className="flex items-center border-b overflow-x-auto"
      role="tablist"
      aria-label="Open chats"
      style={{
        background: 'var(--bg-primary)',
        borderColor: 'var(--border)',
        height: 36,
        flexShrink: 0,
      }}
    >
      {tabs.map((tab) => {
        const active = tab.id === activeId
        const title = tab.title || 'Untitled'
        return (
          <div
            key={tab.id}
            role="tab"
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            className="group flex items-center gap-1 px-3 h-full cursor-pointer border-r text-xs whitespace-nowrap transition-colors"
            style={{
              background: active
                ? 'var(--bg-secondary)'
                : 'transparent',
              borderColor: 'var(--border)',
              color: active ? 'var(--text-primary)' : 'var(--text-muted)',
              borderBottom: active
                ? '2px solid var(--accent)'
                : '2px solid transparent',
            }}
            onClick={() => onSelect(tab.id)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onSelect(tab.id)
              }
            }}
            onContextMenu={(e) => {
              e.preventDefault()
              onExport?.(tab.id)
            }}
            title={title}
          >
            <span className="truncate max-w-[140px]">{title}</span>
            <button
              type="button"
              className="ml-0.5 text-xs rounded-full w-4 h-4 flex items-center justify-center opacity-0 group-hover:opacity-100 focus-visible:opacity-100 transition-opacity"
              style={{ color: 'var(--text-muted)' }}
              onClick={(e) => {
                e.stopPropagation()
                onClose(tab.id)
              }}
              title="Close tab"
              aria-label={`Close ${title}`}
            >
              {'\u00D7'}
            </button>
          </div>
        )
      })}

      <button
        type="button"
        onClick={onNew}
        className="px-3 h-full text-xs transition-colors flex-shrink-0"
        style={{ color: 'var(--text-muted)', background: 'transparent' }}
        title="New tab"
        aria-label="New chat tab"
      >
        +
      </button>

      <div className="flex-1" />
    </div>
  )
}
