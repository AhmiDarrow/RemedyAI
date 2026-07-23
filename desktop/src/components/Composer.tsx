import { useState, useRef, useCallback, useEffect } from 'react'
import { searchFiles } from '../api/messages'

export interface AgentDef {
  name: string
  description: string
}

interface ComposerProps {
  onSend: (text: string) => void
  onStop: () => void
  onCommand: (command: string) => void
  streaming: boolean
  disabled: boolean
  planMode?: boolean
  agents?: AgentDef[]
  /** When set, loads text into the composer (edit-and-resend flow). */
  editDraft?: string | null
  onEditDraftConsumed?: () => void
}

type SuggestionItem = { label: string; value: string; icon: string; type: 'file' | 'agent' }

export function Composer({
  onSend,
  onStop,
  onCommand,
  streaming,
  disabled,
  planMode,
  agents = [],
  editDraft,
  onEditDraftConsumed,
}: ComposerProps) {
  const [input, setInput] = useState('')
  const [suggestions, setSuggestions] = useState<SuggestionItem[]>([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [suggestionIdx, setSuggestionIdx] = useState(0)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const suggestTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const submittingRef = useRef(false)

  useEffect(() => {
    if (editDraft != null && editDraft !== '') {
      setInput(editDraft)
      onEditDraftConsumed?.()
      requestAnimationFrame(() => {
        const el = textareaRef.current
        if (el) {
          el.focus()
          el.selectionStart = el.selectionEnd = el.value.length
        }
      })
    }
  }, [editDraft, onEditDraftConsumed])

  const detectAtQuery = useCallback((text: string, cursorPos: number) => {
    const before = text.slice(0, cursorPos)
    const match = before.match(/@(\S*)$/)
    return match ? match[1] : null
  }, [])

  const handleSuggestionSelect = useCallback(
    (item: SuggestionItem) => {
      const cursorPos = textareaRef.current?.selectionStart ?? input.length
      const before = input.slice(0, cursorPos)
      const after = input.slice(cursorPos)
      const atIdx = before.lastIndexOf('@')
      const newInput = before.slice(0, atIdx) + item.value + ' ' + after
      setInput(newInput)
      setShowSuggestions(false)
      textareaRef.current?.focus()
    },
    [input],
  )

  const handleSubmit = useCallback(() => {
    const text = input.trim()
    if (!text || streaming || disabled) return
    // Guard against Enter + click double-fire in the same tick.
    if (submittingRef.current) return
    submittingRef.current = true
    try {
      if (text.startsWith('/')) {
        onCommand(text)
      } else {
        onSend(text)
      }
      setInput('')
    } finally {
      // Release on next frame so a second synthetic submit is ignored.
      requestAnimationFrame(() => {
        submittingRef.current = false
      })
    }
  }, [input, onSend, onCommand, streaming, disabled])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (showSuggestions) {
        if (e.key === 'ArrowDown') {
          e.preventDefault()
          setSuggestionIdx((i) => (i + 1) % suggestions.length)
          return
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault()
          setSuggestionIdx((i) => (i - 1 + suggestions.length) % suggestions.length)
          return
        }
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault()
          if (suggestions[suggestionIdx]) {
            handleSuggestionSelect(suggestions[suggestionIdx])
          }
          return
        }
        if (e.key === 'Escape') {
          setShowSuggestions(false)
          return
        }
      }

      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSubmit()
      }
    },
    [showSuggestions, suggestions, suggestionIdx, handleSuggestionSelect, handleSubmit],
  )

  const handleChange = useCallback(
    (text: string) => {
      setInput(text)
      const cursorPos = textareaRef.current?.selectionStart ?? text.length
      const q = detectAtQuery(text, cursorPos)

      if (q !== null && q.length >= 1) {
        clearTimeout(suggestTimer.current ?? undefined)
        suggestTimer.current = setTimeout(async () => {
          const items: SuggestionItem[] = []

          const matchedAgents = agents
            .filter((a) => a.name.toLowerCase().includes(q.toLowerCase()))
            .slice(0, 4)
            .map((a) => ({
              label: a.name,
              value: `@${a.name}`,
              icon: '@',
              type: 'agent' as const,
            }))
          items.push(...matchedAgents)

          if (q.length >= 2) {
            try {
              const r = await searchFiles(q)
              for (const f of r.results.slice(0, 6)) {
                items.push({
                  label: f.name,
                  value: f.path,
                  icon: f.is_dir ? '\u25B6' : '\u25CF',
                  type: 'file' as const,
                })
              }
            } catch {
              // ignore
            }
          }

          if (items.length > 0) {
            setSuggestions(items)
            setSuggestionIdx(0)
            setShowSuggestions(true)
          } else {
            setShowSuggestions(false)
          }
        }, 120)
      } else {
        setShowSuggestions(false)
      }
    },
    [agents, detectAtQuery],
  )

  useEffect(() => {
    textareaRef.current?.focus()
  }, [streaming])

  return (
    <div
      className="p-3 border-t flex flex-col"
      style={{ background: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
    >
      {suggestions.length > 0 && showSuggestions && (
        <div
          className="mb-1 rounded-md border text-xs max-h-40 overflow-y-auto"
          style={{
            background: 'var(--bg-primary)',
            borderColor: 'var(--border)',
          }}
        >
          {suggestions.map((s, i) => (
            <button
              key={`${s.type}-${s.value}`}
              className="flex items-center gap-2 w-full text-left px-3 py-1.5"
              style={{
                background: i === suggestionIdx ? 'var(--bg-tertiary)' : 'transparent',
                color: 'var(--text-primary)',
              }}
              onMouseDown={(e) => {
                e.preventDefault()
                handleSuggestionSelect(s)
              }}
            >
              <span style={{ color: s.type === 'agent' ? 'var(--accent)' : 'var(--text-muted)', width: 16, textAlign: 'center' }}>
                {s.icon}
              </span>
              <span className="truncate">{s.label}</span>
              <span className="ml-auto text-[0.65rem]" style={{ color: 'var(--text-muted)' }}>
                {s.type}
              </span>
            </button>
          ))}
        </div>
      )}

      <div className="flex items-end gap-2">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => handleChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            planMode
              ? 'Plan mode — describe what to do (no tools executed)'
              : 'Type a message, /command, @agent, or @file...'
          }
          disabled={disabled}
          rows={1}
          className="flex-1 resize-none rounded-md px-3 py-2 text-sm outline-none transition-colors"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
            maxHeight: 160,
          }}
          onFocus={(e) => (e.currentTarget.style.borderColor = 'var(--accent)')}
          onBlur={(e) => (e.currentTarget.style.borderColor = 'var(--border)')}
        />

        {streaming ? (
          <button
            onClick={onStop}
            className="px-4 py-2 rounded-md text-sm font-medium transition-colors"
            style={{ background: 'var(--error)', color: '#fff' }}
          >
            Stop
          </button>
        ) : (
          <button
            onClick={handleSubmit}
            disabled={disabled || !input.trim()}
            className="px-4 py-2 rounded-md text-sm font-medium transition-colors"
            style={{
              background: disabled || !input.trim() ? 'var(--bg-tertiary)' : 'var(--accent)',
              color: disabled || !input.trim() ? 'var(--text-muted)' : '#fff',
              cursor: disabled || !input.trim() ? 'not-allowed' : 'pointer',
            }}
          >
            Send
          </button>
        )}
      </div>
    </div>
  )
}
