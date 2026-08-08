import { useState, useCallback, useEffect, useRef } from 'react'

export interface CommandItem {
  id: string
  label: string
  description: string
  category: string
  action: () => void
}

interface CommandPaletteProps {
  open: boolean
  onClose: () => void
  commands: CommandItem[]
}

export function CommandPalette({ open, onClose, commands }: CommandPaletteProps) {
  const [query, setQuery] = useState('')
  const [idx, setIdx] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const filtered = query
    ? commands.filter(
        (c) =>
          c.label.toLowerCase().includes(query.toLowerCase()) ||
          c.description.toLowerCase().includes(query.toLowerCase()) ||
          c.category.toLowerCase().includes(query.toLowerCase()),
      )
    : commands

  useEffect(() => {
    setIdx(0)
    setQuery('')
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open])

  useEffect(() => {
    setIdx(0)
  }, [query])

  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-cmd-idx="${idx}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [idx])

  const execute = useCallback(
    (item: CommandItem) => {
      item.action()
      onClose()
    },
    [onClose],
  )

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setIdx((i) => (i + 1) % Math.max(filtered.length, 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setIdx((i) => (i - 1 + filtered.length) % Math.max(filtered.length, 1))
      } else if (e.key === 'Enter') {
        e.preventDefault()
        if (filtered[idx]) execute(filtered[idx])
      } else if (e.key === 'Escape') {
        onClose()
      }
    },
    [filtered, idx, execute, onClose],
  )

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[14vh] px-4 ui-overlay"
      onClick={onClose}
    >
      <div
        className="command-palette ui-surface w-full max-w-[560px] max-h-[62vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
      >
        <div
          className="px-3.5 py-3 flex items-center gap-2.5"
          style={{ borderBottom: '1px solid color-mix(in srgb, var(--border) 85%, transparent)' }}
        >
          <span
            className="text-sm font-semibold tabular-nums"
            style={{ color: 'var(--accent)' }}
            aria-hidden
          >
            ⌘
          </span>
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search commands, sessions, agents…"
            className="flex-1 outline-none text-sm bg-transparent"
            style={{ color: 'var(--text-primary)' }}
            aria-label="Search commands"
          />
          <kbd
            className="text-[0.65rem] px-1.5 py-0.5 rounded-md font-mono"
            style={{
              background: 'var(--bg-tertiary)',
              color: 'var(--text-muted)',
              border: '1px solid color-mix(in srgb, var(--border) 80%, transparent)',
            }}
          >
            Esc
          </kbd>
        </div>

        <div ref={listRef} className="overflow-y-auto flex-1 py-1">
          {filtered.length === 0 ? (
            <div className="px-4 py-10 text-center text-sm" style={{ color: 'var(--text-muted)' }}>
              No matching commands
            </div>
          ) : (
            filtered.slice(0, 30).map((item, i) => (
              <div
                key={item.id}
                data-cmd-idx={i}
                className={`command-palette-row text-sm${i === idx ? ' is-active' : ''}`}
                onMouseEnter={() => setIdx(i)}
                onMouseDown={(e) => {
                  e.preventDefault()
                  execute(item)
                }}
              >
                <span
                  className="text-[0.62rem] px-1.5 py-0.5 rounded-md flex-shrink-0 uppercase tracking-wide font-semibold"
                  style={{
                    background: 'color-mix(in srgb, var(--bg-primary) 80%, transparent)',
                    color: 'var(--text-muted)',
                    border: '1px solid color-mix(in srgb, var(--border) 70%, transparent)',
                  }}
                >
                  {item.category}
                </span>
                <span className="flex-1 font-medium truncate" style={{ color: 'var(--text-primary)' }}>
                  {item.label}
                </span>
                <span
                  className="text-xs flex-shrink-0 truncate max-w-[200px]"
                  style={{ color: 'var(--text-muted)' }}
                >
                  {item.description}
                </span>
              </div>
            ))
          )}
        </div>

        <div
          className="px-4 py-2 text-[0.65rem] flex flex-wrap gap-x-4 gap-y-1"
          style={{
            borderTop: '1px solid color-mix(in srgb, var(--border) 85%, transparent)',
            color: 'var(--text-muted)',
            background: 'color-mix(in srgb, var(--bg-tertiary) 40%, transparent)',
          }}
        >
          <span>
            <kbd className="font-mono opacity-80">↑↓</kbd> navigate
          </span>
          <span>
            <kbd className="font-mono opacity-80">↵</kbd> select
          </span>
          <span>
            <kbd className="font-mono opacity-80">Esc</kbd> dismiss
          </span>
        </div>
      </div>
    </div>
  )
}
