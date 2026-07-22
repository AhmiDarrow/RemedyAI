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
      style={{
        background: 'var(--bg-primary)',
        borderColor: 'var(--border)',
        height: 36,
        flexShrink: 0,
      }}
    >
      {tabs.map((tab) => (
        <div
          key={tab.id}
          className="flex items-center gap-1 px-3 h-full cursor-pointer border-r text-xs whitespace-nowrap transition-colors"
          style={{
            background: tab.id === activeId ? 'var(--bg-secondary)' : 'transparent',
            borderColor: 'var(--border)',
            color: tab.id === activeId ? 'var(--text-primary)' : 'var(--text-muted)',
            borderBottom: tab.id === activeId ? '2px solid var(--accent)' : '2px solid transparent',
          }}
          onClick={() => onSelect(tab.id)}
          onContextMenu={(e) => {
            e.preventDefault()
            onExport?.(tab.id)
          }}
        >
          <span className="truncate max-w-[140px]">{tab.title || 'Untitled'}</span>
          <button
            className="ml-0.5 text-xs rounded-full w-4 h-4 flex items-center justify-center opacity-0 hover:opacity-100 transition-opacity"
            style={{ color: 'var(--text-muted)' }}
            onClick={(e) => {
              e.stopPropagation()
              onClose(tab.id)
            }}
            title="Close tab"
          >
            {'\u00D7'}
          </button>
        </div>
      ))}

      <button
        onClick={onNew}
        className="px-3 h-full text-xs transition-colors flex-shrink-0"
        style={{ color: 'var(--text-muted)', background: 'transparent' }}
        title="New tab"
      >
        +
      </button>

      <div className="flex-1" />
    </div>
  )
}
