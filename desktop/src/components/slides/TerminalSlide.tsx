import { useCallback, useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { apiFetch } from '../../api/client'
import { isTauri, tauriInvoke } from '../../api/tauri'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'

/**
 * In-app PowerShell via ConPTY + xterm.
 * Waits until the host has a real size before spawning (avoids 0×0 PTY fails).
 */
export function TerminalSlide({ sessionId }: { sessionId?: string | null }) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  const termRef = useRef<Terminal | null>(null)
  const fitRef = useRef<FitAddon | null>(null)
  const ptyIdRef = useRef<string | null>(null)
  const cwdRef = useRef('')
  const [status, setStatus] = useState('Starting PowerShell…')
  const [cwd, setCwd] = useState('')

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
        const data = await apiFetch<{ project_path?: string }>(`/workspace${q}`)
        if (!cancelled && data.project_path) {
          setCwd(data.project_path)
          cwdRef.current = data.project_path
        }
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
      // Close previous session if any
      const prev = ptyIdRef.current
      if (prev) {
        await tauriInvoke('pty_close', { id: prev }).catch(() => {})
        ptyIdRef.current = null
      }
      fit.fit()
      let cols = term.cols
      let rows = term.rows
      if (cols < 20 || rows < 5) {
        // Host still measuring — retry shortly
        setStatus('Waiting for panel size…')
        await new Promise((r) => window.setTimeout(r, 120))
        fit.fit()
        cols = Math.max(term.cols, 80)
        rows = Math.max(term.rows, 24)
      }
      setStatus('Launching PowerShell…')
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
      cursorStyle: 'block',
      cursorWidth: 2,
      fontSize: 13,
      fontFamily: 'Consolas, "Cascadia Mono", "Courier New", monospace',
      theme: {
        background: '#0b0f14',
        foreground: '#e6edf3',
        cursor: '#c4b5fd',
        cursorAccent: '#0b0f14',
        selectionBackground: '#3b3266',
      },
      allowProposedApi: true,
      rightClickSelectsWord: false,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(el)
    // Defer first fit until layout has painted
    requestAnimationFrame(() => {
      try {
        fit.fit()
      } catch {
        /* */
      }
    })
    term.focus()
    termRef.current = term
    fitRef.current = fit

    const focusTerm = () => {
      try {
        term.focus()
      } catch {
        /* */
      }
    }
    el.addEventListener('mousedown', focusTerm)
    el.addEventListener('click', focusTerm)

    const writePty = (data: string) => {
      const id = ptyIdRef.current
      if (!id || !isTauri()) return
      void tauriInvoke('pty_write', { id, data }).catch(() => {})
    }

    const copySelection = async () => {
      const sel = term.getSelection()
      if (!sel) return false
      try {
        await navigator.clipboard.writeText(sel)
        return true
      } catch {
        return false
      }
    }

    const pasteClipboard = async () => {
      try {
        const text = await navigator.clipboard.readText()
        if (text) writePty(text)
      } catch {
        /* clipboard permission denied */
      }
    }

    term.attachCustomKeyEventHandler((ev) => {
      if (ev.type !== 'keydown') return true
      // Never swallow Escape — PopoutOverlay uses it to exit fullscreen / close
      if (ev.key === 'Escape') return true
      const key = ev.key.toLowerCase()
      const ctrl = ev.ctrlKey || ev.metaKey
      const shift = ev.shiftKey
      if (ctrl && shift && key === 'c') {
        void copySelection()
        return false
      }
      if (ctrl && shift && key === 'v') {
        void pasteClipboard()
        return false
      }
      if (ctrl && !shift && key === 'c') {
        if (term.hasSelection()) {
          void copySelection()
          return false
        }
        return true
      }
      if (ctrl && !shift && key === 'v') {
        void pasteClipboard()
        return false
      }
      if (ctrl && !shift && key === 'a') {
        term.selectAll()
        return false
      }
      return true
    })

    const onContextMenu = (e: MouseEvent) => {
      e.preventDefault()
      if (term.hasSelection()) {
        void copySelection()
      } else {
        void pasteClipboard()
      }
    }
    el.addEventListener('contextmenu', onContextMenu)

    const onData = term.onData((data) => {
      writePty(data)
    })

    let unData: UnlistenFn | undefined
    let unExit: UnlistenFn | undefined
    let cancelled = false
    let started = false

    const tryStart = async () => {
      if (cancelled || started || !isTauri()) return
      // Need a non-trivial host size
      if (el.clientWidth < 40 || el.clientHeight < 40) return
      started = true
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
      } catch (e) {
        term.writeln(`\r\nEvent listen failed: ${e}\r\n`)
      }
      if (!cancelled) {
        await startPty(term, fit, cwdRef.current)
      }
    }

    void (async () => {
      if (!isTauri()) {
        term.writeln('Desktop app required for in-app PowerShell.')
        setStatus('Web UI — use desktop for terminal')
        return
      }
      // Retry until layout has size (panel open animation / rail expand)
      for (let i = 0; i < 20 && !cancelled && !started; i++) {
        await tryStart()
        if (!started) await new Promise((r) => window.setTimeout(r, 100))
      }
      if (!started && !cancelled) {
        // Force start with defaults
        started = true
        await startPty(term, fit, cwdRef.current)
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
        } else if (!started && !cancelled) {
          void tryStart()
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
      el.removeEventListener('mousedown', focusTerm)
      el.removeEventListener('click', focusTerm)
      el.removeEventListener('contextmenu', onContextMenu)
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
  }, [startPty])

  const restart = async () => {
    const term = termRef.current
    const fit = fitRef.current
    if (!term || !fit) return
    term.clear()
    term.writeln('Restarting PowerShell…\r\n')
    await startPty(term, fit, cwd || cwdRef.current)
  }

  return (
    <div className="flex flex-col h-full min-h-0 max-h-full overflow-hidden text-xs">
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
      <div
        ref={hostRef}
        className="flex-1 min-h-0 w-full max-h-full cursor-text overflow-hidden"
        style={{ background: '#0b0f14', position: 'relative' }}
        title="Click to focus · Ctrl+Shift+C/V copy/paste · right-click paste · Esc exits fullscreen"
        onMouseDown={() => termRef.current?.focus()}
      />
    </div>
  )
}
