import { useEffect, useState } from 'react'
import { apiFetch } from '../../api/client'
import { isTauri, tauriInvoke } from '../../api/tauri'

/**
 * Terminal slide: open **PowerShell** in the project cwd (primary on Windows).
 */
export function TerminalSlide({ sessionId }: { sessionId?: string | null }) {
  const [cwd, setCwd] = useState('')
  const [log, setLog] = useState(
    'Remedy Terminal\nOpens PowerShell in your project folder (pwsh → Windows PowerShell → WT → cmd).\n',
  )
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const q = sessionId
          ? `?session_id=${encodeURIComponent(sessionId)}`
          : ''
        const data = await apiFetch<{ project_path?: string }>(`/workspace${q}`)
        if (!cancelled && data.project_path) setCwd(data.project_path)
      } catch {
        /* keep empty → Rust uses process cwd */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [sessionId])

  const openExternal = async () => {
    setBusy(true)
    const target = cwd.trim()
    setLog((l) => l + `\n> open terminal${target ? ` @ ${target}` : ''}…\n`)
    try {
      if (!isTauri()) {
        setLog((l) => l + 'Desktop app required for host terminal.\n')
        return
      }
      const msg = await tauriInvoke<string>('open_terminal', {
        cwd: target || null,
      })
      setLog((l) => l + `${msg}\n`)
    } catch (e: unknown) {
      setLog((l) => l + `Error: ${e instanceof Error ? e.message : String(e)}\n`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col h-full min-h-0 text-xs font-mono">
      <div
        className="px-2 py-1.5 border-b flex flex-col gap-1 shrink-0"
        style={{ borderColor: 'var(--border)' }}
      >
        <div className="flex gap-1 items-center">
          <button
            type="button"
            className="px-2 py-0.5 rounded text-[11px] font-sans font-medium"
            style={{
              background: 'var(--accent)',
              color: '#fff',
              opacity: busy ? 0.7 : 1,
            }}
            disabled={busy}
            onClick={() => void openExternal()}
          >
            {busy ? 'Opening…' : 'Open PowerShell here'}
          </button>
          <button
            type="button"
            className="px-2 py-0.5 rounded text-[11px] font-sans"
            style={{
              background: 'var(--bg-primary)',
              border: '1px solid var(--border)',
              color: 'var(--text-secondary)',
            }}
            onClick={() =>
              setLog(
                'Remedy Terminal\nOpens PowerShell in your project folder (pwsh → Windows PowerShell → WT → cmd).\n',
              )
            }
          >
            Clear log
          </button>
        </div>
        <input
          value={cwd}
          onChange={(e) => setCwd(e.target.value)}
          className="w-full rounded px-1.5 py-1 font-sans outline-none"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
            fontSize: 11,
          }}
          placeholder="Working directory (project path)"
          spellCheck={false}
        />
      </div>
      <pre
        className="flex-1 min-h-0 overflow-auto p-2 m-0 whitespace-pre-wrap"
        style={{
          background: 'var(--bg-primary)',
          color: 'var(--text-secondary)',
          fontSize: 11,
          lineHeight: 1.45,
        }}
      >
        {log}
      </pre>
    </div>
  )
}
