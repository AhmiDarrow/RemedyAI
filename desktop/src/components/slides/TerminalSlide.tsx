import { useCallback, useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { apiFetch } from '../../api/client'
import { isTauri, tauriInvoke } from '../../api/tauri'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'

/**
 * In-app PowerShell via ConPTY + xterm. Auto-starts when the slide mounts.
 */
export function TerminalSlide({ sessionId }: { sessionId?: string | null }) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const termRef = useRef<Terminal | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const ptyIdRef = useRef<string | null>(null)
  const [status, setStatus] = useState('Starting PowerShell…')
  const [cwd, setCwd] = useState('')

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
        const data = await apiFetch<{ project_path?: string }>(`/workspace${q}`)
        if (!cancelled && data.project_path) setCwd(data.project_path)
      } catch {
        /* empty cwd → process default */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [sessionId])

  const startPty = useCallback(async (term: Terminal, fit: FitAddon, workdir: string) => {
    if (!isTauri()) {
      term.writeln('\r\nDesktop app required for in-app PowerShell.\r\n')
      setStatus('Not available outside desktop')
      return
    }
    try {
      fit.fit()
      const cols = term.cols
      const rows = term.rows
      const id = await tauriInvoke<string>('pty_open', {
        cwd: workdir.trim() || null,
        cols,
        rows,
      })
      ptyIdRef.current = id
      setStatus(workdir ? `PowerShell · ${workdir}` : 'PowerShell')
      term.focus()
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e)
      term.writeln(`\r\nFailed to start PowerShell: ${msg}\r\n`)
      setStatus(`Error: ${msg}`)
    }
  }, [])

  useEffect(() => {
    const el = hostRef.current
    if (!el) return

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: 'Consolas, "Cascadia Mono", "Courier New", monospace',
      theme: {
        background: '#0b0f14',
        foreground: '#e6edf3',
        cursor: '#a78bfa',
        selectionBackground: '#3b3266',
      },
      allowProposedApi: true,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(el)
    fit.fit()
    termRef.current = term
    fitRef.current = fit

    const onData = term.onData((data) => {
      const id = ptyIdRef.current
      if (!id || !isTauri()) return
      void tauriInvoke('pty_write', { id, data }).catch(() => {})
    })

    let unData: UnlistenFn | undefined
    let unExit: UnlistenFn | undefined
    let cancelled = false

    void (async () => {
      if (!isTauri()) {
        term.writeln('Desktop app required for in-app PowerShell.')
        setStatus('Web UI — use desktop for terminal')
        return
      }
      try {
        unData = await listen<{ id: string; data: string }>('pty-data', (ev) => {
          if (ev.payload.id === ptyIdRef.current) {
            term.write(ev.payload.data)
          }
        })
        unExit = await listen<{ id: string }>('pty-exit', (ev) => {
          if (ev.payload.id === ptyIdRef.current) {
            term.writeln('\r\n[shell exited]\r\n')
            ptyIdRef.current = null
            setStatus('Shell exited — Restart to open again')
          }
        })
      } catch {
        /* event API unavailable */
      }
      if (!cancelled) {
        await startPty(term, fit, cwd)
      }
    })()

    const ro = new ResizeObserver(() => {
      try {
        fit.fit()
        const id = ptyIdRef.current
        if (id && isTauri()) {
          void tauriInvoke('pty_resize', {
            id,
            cols: term.cols,
            rows: term.rows,
          }).catch(() => {})
        }
      } catch {
        /* */
      }
    })
    ro.observe(el)

    return () => {
      cancelled = true
      onData.dispose()
      ro.disconnect()
      void unData?.()
      void unExit?.()
      const id = ptyIdRef.current
      if (id && isTauri()) {
        void tauriInvoke('pty_close', { id }).catch(() => {})
      }
      ptyIdRef.current = null
      term.dispose()
      termRef.current = null
      fitRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount once; cwd restart via button
  }, [])

  // Restart when project cwd becomes known after first empty start
  const restart = async () => {
    const term = termRef.current
    const fit = fitRef.current
    if (!term || !fit) return
    const prev = ptyIdRef.current
    if (prev && isTauri()) {
      await tauriInvoke('pty_close', { id: prev }).catch(() => {})
      ptyIdRef.current = null
    }
    term.clear()
    term.writeln('Restarting PowerShell…\r\n')
    await startPty(term, fit, cwd)
  }

  return (
    <div className="flex flex-col h-full min-h-0 text-xs">
      <div
        className="px-2 py-1 border-b flex gap-1 items-center shrink-0"
        style={{ borderColor: 'var(--border)', color: 'var(--text-muted)' }}
      >
        <span className="truncate flex-1 font-mono" title={cwd || undefined}>
          {status}
        </span>
        <button
          type="button"
          className="px-2 py-0.5 rounded font-sans"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-secondary)',
          }}
          onClick={() => void restart()}
        >
          Restart
        </button>
      </div>
      <div ref={hostRef} className="flex-1 min-h-0 w-full" style={{ background: '#0b0f14' }} />
    </div>
  )
}
