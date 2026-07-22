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
      className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]"
      style={{ background: 'rgba(0,0,0,0.5)' }}
      onClick={onClose}
    >
      <div
        className="w-[560px] max-h-[60vh] rounded-xl overflow-hidden shadow-2xl flex flex-col"
        style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="px-4 py-3 flex items-center gap-2" style={{ borderBottom: '1px solid var(--border)' }}>
          <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>{'>'}</span>
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search commands, sessions, agents..."
            className="flex-1 outline-none text-sm"
            style={{ background: 'transparent', color: 'var(--text-primary)' }}
          />
          <kbd
            className="text-xs px-1.5 py-0.5 rounded"
            style={{ background: 'var(--bg-tertiary)', color: 'var(--text-muted)', fontSize: '0.65rem' }}
          >
            ESC
          </kbd>
        </div>

        <div className="overflow-y-auto flex-1">
          {filtered.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm" style={{ color: 'var(--text-muted)' }}>
              No matching commands
            </div>
          ) : (
            filtered.slice(0, 30).map((item, i) => (
              <div
                key={item.id}
                className="flex items-center gap-3 px-4 py-2.5 cursor-pointer transition-colors text-sm"
                style={{
                  background: i === idx ? 'var(--bg-tertiary)' : 'transparent',
                  borderLeft: i === idx ? '2px solid var(--accent)' : '2px solid transparent',
                }}
                onMouseEnter={() => setIdx(i)}
                onMouseDown={(e) => {
                  e.preventDefault()
                  execute(item)
                }}
              >
                <span
                  className="text-xs px-1.5 py-0.5 rounded flex-shrink-0"
                  style={{ background: 'var(--bg-primary)', color: 'var(--text-muted)', fontSize: '0.65rem' }}
                >
                  {item.category}
                </span>
                <span className="flex-1" style={{ color: 'var(--text-primary)' }}>
                  {item.label}
                </span>
                <span className="text-xs flex-shrink-0 truncate max-w-[200px]" style={{ color: 'var(--text-muted)' }}>
                  {item.description}
                </span>
              </div>
            ))
          )}
        </div>

        <div
          className="px-4 py-1.5 text-xs flex gap-3"
          style={{ borderTop: '1px solid var(--border)', color: 'var(--text-muted)' }}
        >
          <span><kbd style={{ background: 'var(--bg-tertiary)', padding: '0 4px', borderRadius: 3, fontSize: '0.6rem' }}>↑↓</kbd> Navigate</span>
          <span><kbd style={{ background: 'var(--bg-tertiary)', padding: '0 4px', borderRadius: 3, fontSize: '0.6rem' }}>↵</kbd> Select</span>
          <span><kbd style={{ background: 'var(--bg-tertiary)', padding: '0 4px', borderRadius: 3, fontSize: '0.6rem' }}>ESC</kbd> Dismiss</span>
        </div>
      </div>
    </div>
  )
}
