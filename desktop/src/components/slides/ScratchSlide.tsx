import { useEffect, useState } from 'react'

function keyFor(sessionId: string | null) {
  return `remedy.scratch.${sessionId || 'global'}`
}

export function ScratchSlide({ sessionId }: { sessionId: string | null }) {
  const [text, setText] = useState('')

  useEffect(() => {
    try {
      setText(localStorage.getItem(keyFor(sessionId)) || '')
    } catch {
      setText('')
    }
  }, [sessionId])

  const save = (v: string) => {
    setText(v)
    try {
      localStorage.setItem(keyFor(sessionId), v)
    } catch {
      /* */
    }
  }

  return (
    <div className="flex flex-col h-full min-h-0 text-xs">
      <div
        className="px-2 py-1.5 border-b shrink-0"
        style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
      >
        Session scratch pad · auto-saves locally
      </div>
      <textarea
        value={text}
        onChange={(e) => save(e.target.value)}
        className="flex-1 min-h-0 w-full resize-none p-2 outline-none text-sm"
        style={{
          background: 'var(--bg-primary)',
          color: 'var(--text-primary)',
          border: 'none',
          lineHeight: 1.5,
        }}
        placeholder="Notes, TODOs, paste dumps…"
        spellCheck
      />
    </div>
  )
}
