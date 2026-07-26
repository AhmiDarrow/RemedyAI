import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from '../../api/client'

type Entry = { name: string; path: string; is_dir: boolean }

export function FilesSlide({ sessionId }: { sessionId: string | null }) {
  const [root, setRoot] = useState('')
  const [path, setPath] = useState('.')
  const [files, setFiles] = useState<Entry[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

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

  const goUp = () => {
    if (!path || path === '.') return
    const parts = path.replace(/\\/g, '/').split('/').filter(Boolean)
    parts.pop()
    void load(parts.length ? parts.join('/') : '.')
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
        <span className="truncate flex-1 py-0.5" style={{ color: 'var(--text-muted)' }}>
          {path}
        </span>
        <button
          type="button"
          className="px-1.5 py-0.5 rounded"
          style={{ background: 'var(--bg-primary)', color: 'var(--text-secondary)' }}
          onClick={() => void load(path)}
        >
          ↻
        </button>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto py-1">
        {loading && (
          <div className="px-2 py-2" style={{ color: 'var(--text-muted)' }}>
            Loading…
          </div>
        )}
        {error && (
          <div className="px-2 py-2" style={{ color: 'var(--warning)' }}>
            {error}
          </div>
        )}
        {!loading &&
          files.map((f) => (
            <button
              key={f.path}
              type="button"
              className="w-full text-left px-2 py-1 truncate"
              style={{
                color: f.is_dir ? 'var(--accent)' : 'var(--text-secondary)',
                background: 'transparent',
              }}
              onClick={() => {
                if (f.is_dir) void load(f.path)
              }}
              title={f.path}
            >
              {f.is_dir ? '▸ ' : '  '}
              {f.name}
            </button>
          ))}
        {!loading && !files.length && !error && (
          <div className="px-2 py-4 text-center" style={{ color: 'var(--text-muted)' }}>
            Empty folder
          </div>
        )}
      </div>
    </div>
  )
}
