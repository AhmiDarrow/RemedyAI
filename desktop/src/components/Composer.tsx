import { useState, useRef, useCallback, useEffect } from 'react'
import { searchFiles } from '../api/messages'

interface ComposerProps {
  onSend: (text: string) => void
  onStop: () => void
  onCommand: (command: string) => void
  streaming: boolean
  disabled: boolean
  planMode?: boolean
}

interface FileMatch {
  name: string
  path: string
  is_dir: boolean
}

export function Composer({ onSend, onStop, onCommand, streaming, disabled, planMode }: ComposerProps) {
  const [input, setInput] = useState('')
  const [suggestions, setSuggestions] = useState<FileMatch[]>([])
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [suggestionIdx, setSuggestionIdx] = useState(0)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const suggestTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const detectAtQuery = useCallback((text: string, cursorPos: number) => {
    const before = text.slice(0, cursorPos)
    const match = before.match(/@(\S*)$/)
    return match ? match[1] : null
  }, [])

  const handleSuggestionSelect = useCallback(
    (file: FileMatch) => {
      const cursorPos = textareaRef.current?.selectionStart ?? input.length
      const before = input.slice(0, cursorPos)
      const after = input.slice(cursorPos)
      const atIdx = before.lastIndexOf('@')
      const newInput = before.slice(0, atIdx) + file.path + ' ' + after
      setInput(newInput)
      setShowSuggestions(false)
      textareaRef.current?.focus()
    },
    [input],
  )

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
    [showSuggestions, suggestions, suggestionIdx],
  )

  const handleChange = useCallback(
    (text: string) => {
      setInput(text)
      const cursorPos = textareaRef.current?.selectionStart ?? text.length
      const q = detectAtQuery(text, cursorPos)

      if (q !== null && q.length >= 1) {
        clearTimeout(suggestTimer.current ?? undefined)
        suggestTimer.current = setTimeout(async () => {
          try {
            const r = await searchFiles(q)
            setSuggestions(r.results.slice(0, 8))
            setSuggestionIdx(0)
            setShowSuggestions(true)
          } catch {
            setShowSuggestions(false)
          }
        }, 150)
      } else {
        setShowSuggestions(false)
      }
    },
    [],
  )

  const handleSubmit = useCallback(() => {
    const text = input.trim()
    if (!text) return
    if (text.startsWith('/')) {
      onCommand(text)
    } else {
      onSend(text)
    }
    setInput('')
  }, [input, onSend, onCommand])

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
          {suggestions.map((f, i) => (
            <button
              key={f.path}
              className="flex items-center gap-2 w-full text-left px-3 py-1.5"
              style={{
                background: i === suggestionIdx ? 'var(--bg-tertiary)' : 'transparent',
                color: 'var(--text-primary)',
              }}
              onMouseDown={(e) => {
                e.preventDefault()
                handleSuggestionSelect(f)
              }}
            >
              <span style={{ color: 'var(--text-muted)' }}>
                {f.is_dir ? '\u25B6' : '\u25CF'}
              </span>
              <span className="truncate">{f.path}</span>
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
              : disabled
                ? 'Select or create a session to begin'
                : 'Type a message, /command, or @file...'
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
