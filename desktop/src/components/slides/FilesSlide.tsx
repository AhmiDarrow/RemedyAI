import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { apiFetch } from '../../api/client'
import { isTauri, tauriInvoke } from '../../api/tauri'
import { FILES_SET_PATH_EVENT, takePendingFilesPath } from '../../workspace/railNav'
import { EmptyState } from '../EmptyState'
import { absoluteFilePath, parentFilePath } from '../../utils/filePaths'

type Entry = { name: string; path: string; is_dir: boolean }

/**
 * Project / session file browser: open, attach path to clipboard, navigate.
 * Follows session project_path via /api/files?session_id=.
 */
export function FilesSlide({
  sessionId,
  onAttachPath,
}: {
  sessionId: string | null
  /** Optional: parent can attach file path to composer */
  onAttachPath?: (path: string) => void
}) {
  const [root, setRoot] = useState('')
  const [path, setPath] = useState('.')
  const [files, setFiles] = useState<Entry[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState('')
  const [filter, setFilter] = useState('')
  const [focusIdx, setFocusIdx] = useState(-1)
  const statusTimer = useRef<number | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null)
  const listId = useId()
  /** Ignore out-of-order /files responses after session or path thrash. */
  const loadGen = useRef(0)

  const flashStatus = useCallback((msg: string, ms = 2800) => {
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
  }, [])

  useEffect(() => {
    return () => {
      if (statusTimer.current != null) window.clearTimeout(statusTimer.current)
      loadGen.current += 1 // invalidate in-flight list on unmount
    }
  }, [])

  const load = useCallback(
    async (p: string) => {
      const gen = ++loadGen.current
      setLoading(true)
      setError('')
      setFocusIdx(-1)
      try {
        const q = new URLSearchParams({ path: p })
        if (sessionId) q.set('session_id', sessionId)
        const data = await apiFetch<{
          files: Entry[]
          path: string
          root?: string
          error?: string
        }>(`/files?${q}`)
        if (gen !== loadGen.current) return
        // Dirs first, then name (case-insensitive)
        const list = [...(data.files || [])].sort((a, b) => {
          if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1
          return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
        })
        setFiles(list)
        setPath(data.path || p)
        if (data.root) setRoot(data.root)
        else if (p === '.') setRoot('')
        if (data.error) setError(data.error)
      } catch (e: unknown) {
        if (gen !== loadGen.current) return
        setError(e instanceof Error ? e.message : 'Failed to list files')
        setFiles([])
      } finally {
        if (gen === loadGen.current) setLoading(false)
      }
    },
    [sessionId],
  )

  // Reset whenever the session changes; honor a pending app_control path.
  useEffect(() => {
    const pending = takePendingFilesPath()
    setPath(pending || '.')
    setRoot('')
    setFiles([])
    setError('')
    setStatus('')
    setFilter('')
    setFocusIdx(-1)
    void load(pending || '.')
  }, [load])

  useEffect(() => {
    const onSet = (ev: Event) => {
      const folder = (ev as CustomEvent<{ path?: string }>).detail?.path?.trim()
      if (!folder) return
      takePendingFilesPath()
      setFilter('')
      void load(folder)
    }
    window.addEventListener(FILES_SET_PATH_EVENT, onSet)
    return () => window.removeEventListener(FILES_SET_PATH_EVENT, onSet)
  }, [load])

  const absPath = (rel: string) => absoluteFilePath(root, rel)

  const goUp = () => {
    if (!path || path === '.') return
    void load(parentFilePath(path))
  }

  const openEntry = async (f: Entry) => {
    if (f.is_dir) {
      setFilter('')
      void load(f.path)
      return
    }
    const full = absPath(f.path)
    flashStatus(isTauri() ? `Opening ${f.name}…` : `Copying path for ${f.name}…`, 1500)
    try {
      if (isTauri()) {
        try {
          await tauriInvoke('open_path', { path: full })
          flashStatus(`Opened ${f.name}`)
          return
        } catch {
          throw new Error('open failed')
        }
      }
      await navigator.clipboard.writeText(full)
      flashStatus('Path copied. Paste it into your file manager to open this file; browsers cannot open local files directly.', 7000)
    } catch (e: unknown) {
      flashStatus(e instanceof Error ? e.message : String(e), 4000)
    }
  }

  const copyPath = async (full: string, label = 'Copied path') => {
    try {
      await navigator.clipboard.writeText(full)
      flashStatus(label)
      onAttachPath?.(full)
    } catch {
      flashStatus(full, 5000)
    }
  }

  const q = filter.trim().toLowerCase()
  const visible = q
    ? files.filter((f) => f.name.toLowerCase().includes(q))
    : files

  useEffect(() => {
    setFocusIdx((i) => (visible.length === 0 ? -1 : Math.min(i, visible.length - 1)))
  }, [visible.length])

  const dirCount = visible.filter((f) => f.is_dir).length
  const fileCount = visible.length - dirCount
  const canGoUp = Boolean(path && path !== '.' && parentFilePath(path) !== path.replace(/\\/g, '/'))
  const rootLabel = root
    ? root.replace(/[/\\]+$/, '').split(/[/\\]/).pop() || root
    : 'Project files'

  const onListKeyDown = (e: KeyboardEvent) => {
    if (e.nativeEvent.isComposing || e.target !== e.currentTarget || loading) return
    if (!visible.length) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setFocusIdx((i) => Math.min(visible.length - 1, (i < 0 ? -1 : i) + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setFocusIdx((i) => Math.max(0, (i < 0 ? visible.length : i) - 1))
    } else if (e.key === 'Enter' && focusIdx >= 0 && focusIdx < visible.length) {
      e.preventDefault()
      void openEntry(visible[focusIdx])
    } else if (e.key === 'Backspace' && !filter && canGoUp) {
      e.preventDefault()
      goUp()
    } else if ((e.key === 'c' || e.key === 'C') && (e.ctrlKey || e.metaKey) && focusIdx >= 0) {
      const f = visible[focusIdx]
      if (f && !f.is_dir) {
        e.preventDefault()
        void copyPath(absPath(f.path))
      }
    }
  }

  return (
    <div className="flex flex-col h-full min-h-0 text-xs">
      <div
        className="px-2 py-1.5 border-b shrink-0 flex items-center gap-1"
        style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
      >
        <button
          type="button"
          className="truncate flex-1 min-w-0 text-left rounded px-0.5 py-0.5 hover:opacity-90"
          style={{ color: 'var(--text-muted)' }}
          title={root ? `${root} — click to copy` : 'No project path on this session'}
          onClick={() => {
            if (root) void copyPath(root, 'Copied project root')
          }}
        >
          <span style={{ color: 'var(--text-secondary)' }}>{rootLabel}</span>
        </button>
        {loading && (
          <span className="shrink-0 text-[10px] tabular-nums" style={{ color: 'var(--accent)' }}>
            …
          </span>
        )}
      </div>
      <div className="px-2 py-1 flex gap-1 border-b shrink-0" style={{ borderColor: 'var(--border)' }}>
        <button
          type="button"
          className="px-1.5 py-0.5 rounded shrink-0 disabled:opacity-40"
          style={{ background: 'var(--bg-primary)', color: 'var(--text-secondary)' }}
          onClick={goUp}
          disabled={!canGoUp || loading}
          title="Parent folder (Backspace)"
          aria-label="Parent folder"
        >
          ↑
        </button>
        <input
          value={path}
          onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => {
            if (e.nativeEvent.isComposing) return
            if (e.key === 'Enter') void load(path || '.')
            if (e.key === 'Escape') e.currentTarget.blur()
          }}
          onFocus={(e) => e.currentTarget.select()}
          className="flex-1 min-w-0 rounded px-1 py-0.5 outline-none font-mono"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
            fontSize: 11,
          }}
          spellCheck={false}
          aria-label="Folder path"
          title={path}
        />
        <button
          type="button"
          className="px-1.5 py-0.5 rounded shrink-0 disabled:opacity-40"
          style={{ background: 'var(--bg-primary)', color: 'var(--text-secondary)' }}
          onClick={() => void load(path || '.')}
          disabled={loading}
          title="Refresh"
          aria-label="Refresh"
        >
          ↻
        </button>
      </div>
      <div className="px-2 py-1 border-b shrink-0" style={{ borderColor: 'var(--border)' }}>
        <input
          value={filter}
          onChange={(e) => {
            setFilter(e.target.value)
            setFocusIdx(0)
          }}
          onKeyDown={(e) => {
            if (e.nativeEvent.isComposing) return
            if (e.key === 'Escape' && filter) {
              e.preventDefault()
              setFilter('')
            } else if (e.key === 'ArrowDown' || e.key === 'Enter') {
              e.preventDefault()
              setFocusIdx(0)
              // Hand off focus into the list
              listRef.current?.focus()
              if (e.key === 'Enter' && visible[0]) void openEntry(visible[0])
            }
          }}
          className="w-full rounded px-1.5 py-0.5 outline-none"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
            fontSize: 11,
          }}
          placeholder="Filter in folder…"
          aria-label="Filter files"
          spellCheck={false}
        />
      </div>
      {status ? (
        <div
          className="px-2 py-1 shrink-0 break-words"
          style={{ color: 'var(--text-muted)' }}
          role="status"
          title={status}
        >
          {status}
        </div>
      ) : null}
      <div
        ref={listRef}
        className="flex-1 min-h-0 overflow-y-auto py-1 outline-none"
        tabIndex={0}
        role="listbox"
        aria-label="Files"
        aria-busy={loading}
        aria-activedescendant={focusIdx >= 0 ? `${listId}-${focusIdx}` : undefined}
        onKeyDown={onListKeyDown}
      >
        {loading && files.length === 0 && (
          <div className="px-2 py-2" style={{ color: 'var(--text-muted)' }}>
            Loading…
          </div>
        )}
        {error && (
          <EmptyState
            compact
            tone="error"
            title="Could not list files"
            description={error}
            actionLabel="Retry"
            onAction={() => void load(path || '.')}
          />
        )}
        {!loading && !error && files.length === 0 && (
          <EmptyState
            compact
            title="No files here"
            description={
              root
                ? 'This folder is empty.'
                : 'Attach a project to this session to browse files.'
            }
          />
        )}
        {!loading && !error && files.length > 0 && visible.length === 0 && (
          <div className="px-2 py-3 text-center" style={{ color: 'var(--text-muted)' }}>
            No matches for “{filter}”
          </div>
        )}
        {!error &&
          visible.map((f, i) => (
            <div
              key={f.path}
              id={`${listId}-${i}`}
              className={`files-row flex items-center gap-0.5 px-1 group${
                i === focusIdx ? ' is-focused' : ''
              }`}
              role="option"
              aria-selected={i === focusIdx}
              draggable={!f.is_dir}
              onMouseEnter={() => setFocusIdx(i)}
              onDragStart={(e) => {
                if (f.is_dir) return
                const full = absPath(f.path)
                e.dataTransfer.setData('text/plain', full)
                e.dataTransfer.setData('text/uri-list', `file:///${full.replace(/\\/g, '/')}`)
                e.dataTransfer.effectAllowed = 'copy'
              }}
            >
              <button
                type="button"
                className="flex-1 min-w-0 text-left px-1 py-1 truncate"
                style={{
                  color: f.is_dir ? 'var(--accent)' : 'var(--text-secondary)',
                  background: 'transparent',
                }}
                onClick={() => {
                  setFocusIdx(i)
                  void openEntry(f)
                }}
                title={absPath(f.path)}
              >
                <span className="inline-block w-3 opacity-70" aria-hidden>
                  {f.is_dir ? '▸' : '·'}
                </span>
                {f.name}
              </button>
              <button
                type="button"
                className="px-1 py-0.5 opacity-0 group-hover:opacity-100 focus:opacity-100 text-[10px] rounded"
                style={{ color: 'var(--text-muted)' }}
                title="Copy full path"
                aria-label={`Copy path for ${f.name}`}
                onClick={() => void copyPath(absPath(f.path))}
              >
                path
              </button>
            </div>
          ))}
      </div>
      <div
        className="px-2 py-1 text-[10px] border-t shrink-0 flex items-center gap-2"
        style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
      >
        <span className="truncate flex-1">
          {isTauri() ? '↑↓ navigate · Enter open' : '↑↓ navigate · Enter copy file path'}
        </span>
        {!loading && !error && visible.length > 0 && (
          <span className="shrink-0 tabular-nums" title="Folders / files">
            {dirCount > 0 ? `${dirCount}d` : ''}
            {dirCount > 0 && fileCount > 0 ? ' · ' : ''}
            {fileCount > 0 ? `${fileCount}f` : ''}
            {q && files.length !== visible.length ? ` / ${files.length}` : ''}
          </span>
        )}
      </div>
    </div>
  )
}
