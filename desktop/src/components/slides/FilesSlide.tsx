import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../../api/client'
import { isTauri, tauriInvoke } from '../../api/tauri'
import { EmptyState } from '../EmptyState'

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

  const load = useCallback(
    async (p: string) => {
      setLoading(true)
      setError('')
      try {
        const q = new URLSearchParams({ path: p })
        if (sessionId) q.set('session_id', sessionId)
        const data = await apiFetch<{
          files: Entry[]
          path: string
          root?: string
          error?: string
        }>(`/files?${q}`)
        setFiles(data.files || [])
        setPath(data.path || p)
        if (data.root) setRoot(data.root)
        if (data.error) setError(data.error)
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Failed to list files')
        setFiles([])
      } finally {
        setLoading(false)
      }
    },
    [sessionId],
  )

  useEffect(() => {
    void load('.')
  }, [load])

  const absPath = (rel: string) => {
    if (!root) return rel
    if (/^[A-Za-z]:[\\/]/.test(rel) || rel.startsWith('\\\\')) return rel
    const sep = root.includes('\\') ? '\\' : '/'
    const base = root.replace(/[/\\]+$/, '')
    const tail = rel.replace(/^\.?[/\\]/, '')
    return `${base}${sep}${tail.replace(/\//g, sep)}`
  }

  const goUp = () => {
    if (!path || path === '.') return
    const parts = path.replace(/\\/g, '/').split('/').filter(Boolean)
    parts.pop()
    void load(parts.length ? parts.join('/') : '.')
  }

  const openEntry = async (f: Entry) => {
    if (f.is_dir) {
      void load(f.path)
      return
    }
    const full = absPath(f.path)
    setStatus(`Opening ${f.name}…`)
    try {
      if (isTauri()) {
        // Prefer dedicated open_path; fall back to external open via shell
        try {
          await tauriInvoke('open_path', { path: full })
          setStatus(`Opened ${f.name}`)
          return
        } catch {
          await tauriInvoke('open_external_url', {
            url: `file:///${full.replace(/\\/g, '/')}`,
            preferFirefox: false,
          }).catch(() => {
            throw new Error('open failed')
          })
          setStatus(`Opened ${f.name}`)
          return
        }
      }
      window.open(`file:///${full.replace(/\\/g, '/')}`, '_blank')
      setStatus(`Opened ${f.name}`)
    } catch (e: unknown) {
      setStatus(e instanceof Error ? e.message : String(e))
    }
  }

  const copyPath = async (f: Entry) => {
    const full = absPath(f.path)
    try {
      await navigator.clipboard.writeText(full)
      setStatus(`Copied path: ${full}`)
      onAttachPath?.(full)
    } catch {
      setStatus(full)
    }
  }

  return (
    <div className="flex flex-col h-full min-h-0 text-xs">
      <div
        className="px-2 py-1.5 border-b shrink-0 truncate"
        style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
        title={root}
      >
        {root || 'Project files'}
      </div>
      <div className="px-2 py-1 flex gap-1 border-b shrink-0" style={{ borderColor: 'var(--border)' }}>
        <button
          type="button"
          className="px-1.5 py-0.5 rounded"
          style={{ background: 'var(--bg-primary)', color: 'var(--text-secondary)' }}
          onClick={goUp}
          disabled={path === '.'}
        >
          ↑ Up
        </button>
        <input
          value={path}
          onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void load(path || '.')
          }}
          className="flex-1 min-w-0 rounded px-1 py-0.5 outline-none font-mono"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
            fontSize: 11,
          }}
          spellCheck={false}
        />
        <button
          type="button"
          className="px-1.5 py-0.5 rounded"
          style={{ background: 'var(--bg-primary)', color: 'var(--text-secondary)' }}
          onClick={() => void load(path || '.')}
        >
          ↻
        </button>
      </div>
      {status && (
        <div className="px-2 py-0.5 truncate" style={{ color: 'var(--text-muted)' }}>
          {status}
        </div>
      )}
      <div className="flex-1 min-h-0 overflow-y-auto py-1">
        {loading && (
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
                ? 'This folder is empty, or the project path is not set.'
                : 'Attach a project to this session to browse files.'
            }
          />
        )}
        {!loading &&
          files.map((f) => (
            <div
              key={f.path}
              className="flex items-center gap-0.5 px-1 group"
              draggable={!f.is_dir}
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
                onClick={() => void openEntry(f)}
                onDoubleClick={() => void openEntry(f)}
                title={f.path}
              >
                {f.is_dir ? '▸ ' : '  '}
                {f.name}
              </button>
              {!f.is_dir && (
                <button
                  type="button"
                  className="px-1 py-0.5 opacity-0 group-hover:opacity-100 text-[10px]"
                  style={{ color: 'var(--text-muted)' }}
                  title="Copy full path (paste into chat)"
                  onClick={() => void copyPath(f)}
                >
                  path
                </button>
              )}
            </div>
          ))}
        {!loading && !files.length && !error && (
          <div className="px-2 py-4 text-center" style={{ color: 'var(--text-muted)' }}>
            Empty folder
          </div>
        )}
      </div>
      <div
        className="px-2 py-1 text-[10px] border-t shrink-0"
        style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
      >
        Double-click open · drag file into chat · follows session project
      </div>
    </div>
  )
}
