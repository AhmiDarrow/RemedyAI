import { useState, useCallback, useEffect, useRef, type KeyboardEvent } from 'react'
import { browserStackHold } from '../utils/browserStack'
import { EmptyState } from './EmptyState'

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

  const q = query.trim().toLowerCase()
  const filtered = q
    ? commands.filter(
        (c) =>
          c.label.toLowerCase().includes(q)
          || c.description.toLowerCase().includes(q)
          || c.category.toLowerCase().includes(q)
          || c.id.toLowerCase().includes(q),
      )
    : commands

  const visible = filtered.slice(0, 40)

  useEffect(() => {
    setIdx(0)
    setQuery('')
    if (open) {
      const t = window.setTimeout(() => inputRef.current?.focus(), 40)
      return () => window.clearTimeout(t)
    }
  }, [open])

  useEffect(() => {
    setIdx(0)
  }, [query])

  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-cmd-idx="${idx}"]`)
    el?.scrollIntoView({ block: 'nearest' })
  }, [idx])

  // Keep native Browser embed from covering the palette
  useEffect(() => {
    if (!open) return
    return browserStackHold('command-palette')
  }, [open])

  // Esc / backdrop already close; also stop body scroll while open
  useEffect(() => {
    if (!open) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = prev
    }
  }, [open])

  const execute = useCallback(
    (item: CommandItem) => {
      item.action()
      onClose()
    },
    [onClose],
  )

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      const n = Math.max(visible.length, 1)
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setIdx((i) => (i + 1) % n)
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setIdx((i) => (i - 1 + n) % n)
      } else if (e.key === 'Home') {
        e.preventDefault()
        setIdx(0)
      } else if (e.key === 'End') {
        e.preventDefault()
        setIdx(Math.max(0, visible.length - 1))
      } else if (e.key === 'Enter') {
        e.preventDefault()
        if (visible[idx]) execute(visible[idx])
      } else if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
    },
    [visible, idx, execute, onClose],
  )

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[14vh] px-4 ui-overlay"
      onClick={onClose}
      role="presentation"
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
            placeholder="Search commands…"
            className="flex-1 outline-none text-sm bg-transparent"
            style={{ color: 'var(--text-primary)' }}
            aria-label="Search commands"
            aria-controls="command-palette-list"
            autoComplete="off"
            spellCheck={false}
          />
          {q ? (
            <span
              className="text-[0.65rem] tabular-nums shrink-0"
              style={{ color: 'var(--text-muted)' }}
            >
              {filtered.length}
            </span>
          ) : null}
          <kbd
            className="text-[0.65rem] px-1.5 py-0.5 rounded-md font-mono shrink-0"
            style={{
              background: 'var(--bg-tertiary)',
              color: 'var(--text-muted)',
              border: '1px solid color-mix(in srgb, var(--border) 80%, transparent)',
            }}
          >
            Esc
          </kbd>
        </div>

        <div
          ref={listRef}
          id="command-palette-list"
          className="overflow-y-auto flex-1 py-1"
          role="listbox"
          aria-label="Commands"
        >
          {visible.length === 0 ? (
            <EmptyState
              compact
              title="No matching commands"
              description={q ? `Nothing matches “${query.trim()}”.` : 'No commands registered.'}
            />
          ) : (
            visible.map((item, i) => (
              <div
                key={item.id}
                data-cmd-idx={i}
                role="option"
                aria-selected={i === idx}
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
                  title={item.description}
                >
                  {item.description}
                </span>
              </div>
            ))
          )}
        </div>

        <div
          className="px-4 py-2 text-[0.65rem] flex flex-wrap gap-x-4 gap-y-1 items-center"
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
          {filtered.length > visible.length ? (
            <span className="ml-auto tabular-nums">
              Showing {visible.length} of {filtered.length}
            </span>
          ) : (
            <span className="ml-auto tabular-nums">{commands.length} commands</span>
          )}
        </div>
      </div>
    </div>
  )
}
