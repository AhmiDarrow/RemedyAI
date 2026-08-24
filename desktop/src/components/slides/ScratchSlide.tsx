import { useEffect, useMemo, useRef, useState } from 'react'
import { apiFetch } from '../../api/client'
import { isTauri, tauriInvoke } from '../../api/tauri'
import { SCRATCH_RELOAD_EVENT } from '../../workspace/railNav'
import { ConfirmDialog } from '../ConfirmDialog'

function storageKey(sessionId: string | null) {
  return `remedy.scratch.${sessionId || 'global'}`
}

function countStats(text: string) {
  const chars = text.length
  const lines = text.length === 0 ? 0 : text.split(/\r\n|\r|\n/).length
  const words = text.trim() ? text.trim().split(/\s+/).length : 0
  return { chars, lines, words }
}

/**
 * Session scratch pad — localStorage + optional download / clear.
 * Larger popout uses the same component.
 */
export function ScratchSlide({ sessionId }: { sessionId: string | null }) {
  const [text, setText] = useState('')
  const [preview, setPreview] = useState(false)
  const [status, setStatus] = useState('')
  const [dirty, setDirty] = useState(false)
  const [confirmClear, setConfirmClear] = useState(false)
  const key = useMemo(() => storageKey(sessionId), [sessionId])
  const persistTimer = useRef<number | null>(null)
  const statusTimer = useRef<number | null>(null)
  const textRef = useRef('')
  const keyRef = useRef(key)
  const taRef = useRef<HTMLTextAreaElement | null>(null)

  const flashStatus = (msg: string, ms = 2800) => {
    setStatus(msg)
    if (statusTimer.current != null) {
      window.clearTimeout(statusTimer.current)
      statusTimer.current = null
    }
    if (msg) {
      statusTimer.current = window.setTimeout(() => {
        statusTimer.current = null
        setStatus('')
      }, ms)
    }
  }

  const persistServer = (v: string, sid: string | null) => {
    void apiFetch('/scratch', {
      method: 'PUT',
      body: JSON.stringify({ session_id: sid, text: v }),
    }).catch(() => {
      /* local cache still holds it */
    })
  }

  const loadFromServer = async (sid: string | null, storageKeyNow: string) => {
    try {
      const q = sid ? `?session_id=${encodeURIComponent(sid)}` : ''
      const data = await apiFetch<{ text?: string }>(`/scratch${q}`)
      let t = data.text || ''
      if (!t) {
        try {
          t = localStorage.getItem(storageKeyNow) || ''
        } catch {
          t = ''
        }
        if (t) persistServer(t, sid)
      }
      return t
    } catch {
      try {
        return localStorage.getItem(storageKeyNow) || ''
      } catch {
        return ''
      }
    }
  }

  useEffect(() => {
    // Flush prior session's debounced text before switching keys
    if (persistTimer.current != null) {
      window.clearTimeout(persistTimer.current)
      persistTimer.current = null
      try {
        localStorage.setItem(keyRef.current, textRef.current)
      } catch {
        /* ignore */
      }
    }
    keyRef.current = key
    setPreview(false)
    setDirty(false)
    let cancelled = false
    void loadFromServer(sessionId, key).then((loaded) => {
      if (cancelled) return
      setText(loaded)
      textRef.current = loaded
      setStatus('')
    })
    const onReload = () => {
      void loadFromServer(sessionId, key).then((loaded) => {
        if (cancelled) return
        setText(loaded)
        textRef.current = loaded
      })
    }
    window.addEventListener(SCRATCH_RELOAD_EVENT, onReload)
    return () => {
      cancelled = true
      window.removeEventListener(SCRATCH_RELOAD_EVENT, onReload)
      if (persistTimer.current != null) {
        window.clearTimeout(persistTimer.current)
        persistTimer.current = null
        try {
          localStorage.setItem(keyRef.current, textRef.current)
        } catch {
          /* ignore */
        }
      }
      if (statusTimer.current != null) {
        window.clearTimeout(statusTimer.current)
        statusTimer.current = null
      }
    }
  }, [key, sessionId])

  const persistNow = (v: string, storageKeyOverride?: string) => {
    try {
      // Always write to the live key ref so a debounced save after session
      // switch cannot land notes on the wrong pad.
      localStorage.setItem(storageKeyOverride ?? keyRef.current, v)
      persistServer(v, sessionId)
      setDirty(false)
    } catch {
      flashStatus('Could not persist (storage full?)', 4000)
    }
  }

  /** Immediate UI update; debounce localStorage writes while typing. */
  const save = (v: string, flush = false) => {
    setText(v)
    textRef.current = v
    setDirty(true)
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
        flashStatus(path ? `Saved ${path}` : 'Save cancelled', path ? 4000 : 2000)
        return
      } catch (e: unknown) {
        flashStatus(e instanceof Error ? e.message : String(e), 4000)
      }
    }
    const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1500)
    flashStatus(`Downloaded ${name}`)
  }

  const stats = useMemo(() => countStats(text), [text])
  const sessionLabel = sessionId ? 'session' : 'global'

  return (
    <div className="flex flex-col h-full min-h-0 max-h-full overflow-hidden text-xs">
      <div
        className="px-2 py-1.5 border-b shrink-0 flex flex-wrap items-center gap-1"
        style={{
          borderColor: 'var(--border)',
          color: 'var(--text-muted)',
          background: 'var(--bg-secondary)',
          position: 'relative',
          zIndex: 2,
        }}
      >
        <span className="mr-auto truncate" title={`Scratch pad (${sessionLabel})`}>
          Scratch · {sessionLabel}
          {dirty ? ' · saving…' : ' · auto-saves'}
        </span>
        <button
          type="button"
          className="px-1.5 py-0.5 rounded"
          style={{
            background: preview ? 'var(--accent)' : 'var(--bg-primary)',
            color: preview ? '#fff' : 'var(--text-secondary)',
            border: '1px solid var(--border)',
          }}
          onClick={() => setPreview((p) => !p)}
          aria-pressed={preview}
        >
          {preview ? 'Edit' : 'Preview'}
        </button>
        <button
          type="button"
          className="px-1.5 py-0.5 rounded disabled:opacity-40"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-secondary)',
          }}
          disabled={!text}
          title="Copy all to clipboard"
          onClick={() => {
            void navigator.clipboard.writeText(text).then(
              () => flashStatus('Copied all'),
              () => flashStatus('Copy failed', 3000),
            )
          }}
        >
          Copy
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
          title="Export as Markdown file (Ctrl+S)"
        >
          Save as…
        </button>
        <button
          type="button"
          className="px-1.5 py-0.5 rounded disabled:opacity-40"
          style={{ color: 'var(--error)' }}
          disabled={!text}
          onClick={() => setConfirmClear(true)}
        >
          Clear
        </button>
      </div>
      {status ? (
        <div
          className="px-2 py-0.5 truncate shrink-0"
          style={{ color: 'var(--text-muted)' }}
          role="status"
          title={status}
        >
          {status}
        </div>
      ) : null}
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
          ref={taRef}
          value={text}
          onChange={(e) => save(e.target.value)}
          onBlur={() => {
            // Flush on blur so session switch never loses the last keystrokes
            if (persistTimer.current != null) {
              window.clearTimeout(persistTimer.current)
              persistTimer.current = null
              persistNow(textRef.current)
            }
          }}
          onKeyDown={(e) => {
            // Do not stop Esc — PopoutOverlay capture handler exits fullscreen / closes
            if (e.key === 'Escape') return
            // Ctrl/Cmd+S → export
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
              e.preventDefault()
              void download()
            }
          }}
          className="flex-1 min-h-0 w-full max-h-full resize-none p-2 outline-none text-sm"
          style={{
            background: 'var(--bg-primary)',
            color: 'var(--text-primary)',
            border: 'none',
            lineHeight: 1.5,
          }}
          placeholder="Notes, TODOs, paste dumps… (Markdown ok) · Ctrl+S export · Esc exits fullscreen"
          spellCheck
          aria-label="Scratch pad notes"
        />
      )}
      <div
        className="px-2 py-1 text-[10px] border-t shrink-0 flex items-center gap-2 tabular-nums"
        style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
      >
        <span className="truncate flex-1">
          {stats.lines} line{stats.lines === 1 ? '' : 's'} · {stats.words} word
          {stats.words === 1 ? '' : 's'} · {stats.chars} char{stats.chars === 1 ? '' : 's'}
        </span>
      </div>
      <ConfirmDialog
        open={confirmClear}
        title="Clear this scratch pad?"
        body={'Everything written here is erased. This can’t be undone — use “Save as…” first if you want to keep it.'}
        confirmLabel="Clear pad"
        onCancel={() => setConfirmClear(false)}
        onConfirm={() => {
          setConfirmClear(false)
          save('', true)
          flashStatus('Cleared')
          taRef.current?.focus()
        }}
      />
    </div>
  )
}
