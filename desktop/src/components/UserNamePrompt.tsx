import { useState } from 'react'
import { RemedyLogo } from './RemedyLogo'

interface UserNamePromptProps {
  open: boolean
  initial?: string
  onSave: (name: string) => void
  onSkip?: () => void
}

/** First-run / missing name: ask what Remedy should call the user. */
export function UserNamePrompt({ open, initial = '', onSave, onSkip }: UserNamePromptProps) {
  const [name, setName] = useState(initial)
  if (!open) return null

  const submit = () => {
    const n = name.trim()
    if (!n) return
    onSave(n)
  }

  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.55)' }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="user-name-title"
    >
      <div
        className="w-full max-w-sm rounded-xl p-5 shadow-2xl"
        style={{
          background: 'var(--bg-secondary)',
          border: '1px solid var(--border)',
          color: 'var(--text-primary)',
        }}
      >
        <div className="flex items-center gap-3 mb-3">
          <RemedyLogo size={32} framed />
          <div>
            <div id="user-name-title" className="font-semibold text-sm">
              What should Remedy call you?
            </div>
            <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
              Used in chat and memory — change anytime in Settings.
            </div>
          </div>
        </div>
        <input
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submit()
          }}
          placeholder="Your name"
          className="w-full rounded-lg px-3 py-2 text-sm outline-none mb-3"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
          }}
        />
        <div className="flex gap-2 justify-end">
          {onSkip && (
            <button
              type="button"
              className="px-3 py-1.5 rounded-lg text-xs"
              style={{
                background: 'var(--bg-tertiary)',
                color: 'var(--text-secondary)',
                border: '1px solid var(--border)',
              }}
              onClick={onSkip}
            >
              Later
            </button>
          )}
          <button
            type="button"
            className="px-3 py-1.5 rounded-lg text-xs font-medium"
            style={{
              background: name.trim() ? 'var(--accent)' : 'var(--bg-tertiary)',
              color: name.trim() ? '#fff' : 'var(--text-muted)',
              cursor: name.trim() ? 'pointer' : 'not-allowed',
            }}
            disabled={!name.trim()}
            onClick={submit}
          >
            Save
          </button>
        </div>
      </div>
    </div>
  )
}
