import { useEffect, useMemo, useRef, useState } from 'react'
import { isTauri, tauriInvoke } from '../../api/tauri'

function storageKey(sessionId: string | null) {
  return `remedy.scratch.${sessionId || 'global'}`
}

/**
 * Session scratch pad — localStorage + optional download / clear.
 * Larger popout uses the same component.
 */
export function ScratchSlide({ sessionId }: { sessionId: string | null }) {
  const [text, setText] = useState('')
  const [preview, setPreview] = useState(false)
  const [status, setStatus] = useState('')
  const key = useMemo(() => storageKey(sessionId), [sessionId])
  const persistTimer = useRef<number | null>(null)

  useEffect(() => {
    try {
      setText(localStorage.getItem(key) || '')
      setStatus('')
    } catch {
      setText('')
    }
    return () => {
      if (persistTimer.current != null) {
        window.clearTimeout(persistTimer.current)
        persistTimer.current = null
      }
    }
  }, [key])

  const persistNow = (v: string) => {
    try {
      localStorage.setItem(key, v)
    } catch {
      setStatus('Could not persist (storage full?)')
    }
  }

  /** Immediate UI update; debounce localStorage writes while typing. */
  const save = (v: string, flush = false) => {
    setText(v)
    if (persistTimer.current != null) {
      window.clearTimeout(persistTimer.current)
      persistTimer.current = null
    }
    if (flush) {
      persistNow(v)
      return
    }
    persistTimer.current = window.setTimeout(() => {
      persistTimer.current = null
      persistNow(v)
    }, 250)
  }

  const download = async () => {
    // Flush any debounced persist so disk export matches the textarea.
    if (persistTimer.current != null) {
      window.clearTimeout(persistTimer.current)
      persistTimer.current = null
    }
    persistNow(text)
    const name = `scratch-${(sessionId || 'global').slice(0, 8)}.md`
    if (isTauri()) {
      try {
        const path = await tauriInvoke<string | null>('save_text_file', {
          defaultName: name,
          contents: text,
        })
        setStatus(path ? `Saved ${path}` : 'Save cancelled')
        return
      } catch (e: unknown) {
        setStatus(e instanceof Error ? e.message : String(e))
      }
    }
    const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1500)
    setStatus(`Downloaded ${name}`)
  }

  return (
    <div className="flex flex-col h-full min-h-0 text-xs">
      <div
        className="px-2 py-1.5 border-b shrink-0 flex flex-wrap items-center gap-1"
        style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
      >
        <span className="mr-auto">Scratch · auto-saves</span>
        <button
          type="button"
          className="px-1.5 py-0.5 rounded"
          style={{
            background: preview ? 'var(--accent)' : 'var(--bg-primary)',
            color: preview ? '#fff' : 'var(--text-secondary)',
            border: '1px solid var(--border)',
          }}
          onClick={() => setPreview((p) => !p)}
        >
          {preview ? 'Edit' : 'Preview'}
        </button>
        <button
          type="button"
          className="px-1.5 py-0.5 rounded"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-secondary)',
          }}
          onClick={() => void download()}
        >
          Save as…
        </button>
        <button
          type="button"
          className="px-1.5 py-0.5 rounded"
          style={{ color: 'var(--error)' }}
          onClick={() => {
            if (window.confirm('Clear this scratch pad?')) save('', true)
          }}
        >
          Clear
        </button>
      </div>
      {status && (
        <div className="px-2 py-0.5 truncate" style={{ color: 'var(--text-muted)' }}>
          {status}
        </div>
      )}
      {preview ? (
        <pre
          className="flex-1 min-h-0 overflow-auto p-2 m-0 whitespace-pre-wrap text-sm"
          style={{
            background: 'var(--bg-primary)',
            color: 'var(--text-primary)',
            lineHeight: 1.5,
          }}
        >
          {text || '— empty —'}
        </pre>
      ) : (
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
          placeholder="Notes, TODOs, paste dumps… (Markdown ok)"
          spellCheck
        />
      )}
    </div>
  )
}
