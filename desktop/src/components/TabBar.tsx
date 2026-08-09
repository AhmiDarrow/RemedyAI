import { useEffect, useRef, type WheelEvent } from 'react'
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
  const scrollerRef = useRef<HTMLDivElement>(null)
  const activeTabRef = useRef<HTMLDivElement | null>(null)
  const keepFocusRef = useRef(false)

  // After keyboard arrow navigation, move focus onto the newly active tab
  useEffect(() => {
    if (!keepFocusRef.current) return
    keepFocusRef.current = false
    activeTabRef.current?.focus()
    activeTabRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [activeId])

  if (tabs.length === 0) return null

  const onWheel = (e: WheelEvent) => {
    const el = scrollerRef.current
    if (!el) return
    // Prefer horizontal scroll when the row is overflowing
    if (el.scrollWidth <= el.clientWidth + 2) return
    if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
      el.scrollLeft += e.deltaY
      e.preventDefault()
    }
  }

  return (
    <div
      className="tab-bar flex items-center border-b"
      role="tablist"
      aria-label="Open chats"
      style={{
        background: 'var(--bg-primary)',
        borderColor: 'var(--border)',
        height: 36,
        flexShrink: 0,
      }}
    >
      <div
        ref={scrollerRef}
        className="flex items-center flex-1 min-w-0 overflow-x-auto h-full"
        onWheel={onWheel}
      >
        {tabs.map((tab) => {
          const active = tab.id === activeId
          const title = tab.title || 'Untitled'
          return (
            <div
              key={tab.id}
              ref={active ? activeTabRef : undefined}
              role="tab"
              aria-selected={active}
              tabIndex={active ? 0 : -1}
              className={`tab-bar-item group flex items-center gap-1 px-3 h-full cursor-pointer border-r text-xs whitespace-nowrap${
                active ? ' is-active' : ''
              }`}
              style={{
                background: active ? 'var(--bg-secondary)' : 'transparent',
                borderColor: 'var(--border)',
                color: active ? 'var(--text-primary)' : 'var(--text-muted)',
                borderBottom: active
                  ? '2px solid var(--accent)'
                  : '2px solid transparent',
              }}
              onClick={() => onSelect(tab.id)}
              onAuxClick={(e) => {
                // Middle-click closes (browser-tab muscle memory)
                if (e.button === 1) {
                  e.preventDefault()
                  onClose(tab.id)
                }
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onSelect(tab.id)
                } else if (e.key === 'Delete' || (e.key === 'w' && (e.ctrlKey || e.metaKey))) {
                  e.preventDefault()
                  onClose(tab.id)
                } else if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
                  e.preventDefault()
                  const i = tabs.findIndex((t) => t.id === tab.id)
                  const next =
                    e.key === 'ArrowRight'
                      ? tabs[(i + 1) % tabs.length]
                      : tabs[(i - 1 + tabs.length) % tabs.length]
                  if (next) {
                    keepFocusRef.current = true
                    onSelect(next.id)
                  }
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
      </div>

      <button
        type="button"
        onClick={onNew}
        className="tab-bar-new px-2.5 h-full flex items-center justify-center shrink-0 text-sm"
        style={{
          color: 'var(--text-muted)',
          borderLeft: '1px solid var(--border)',
          background: 'transparent',
        }}
        title="New chat"
        aria-label="New chat"
      >
        +
      </button>
    </div>
  )
}
