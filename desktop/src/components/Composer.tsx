import { useState, useRef, useCallback, useEffect } from 'react'

interface ComposerProps {
  onSend: (text: string) => void
  onStop: () => void
  onCommand: (command: string) => void | undefined
  streaming: boolean
  disabled: boolean
}

export function Composer({ onSend, onStop, onCommand, streaming, disabled }: ComposerProps) {
  const [input, setInput] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const handleSubmit = useCallback(() => {
    const text = input.trim()
    if (!text) return

    if (text.startsWith('/')) {
      onCommand?.(text)
    } else {
      onSend(text)
    }
    setInput('')
  }, [input, onSend, onCommand])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSubmit()
      }
    },
    [handleSubmit],
  )

  useEffect(() => {
    textareaRef.current?.focus()
  }, [streaming])

  return (
    <div
      className="p-3 border-t flex items-end gap-2"
      style={{ background: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
    >
      <textarea
        ref={textareaRef}
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Type a message or /command..."
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
          style={{
            background: 'var(--error)',
            color: '#fff',
          }}
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
  )
}
